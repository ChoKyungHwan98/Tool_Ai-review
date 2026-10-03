"""현재 리뷰 분석 — 게임별 주제 · 재미 종류 · 불만 심층.

모든 게임에 같은 주제를 강제하지 않고 다음 세 단계로 분석한다.

  A. 주제 찾기   리뷰 일부(최대 150건)로 이 게임에서 반복되는 주제 목록을 한 번 만든다.
  B. 전체 분류   모든 리뷰를 짧게 분류한다. 언급한 주제만 답하게 해 출력 토큰을 줄인다.
  C. 불만 심층   불만·섞임 리뷰만 문제 / 원인 / 유저 제안으로 나눈다.

내용이 없는 짧은 리뷰는 AI에 보내지 않고, 짧은 불만은 선별해 분류한다.

결과 파일
  themes_v3.json       A의 주제 목록
  analysis_v3.jsonl    B의 리뷰별 분류 (한 줄에 한 리뷰)
  complaints_v3.jsonl  C의 불만 분해
  usage_v3.json        단계별 실제 토큰 사용량
  analysis_v3.csv      품질 점검·검증·리뷰 탐색이 읽는 분석 표
"""

import asyncio
import csv
import json
import os
import random
import re
import sys

import httpx
from budget_control import BudgetExceeded, current as current_budget
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
from config import cfg
import progress
import openrouter_limits as limits

API_KEY = cfg.OPENROUTER_API_KEY
URL = cfg.OPENROUTER_URL

ASPECTS = ["graphics", "gameplay", "story", "performance", "value"]
AREA_TOPIC = {"graphics": "content", "gameplay": "content", "story": "content",
              "performance": "technical", "value": "value"}

# MDA 프레임워크의 8가지 재미를 한국어로 옮긴 것.
FUN_TYPES = {
    "감각": "보고 듣는 즐거움",
    "판타지": "세계관과 역할에 몰입",
    "이야기": "서사와 캐릭터",
    "도전": "어려움을 이겨내는 성취",
    "함께": "다른 유저와 협동·경쟁",
    "발견": "탐험과 새로운 것 찾기",
    "표현": "꾸미기·건축·창작",
    "몰두": "시간 가는 줄 모르는 반복",
}

THEME_SAMPLE = 150       # A에서 읽을 리뷰 수
BATCH_B = 15             # B 한 번에 담을 리뷰 수. 고정 지시문 비용을 더 많은 리뷰가 나눠 낸다
BATCH_C = 5              # 심층 답변이 길어 JSON이 잘리지 않도록 작은 묶음 사용
CONCURRENCY = 3
CLIP_B = 480             # B에 보낼 본문 최대 글자 (앞부분 + 끝부분)
CLIP_C = 720             # C에 보낼 본문 최대 글자 (앞부분 + 끝부분)
MIN_LEN_C = 15           # 이보다 짧은 불만은 심층 분석할 내용이 없다
SHORT_COMPLAINT_CUES = ("렉", "버그", "오류", "튕", "끊", "불편", "환불", "노잼",
                        "lag", "bug", "crash", "error", "refund")

SENT_MAP = {"P": "POSITIVE", "N": "NEGATIVE", "M": "MIXED", "U": "NEUTRAL"}


class FatalApiError(RuntimeError):
    """재시도해도 소용없는 오류 (모델 없음, 키 무효, 잔액 부족). 즉시 멈춘다."""


def folder():
    return os.path.dirname(cfg.ANALYSIS_CSV)


def path(name):
    return os.path.join(folder(), name)


USAGE = {s: {"calls": 0, "input": 0, "output": 0} for s in ("A", "B", "C")}


def clean_json(text):
    text = text.strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\[[\s\S]*\])"):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return text


async def ask(client, stage, system, user, max_tokens):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    body = {
        "model": cfg.MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if getattr(cfg, "MODEL_JSON_MODE", False):
        body["response_format"] = {"type": "json_object"}
    last = None
    errors = rate_waits = 0
    while True:
        guard = current_budget()
        reservation = guard.reserve(messages, max_tokens) if guard else None
        usage_recorded = False
        try:
            await limits.await_turn()
            r = await client.post(URL, headers=headers, json=body, timeout=120.0)
            if r.status_code == 429:
                if limits.daily_limit_hit(r.text):
                    raise FatalApiError(limits.DAILY_MESSAGE)
                rate_waits += 1
                if rate_waits > 3:   # 실패한 429도 하루 한도에 들어가므로 오래 버티지 않는다
                    raise RuntimeError(f"요청이 너무 많다는 응답(429)이 계속됩니다: {r.text[:120]}")
                await asyncio.sleep(limits.retry_after(r, 10.0 if limits.is_free() else 5.0))
                continue
            if r.status_code in (400, 404) and body.pop("response_format", None):
                raise ValueError("선택한 모델의 JSON 모드가 거부되어 일반 형식으로 재시도합니다")
            if r.status_code in (400, 401, 402, 403, 404):
                raise FatalApiError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                error = data["error"]
                if isinstance(error, dict) and error.get("code") == 429:   # 본문에 담겨 오는 한도 초과
                    if limits.daily_limit_hit(str(error)):
                        raise FatalApiError(limits.DAILY_MESSAGE)
                    raise RuntimeError(f"요청 한도 초과: {str(error)[:120]}")
                raise FatalApiError(str(error)[:200])
            usage = data.get("usage") or {}
            if guard:
                guard.finish(reservation, usage)
                usage_recorded = True
            USAGE[stage]["calls"] += 1
            USAGE[stage]["input"] += int(usage.get("prompt_tokens") or 0)
            USAGE[stage]["output"] += int(usage.get("completion_tokens") or 0)
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            parsed = json.loads(clean_json(content or ""))
            if isinstance(parsed, dict):
                parsed = parsed.get("items")
            if not isinstance(parsed, list):
                raise ValueError("응답이 배열이 아닙니다")
            return parsed
        except (FatalApiError, BudgetExceeded):
            raise
        except Exception as e:  # 일시적 오류만 한 번 더 시도한다
            last = e
            errors += 1
            if errors >= 2:
                raise last
            await asyncio.sleep(2.0)
        finally:
            if guard and not usage_recorded:
                guard.finish(reservation)


def compact(items):
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def clip(text, limit):
    """긴 리뷰는 앞부분과 끝부분만 보낸다. "재밌는데 … 근데 렉이 심함"처럼 불만이 끝에 오는 경우가 많다."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    tail = limit // 4
    return text[: limit - tail - 1] + "…" + text[-tail:]


def same_text_groups(rows):
    """본문이 같은 리뷰는 한 번만 AI에 보낸다. 대표 리뷰 목록과 {대표 번호: [같은 본문 리뷰]}를 돌려준다."""
    first, copies = {}, {}
    for r in rows:
        key = " ".join((r.get("content") or "").split()).casefold()
        if key in first:
            copies.setdefault(first[key]["recommendationid"], []).append(r)
        else:
            first[key] = r
    return list(first.values()), copies


def load_reviews():
    with open(cfg.REVIEWS_CSV, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    candidates = [r for r in rows if should_classify(r)]
    return rows, candidates


def should_classify(row):
    """두 글자 이상이면 추천·비추천을 가리지 않고 모두 분류한다.

    예전에는 짧은 글 중 비추천이거나 불만 낱말이 있는 글만 남겼다. 그러면 짧은 추천 글만 빠져
    분석 묶음이 불만 쪽으로 기운다(팰월드: 수집 4.8% → 분석 7.5%)."""
    return len((row.get("content") or "").strip()) >= cfg.MIN_REVIEW_LEN


def hours(row):
    value = row.get("playtime_at_review_min")
    try:
        minutes = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    return round(minutes / 60.0, 1) if minutes is not None and minutes >= 0 else None


def read_jsonl(p):
    out = {}
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    item = json.loads(line)
                    out[str(item["id"])] = item
    return out


# ── A. 주제 찾기 ──────────────────────────────────────────────────────

SYSTEM_A = "게임 기획자를 돕는 리뷰 분석가입니다. JSON 객체만 출력합니다."

USER_A = """아래는 한 게임의 스팀 리뷰 {n}건입니다. 여러 언어가 섞일 수 있습니다. 주제 이름과 설명은 한국어로 쓰세요.
{reviews}

유저들이 반복해서 말하는 게임의 구성 요소를 8~14개로 정리하세요.
- name: 이 게임에 맞는 구체적인 대상 이름, 한국어 2~6자.
  좋은 예: "저장", "조작", "서버 동기화", "최적화", "인벤토리", "튜토리얼"
  나쁜 예: "게임플레이"(너무 넓음), "멋진 캐릭터"(평가가 들어감), "성취감"·"시간 순삭"(느낌이지 대상이 아님),
          "RPG"·"액션"(장르 이름), "최적화"와 "렉"을 따로(같은 것은 하나로)
- 좋다·나쁘다는 넣지 마세요. 같은 주제로 칭찬과 불만을 모두 담을 수 있어야 합니다.
- desc: 15자 이내 설명
- area: graphics|gameplay|story|performance|value 중 하나
- 비슷한 주제는 하나로 합치세요.
{{"items":[{{"name":"","desc":"","area":""}}]}}"""


async def find_themes(client, long_rows):
    progress.report("topics", force=True)
    p = path("themes_v3.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            themes = json.load(f)
        print(f"[A] 기존 주제 {len(themes)}개 사용")
        return themes

    rng = random.Random(42)
    # 비추천도 추천과 같이 무작위로 고른다. 가장 긴 글만 고르면 길게 쓰는 사람의 주제만 목록에 오른다.
    negatives = [r for r in long_rows if r["voted_up"] in ("0", "False", "false") and len(r["content"]) >= 20]
    rng.shuffle(negatives)
    negatives = negatives[:60]
    positives = [r for r in long_rows if r["voted_up"] not in ("0", "False", "false") and len(r["content"]) >= 20]
    rng.shuffle(positives)
    # Keep both recommendation groups represented, and fit the chosen model's context.
    interleaved = []
    for index in range(max(len(negatives), len(positives))):
        if index < len(negatives):
            interleaved.append((0, negatives[index]))
        if index < len(positives):
            interleaved.append((1, positives[index]))
    # Use the same evidence allowance across free and paid models for comparable results.
    byte_limit = min(27000, max(12000, int(getattr(cfg, "MODEL_CONTEXT_LENGTH", 32768)) - 5000))
    payload = []
    for up, row in interleaved:
        if len(payload) >= THEME_SAMPLE:
            break
        candidate = {"up": up, "t": row["content"][:300]}
        if len(compact(payload + [candidate]).encode("utf-8")) > byte_limit:
            break
        payload.append(candidate)

    print(f"[A] 리뷰 {len(payload)}건으로 주제를 찾습니다")
    result = await ask(client, "A", SYSTEM_A, USER_A.format(n=len(payload), reviews=compact(payload)), 1500)
    themes, seen = [], set()
    for t in result:
        name = str(t.get("name", "")).strip()
        area = t.get("area") if t.get("area") in ASPECTS else "gameplay"
        if name and name not in seen:
            seen.add(name)
            themes.append({"name": name, "desc": str(t.get("desc", ""))[:30], "area": area})
    themes.append({"name": "기타", "desc": "목록에 없는 주제", "area": "gameplay"})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(themes, f, ensure_ascii=False, indent=2)
    print(f"[A] 주제 {len(themes)}개: {', '.join(t['name'] for t in themes)}")
    return themes


# ── B. 전체 분류 ──────────────────────────────────────────────────────

SYSTEM_B = "게임 리뷰 분류기입니다. JSON 객체만 출력합니다. 설명하지 않습니다."

USER_B = """주제: {themes}
재미 종류: {fun}
리뷰 원문은 여러 언어일 수 있습니다. k 요약은 한국어로 쓰세요.

리뷰 (up=추천 여부):
{reviews}

리뷰마다 한 항목씩 {{"items":[...]}} 형식의 JSON 객체로 답하세요.
- s: P 긍정, N 부정, M 섞임, U 판단 불가
- s와 주제별 P/N은 리뷰 문장으로 판단하세요. 추천 여부(up)만으로 감정을 정하지 마세요.
- f: 긍정·섞임 리뷰는 드러난 재미 종류를 1~2개 고르세요 (위 목록에서만). 근거가 전혀 없을 때만 []
- t: 리뷰가 구체적인 대상을 말했을 때만 넣고, 주제별 P 또는 N.
  "재밌다", "갓겜" 같은 막연한 말은 주제가 아닙니다 → []
  구체적인 대상인데 목록에 없을 때만 "기타"
- k: 유저 입장 한 줄, 20자 이내

예시 (주제 이름은 설명용):
"친구랑 같이 집 짓는 게 제일 재밌음. 근데 렉이 심함"
→ {{"id":"1","s":"M","f":["함께","표현"],"t":[["건축","P"],["최적화","N"]],"k":"협동 건축은 좋으나 렉"}}
"캐릭터가 너무 귀엽고 하나씩 모으는 맛이 있다"
→ {{"id":"2","s":"P","f":["감각","발견"],"t":[["캐릭터 디자인","P"],["수집","P"]],"k":"귀여운 캐릭터 수집"}}
"재밌게 잘 놀았습니다"
→ {{"id":"3","s":"P","f":[],"t":[],"k":"만족"}}
"실행하자마자 튕겨서 환불함"
→ {{"id":"4","s":"N","f":[],"t":[["최적화","N"]],"k":"실행 즉시 튕김"}}"""


def clean_tags(raw, area_of):
    """[["주제","P"], ...]만 남긴다. AI가 형식을 틀리게 주는 경우가 있다."""
    tags = []
    for pair in raw or []:
        if (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
                and pair[0] in area_of and pair[1] in ("P", "N")):
            tags.append([pair[0], pair[1]])
    return tags


def analysis_row(src, res, area_of):
    """대시보드와 검증 도구가 읽는 분석 표 한 줄을 추가 호출 없이 만든다."""
    per_area = {a: [] for a in ASPECTS}
    tags = clean_tags(res.get("t"), area_of)
    for pair in tags:
        per_area[area_of[pair[0]]].append(pair)
    row = {
        "recommendationid": src["recommendationid"],
        "voted_up": 1 if src["voted_up"] not in ("0", "False", "false") else 0,
        "playtime_h": f"{hours(src):.1f}" if hours(src) is not None else "",
        "content": src["content"][:500],
        "overall_sentiment": SENT_MAP.get(res.get("s"), "NEUTRAL"),
        "key_phrase": res.get("k", ""),
        # 분류 단계는 신뢰도를 묻지 않는다. 임의의 고정 값을 기록하면 실제 확신도로 오해된다.
        "confidence": "",
        "needs_verification": "",
    }
    for a in ASPECTS:
        pairs = per_area[a]
        if any(p[1] == "N" for p in pairs):
            s = "NEGATIVE"
        elif any(p[1] == "P" for p in pairs):
            s = "POSITIVE"
        else:
            s = "NONE"
        row[f"aspect_{a}_sentiment"] = s
        row[f"aspect_{a}_evidence"] = ", ".join(p[0] for p in pairs)[:120]
    row["keywords"] = "|".join(f"{p[0]}@{AREA_TOPIC[area_of[p[0]]]}" for p in tags)
    return row


ANALYSIS_FIELDS = (["recommendationid", "voted_up", "playtime_h", "content",
              "overall_sentiment", "key_phrase", "confidence", "needs_verification"]
             + [f"aspect_{a}_sentiment" for a in ASPECTS]
             + [f"aspect_{a}_evidence" for a in ASPECTS]
             + ["keywords"])


async def classify(client, long_rows, themes):
    theme_names = [t["name"] for t in themes]
    area_of = {t["name"]: t["area"] for t in themes}
    fun_names = list(FUN_TYPES)
    out_path = path("analysis_v3.jsonl")
    if not os.path.exists(out_path) and os.path.exists(cfg.ANALYSIS_CSV):
        legacy = path("analysis_legacy.csv")
        if os.path.exists(legacy):
            os.remove(legacy)
        os.rename(cfg.ANALYSIS_CSV, legacy)
        print(f"[B] 옛 방식 결과를 {os.path.basename(legacy)}로 보관하고 새로 분석합니다")
    done = read_jsonl(out_path)
    pending = [r for r in long_rows if r["recommendationid"] not in done]
    print(f"[B] 분류 대상 {len(long_rows)}건 / 이미 {len(done)}건 / 남은 {len(pending)}건")
    progress.report("classify", len(done), len(long_rows), force=True)
    if not pending:
        return done
    pending, copies = same_text_groups(pending)
    if copies:
        print(f"[B] 본문이 같은 리뷰 {sum(map(len, copies.values()))}건은 대표 리뷰 결과를 함께 씁니다")

    write_header = not os.path.exists(cfg.ANALYSIS_CSV)
    f_v3 = open(out_path, "a", encoding="utf-8")
    f_csv = open(cfg.ANALYSIS_CSV, "a", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(f_csv, fieldnames=ANALYSIS_FIELDS)
    if write_header:
        writer.writeheader()

    stats = {"ok": 0, "fail": 0}
    sem = asyncio.Semaphore(limits.concurrency(CONCURRENCY))

    def save(src, res):
        tags = clean_tags(res.get("t"), area_of)
        item = {
            "id": src["recommendationid"],
            "up": 1 if src["voted_up"] not in ("0", "False", "false") else 0,
            "h": hours(src),
            "len": len(src["content"]),
            "votes": int(src.get("votes_up") or 0),
            "ts": int(src.get("timestamp_created") or 0),
            "s": res.get("s") if res.get("s") in ("P", "N", "M", "U") else "U",
            "f": [x for x in (res.get("f") or []) if isinstance(x, str) and x in FUN_TYPES][:2],
            "t": tags,
            "k": str(res.get("k", ""))[:40],
        }
        f_v3.write(json.dumps(item, ensure_ascii=False) + "\n")
        writer.writerow(analysis_row(src, res, area_of))
        done[item["id"]] = item
        stats["ok"] += 1
        progress.report("classify", len(done), len(long_rows))
        for twin in copies.pop(src["recommendationid"], []):
            save(twin, res)

    def brief(r):
        return {"id": r["recommendationid"], "up": 1 if r["voted_up"] not in ("0", "False", "false") else 0,
                "t": clip(r["content"], CLIP_B)}

    async def run(batch):
        async with sem:
            payload = [brief(r) for r in batch]
            user = USER_B.format(themes=", ".join(theme_names), fun=", ".join(fun_names), reviews=compact(payload))
            try:
                res = await ask(client, "B", SYSTEM_B, user, 90 * len(batch) + 60)
                by_id = {str(x.get("id")): x for x in res if isinstance(x, dict)}
                missing = [r for r in batch if r["recommendationid"] not in by_id]
                for r in batch:
                    if r["recommendationid"] in by_id:
                        save(r, by_id[r["recommendationid"]])
                if not missing:
                    return
                batch = missing
            except (FatalApiError, BudgetExceeded):
                raise
            except Exception as e:
                print(f"  [B 묶음 실패 → 한 건씩] {str(e)[:80]}")
            for r in batch:  # 빠진 것만 한 건씩 다시
                one = [brief(r)]
                try:
                    res = await ask(client, "B", SYSTEM_B,
                                    USER_B.format(themes=", ".join(theme_names), fun=", ".join(fun_names),
                                                  reviews=compact(one)), 150)
                    if res and isinstance(res[0], dict) and str(res[0].get("id")) == r["recommendationid"]:
                        save(r, res[0])
                    else:
                        stats["fail"] += 1
                except (FatalApiError, BudgetExceeded):
                    raise
                except Exception:
                    stats["fail"] += 1
            f_v3.flush(); f_csv.flush()

    batches = [pending[i:i + BATCH_B] for i in range(0, len(pending), BATCH_B)]
    tasks = [asyncio.create_task(run(b)) for b in batches]
    try:
        for i, t in enumerate(asyncio.as_completed(tasks), 1):
            await t
            f_v3.flush(); f_csv.flush()
            if i % max(1, len(batches) // 10) == 0 or i == len(batches):
                print(f"  [B] {i}/{len(batches)} 묶음 · 성공 {stats['ok']} · 실패 {stats['fail']}")
    except (FatalApiError, BudgetExceeded):
        for t in tasks:
            t.cancel()
        raise
    finally:
        f_v3.close(); f_csv.close()

    total = stats["ok"] + stats["fail"]
    if stats["ok"] == 0:
        raise RuntimeError(f"분류 결과가 0건입니다 ({stats['fail']}건 실패). 모델: {cfg.MODEL}")
    if total and stats["fail"] / total > 0.5:
        raise RuntimeError(f"분류 실패율 {stats['fail'] / total * 100:.0f}% ({stats['fail']}/{total}). 중단합니다.")
    return done


# ── C. 불만 심층 ──────────────────────────────────────────────────────

SYSTEM_C = "게임 기획자를 돕는 리뷰 분석가입니다. 리뷰에 없는 내용은 지어내지 않습니다. JSON 객체만 출력합니다."

USER_C = """주제: {themes}
리뷰 원문은 여러 언어일 수 있습니다. prob, why, fix는 한국어로 쓰세요.

불만이 있을 수 있는 리뷰입니다:
{reviews}

리뷰마다 불만만 주제별로 나눠 {{"items":[...]}} 형식의 JSON 객체로 답하세요. 칭찬은 넣지 마세요.
{{"items":[{{"id":"","p":[{{"t":"주제","prob":"","why":"","fix":""}}]}}]}}
- prob: 무엇이 불편하거나 싫은가, 25자 이내
- why: 리뷰가 직접 밝힌 원인. 추측하지 말고 없으면 빈 문자열
- fix: 유저가 "~해줬으면", "~하면 좋겠다"처럼 직접 요청한 해결책만. 없으면 빈 문자열
- t는 위 주제 목록에서만
- 불만이 없는 리뷰는 "p":[]"""


def needs_deep(item, src):
    if len(src["content"]) < MIN_LEN_C:
        return False
    negative_vote = str(src.get("voted_up", "")).lower() in ("0", "false")
    return item.get("s") in ("N", "M") or negative_vote or any(p[1] == "N" for p in item.get("t") or [])


async def dig_complaints(client, long_rows, classified, themes):
    theme_names = [t["name"] for t in themes]
    out_path = path("complaints_v3.jsonl")
    done = read_jsonl(out_path)
    by_id = {r["recommendationid"]: r for r in long_rows}
    targets = [by_id[i] for i, item in classified.items() if i in by_id and needs_deep(item, by_id[i])]
    pending = [r for r in targets if r["recommendationid"] not in done]
    print(f"[C] 심층 대상 {len(targets)}건 / 이미 {len(done)}건 / 남은 {len(pending)}건")
    target_ids = {r["recommendationid"] for r in targets}
    report = lambda force=False: progress.report("deep", len(target_ids & done.keys()), len(targets), force=force)
    report(force=True)
    if not pending:
        return done
    pending, copies = same_text_groups(pending)

    f_out = open(out_path, "a", encoding="utf-8")
    sem = asyncio.Semaphore(limits.concurrency(CONCURRENCY))

    def save_results(res, allowed_ids):
        for x in res:
            if not isinstance(x, dict):
                continue
            rid = str(x.get("id"))
            if rid not in allowed_ids or rid in done:
                continue
            parts = []
            for p in x.get("p") or []:
                if isinstance(p, dict) and p.get("t") in theme_names:
                    parts.append({k: str(p.get(k, ""))[:60] for k in ("t", "prob", "why", "fix")})
            for twin in [rid] + [r["recommendationid"] for r in copies.pop(rid, [])]:
                item = {"id": twin, "p": parts}
                f_out.write(json.dumps(item, ensure_ascii=False) + "\n")
                done[twin] = item
        f_out.flush()
        report()

    async def run(batch):
        async with sem:
            batch_ids = {r["recommendationid"] for r in batch}
            payload = [{"id": r["recommendationid"], "t": clip(r["content"], CLIP_C)} for r in batch]
            try:
                res = await ask(client, "C", SYSTEM_C,
                                USER_C.format(themes=", ".join(theme_names), reviews=compact(payload)),
                                240 * len(batch) + 100)
            except (FatalApiError, BudgetExceeded):
                raise
            except Exception as e:
                print(f"  [C 묶음 실패, 누락 건만 재시도] {str(e)[:80]}")
                return
            save_results(res, batch_ids)

    async def retry_one(row):
        async with sem:
            rid = row["recommendationid"]
            payload = [{"id": rid, "t": clip(row["content"], CLIP_C)}]
            try:
                res = await ask(client, "C", SYSTEM_C,
                                USER_C.format(themes=", ".join(theme_names), reviews=compact(payload)), 900)
                save_results(res, {rid})
            except (FatalApiError, BudgetExceeded):
                raise
            except Exception as e:
                print(f"  [C 단건 실패] {rid}: {str(e)[:80]}")

    batches = [pending[i:i + BATCH_C] for i in range(0, len(pending), BATCH_C)]
    tasks = [asyncio.create_task(run(b)) for b in batches]
    try:
        for i, t in enumerate(asyncio.as_completed(tasks), 1):
            await t
            if i % max(1, len(batches) // 5) == 0 or i == len(batches):
                print(f"  [C] {i}/{len(batches)} 묶음")
        retry_rows = [r for r in pending if r["recommendationid"] not in done]
        if retry_rows:
            print(f"  [C] 빠진 {len(retry_rows)}건만 단건 재시도")
            await asyncio.gather(*(retry_one(r) for r in retry_rows))
    except (FatalApiError, BudgetExceeded):
        for t in tasks:
            t.cancel()
        raise
    finally:
        f_out.close()
    missing = [r["recommendationid"] for r in targets if r["recommendationid"] not in done]
    if missing:
        raise RuntimeError(f"불만 심층 분석 {len(missing)}건이 누락됐습니다. 재실행하면 누락 건만 다시 분석합니다.")
    return done


# ── 실행 ─────────────────────────────────────────────────────────────

def save_usage():
    p = path("usage_v3.json")
    prev = {}
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            prev = json.load(f)
    merged = {}
    for s in USAGE:
        old = prev.get(s, {})
        merged[s] = {k: int(old.get(k, 0)) + USAGE[s][k] for k in USAGE[s]}
    merged["model"] = cfg.MODEL
    merged["pricing"] = {"input_per_1m": cfg.MODEL_COST_INPUT,
                         "output_per_1m": cfg.MODEL_COST_OUTPUT}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    tin = sum(merged[s]["input"] for s in ("A", "B", "C"))
    tout = sum(merged[s]["output"] for s in ("A", "B", "C"))
    print(f"[토큰] 입력 {tin:,} · 출력 {tout:,}")


async def run_all():
    for stage in USAGE:
        USAGE[stage] = {"calls": 0, "input": 0, "output": 0}
    rows, long_rows = load_reviews()
    print(f"수집 {len(rows)}건 · AI 분석 대상 {len(long_rows)}건 · 짧거나 내용이 없는 리뷰 {len(rows) - len(long_rows)}건(집계만)")
    if not long_rows:
        print("분석할 본문이 없어 AI를 호출하지 않습니다")
        return
    if not API_KEY:
        raise SystemExit(".env의 OPENROUTER_API_KEY를 확인하세요.")
    print(f"모델: {cfg.MODEL}")
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=10)) as client:
        try:
            themes = await find_themes(client, long_rows)
            classified = await classify(client, long_rows, themes)
            await dig_complaints(client, long_rows, classified, themes)
        finally:
            save_usage()


def main():
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
