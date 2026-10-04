"""현재 리뷰 분석 — 게임별 주제 · 재미 종류 · 불만 심층.

모든 게임에 같은 주제를 강제하지 않고 다음 세 단계로 분석한다.

  A. 주제 찾기   리뷰를 세 묶음(각 최대 100건)으로 나눠 주제 후보를 찾고, 후보를 한 번 더 정리해 목록을 만든다.
  B. 전체 분류   모든 리뷰를 짧게 분류한다. 언급한 주제만 답하게 해 출력 토큰을 줄인다.
  C. 불만 심층   불만·섞임 리뷰만 문제 / 원인 / 유저 제안으로 나눈다.

한 글자짜리처럼 내용이 없는 리뷰만 AI에 보내지 않는다.

결과 파일
  themes_v3.json       A의 주제 목록
  analysis_v3.jsonl    B의 리뷰별 분류 (한 줄에 한 리뷰)
  complaints_v3.jsonl  C의 불만 분해
  usage_v3.json        단계별 실제 토큰 사용량
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

sys.stdout.reconfigure(encoding="utf-8")
from config import cfg
import progress
import openrouter_limits as limits
from dashboard_evidence import korean_only
from dashboard_evidence import FUN as FUN_TYPES  # MDA 프레임워크의 8가지 재미. 화면과 같은 목록을 쓴다

API_KEY = cfg.OPENROUTER_API_KEY
URL = cfg.OPENROUTER_URL

THEME_VERSION = 4        # 주제 찾기 방식이 바뀌면 올린다. 예전 방식으로 만든 목록과 분류는 보관하고 다시 한다
THEME_SAMPLES = 3        # A에서 서로 다른 리뷰 묶음으로 후보를 찾는 횟수. 묶음 하나에 치우친 주제가 목록을 흔들지 않게 한다
THEME_SAMPLE = 100       # 묶음 하나에 담는 리뷰 수
THEME_MAX = 16           # 최종 주제 수 상한 ("기타" 제외). 더 많으면 주제끼리 겹쳐 분류가 갈린다
# 느낌이나 평가이지 게임의 구성 요소가 아닌 말. AI가 이런 이름을 내면 규칙으로 뺀다.
FEELING_WORDS = ("시간", "재미", "몰입", "중독", "성취", "만족", "추천", "평가", "갓겜", "기대")
BATCH_B = 15             # B 한 번에 담을 리뷰 수. 고정 지시문 비용을 더 많은 리뷰가 나눠 낸다
BATCH_C = 5              # 심층 답변이 길어 JSON이 잘리지 않도록 작은 묶음 사용
CONCURRENCY = 3
CLIP_B = 480             # B에 보낼 본문 최대 글자 (앞부분 + 끝부분)
CLIP_C = 720             # C에 보낼 본문 최대 글자 (앞부분 + 끝부분)
MIN_LEN_C = 15           # 이보다 짧은 불만은 심층 분석할 내용이 없다

BUSY_MESSAGE = ("'{model}' 모델이 지금 요청을 받아 주지 않습니다(이용자가 몰려 있음). 몇 분 뒤 다시 실행하거나 다른 모델을 고르세요. "
                "모아 둔 리뷰와 이미 분석한 결과는 그대로 두고 이어서 분석합니다.")


class FatalApiError(RuntimeError):
    """재시도해도 소용없는 오류 (모델 없음, 키 무효, 잔액 부족). 즉시 멈춘다."""


class ModelBusy(FatalApiError):
    """모델이 요청을 계속 거절한다. 한 건씩 다시 물으면 거절만 쌓여 몇 시간을 헛돌므로 멈추고 사람에게 알린다."""


STALL_LIMIT = 3          # 연달아 이만큼의 묶음에서 한 건도 분류하지 못하면 멈춘다
STALL_MESSAGE = ("AI가 연달아 답을 주지 못해 분석을 멈췄습니다. 잠시 뒤 이어서 하거나 다른 모델을 고르시면 됩니다. "
                 "모아 둔 리뷰와 이미 분석한 결과는 그대로 남아 있습니다.")


def path(name):
    return os.path.join(cfg.project_dir(), name)


USAGE = {s: {"calls": 0, "input": 0, "output": 0} for s in ("A", "B", "C")}
STAND_INS = {}   # 고른 모델이 붐벼서 대신 답한 모델 → 횟수


def clean_json(text):
    text = text.strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\[[\s\S]*\])"):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return text


def parse_answer(content):
    """AI가 준 글에서 답 목록을 읽는다.

    모델에 따라 설명을 앞에 붙이거나, 배열 뒤에 괄호를 하나 더 붙이거나, 첫 항목만 배열에 넣고 나머지는 줄마다 객체로 준다.
    글을 처음부터 훑으며 읽히는 JSON 조각을 모아, 가장 긴 목록과 따로 떨어진 객체들을 합친다."""
    text = (content or "").strip()
    decoder, best, loose, at = json.JSONDecoder(), [], [], 0
    while at < len(text):
        if text[at] not in "[{":
            at += 1
            continue
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at += 1
            continue
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            value = value["items"]
        if isinstance(value, list):
            found = [x for x in value if isinstance(x, dict)]
            if len(found) > len(best):
                loose, best = [], found      # 더 긴 목록이 나오면 앞에서 주운 것은 설명 속 예시였다
        elif isinstance(value, dict):
            loose.append(value)
        at = end
    if not best and not loose:
        raise ValueError("AI 답을 JSON으로 읽지 못했습니다")
    return best + loose


async def ask(client, stage, system, user, max_tokens):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    max_tokens = limits.answer_room(max_tokens)
    body = {
        **limits.model_fields(),
        **limits.thinking_fields(),
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
                    raise ModelBusy(BUSY_MESSAGE.format(model=cfg.MODEL))
                # 무료 모델은 여러 사람이 같이 써서 제공사 쪽이 붐비면 거절한다. 기다림을 15초, 30초, 60초로 늘려 가며 다시 묻는다.
                await asyncio.sleep(limits.retry_after(r, 15.0 * 2 ** (rate_waits - 1) if limits.is_free() else 5.0))
                continue
            if r.status_code == 400 and body.pop("reasoning", None):
                raise ValueError("선택한 모델이 생각 상한을 받지 않아 빼고 재시도합니다")
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
                    raise ModelBusy(BUSY_MESSAGE.format(model=cfg.MODEL))
                raise FatalApiError(str(error)[:200])
            usage = data.get("usage") or {}
            if guard:
                guard.finish(reservation, usage)
                usage_recorded = True
            USAGE[stage]["calls"] += 1
            answered = data.get("model")
            if answered and answered != cfg.MODEL:      # 대신 답한 모델을 적어 둔다
                STAND_INS[answered] = STAND_INS.get(answered, 0) + 1
            USAGE[stage]["input"] += int(usage.get("prompt_tokens") or 0)
            USAGE[stage]["output"] += int(usage.get("completion_tokens") or 0)
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            parsed = parse_answer(content)
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

유저들이 반복해서 말하는 대상을 두 종류로 찾으세요. 합쳐서 8~14개입니다.
① 게임의 구성 요소 6~10개: 유저가 무엇을 하며 노는지(예: 수집, 전투, 건축, 탐험, 멀티플레이, 육성, 제작, 자동화)와
  무엇이 마음에 드는지(예: 캐릭터 디자인 — 외형·귀여움). 칭찬 리뷰에서 주로 나옵니다.
  "귀엽다"와 "잡아서 모은다"는 서로 다른 주제입니다. 동료·부하에게 일을 시키는 시스템(작업 AI, 자동화)도 따로 잡으세요.
② 기능은 아니지만 반복해서 나오는 불만의 대상 2~4개.
- 기능이 아닌 대상의 예: "버그"(오류·끼임·튕김), "콘텐츠 분량"(후반에 할 거리), "밸런스", "독창성"(다른 게임과 닮음), "가격", "업데이트"(운영·패치).
  리뷰에 여러 번 나오면 반드시 주제로 넣으세요. 빼면 그 불만은 어느 주제에도 들어가지 못합니다.
- name: 이 게임에 맞는 구체적인 대상 이름, 한국어 2~6자.
  좋은 예: "저장", "조작", "서버 동기화", "최적화", "인벤토리", "튜토리얼", "전투", "가격"
  나쁜 예: "게임플레이"(너무 넓음), "멋진 캐릭터"(평가가 들어감), "RPG"·"액션"(장르 이름)
- 느낌은 주제가 아닙니다. "시간 순삭", "몰입", "중독성", "재미", "성취감"은 넣지 마세요.
  무엇이 그렇게 만들었는지(수집, 건축, 전투 등)가 주제입니다.
- 좋다·나쁘다는 넣지 마세요. 같은 주제로 칭찬과 불만을 모두 담을 수 있어야 합니다.
- 같은 문제를 가리키는 것은 하나로 합치세요. 예: 저장이 안 됨 = 세이브가 사라짐, 렉 = 최적화
- desc: 이 주제에 무엇이 들어가는지 20자 이내, 평가 없이 중립으로. 리뷰를 분류할 때 이 설명으로 구분합니다.
{{"items":[{{"name":"","desc":""}}]}}"""

USER_A2 = """같은 게임의 리뷰를 여러 묶음으로 나눠 찾은 주제 후보입니다. seen은 그 후보가 나온 묶음 수입니다.
{candidates}

후보를 정리해 최종 주제를 10~{limit}개로 만드세요. 게임의 구성 요소(무엇을 하며 노는지)가 절반을 넘어야 합니다.
- 구성 요소 후보(수집, 탐험, 육성, 멀티플레이, 캐릭터 디자인, 자동화 등)는 한 묶음에만 나왔어도 남기세요. 칭찬은 여기에 붙습니다.
- desc에는 그 주제에 들어가는 구체적인 낱말을 적으세요. 예: 수집 → "잡기·포획·도감 채우기", 캐릭터 디자인 → "외형·귀여움".
- 같은 문제·같은 대상을 가리키는 후보는 하나로 합치세요.
  예: "저장"과 "데이터 삭제"(둘 다 세이브가 사라지는 문제), "렉"과 "최적화", "팰"과 "팰 디자인"
- 불만 대상 후보는 여러 묶음에서 나온 것을 먼저 남기고, 한 묶음에만 나온 지엽적인 것은 빼세요.
- 게임의 구성 요소와, 기능은 아니지만 반복해서 나오는 불만의 대상("버그", "콘텐츠 분량", "밸런스", "독창성", "가격", "업데이트")을 남기세요.
  이런 후보가 있으면 지엽적이라고 빼지 마세요. 성능(렉·프레임)과 버그(오류·끼임)는 서로 다른 주제입니다.
- 느낌·상태("시간 순삭", "몰입", "중독", "재미")와 장르 이름은 빼세요.
- 주제끼리 범위가 겹치지 않게 하세요. 한 리뷰 문장이 두 주제에 똑같이 들어맞으면 둘을 합치거나 경계를 나누세요.
  예: "팰"에 포획·육성을 넣었다면 "수집"을 따로 두지 않습니다.
- name: 한국어 2~6자, 좋다·나쁘다 없이.
- desc: 무엇이 들어가는지 20자 이내, 합친 후보를 포함해서. 칭찬과 불만을 모두 담도록 중립으로 쓰세요.
  "부족", "불편", "문제", "멍청한" 같은 평가는 빼세요. 나쁜 예: "즐길 거리 부족" → 좋은 예: "즐길 거리의 양과 후반 진행"
{{"items":[{{"name":"","desc":""}}]}}"""


def themes_are_current(themes):
    """지금 방식(THEME_VERSION)으로 만든 주제 목록인가. 빈 목록은 아직 만들지 않은 것으로 본다."""
    return all(isinstance(t, dict) and t.get("v") == THEME_VERSION for t in themes or [])


def theme_guide(themes):
    """분류 지시문에 넣는 주제 목록. 설명을 같이 줘야 낱말만 같은 다른 뜻("거점 최적화")을 가려낸다."""
    return "; ".join(f"{t['name']}: {t['desc']}" if t.get("desc") else t["name"] for t in themes)


def theme_samples(rows):
    """주제 후보를 찾을 리뷰 묶음들. 묶음끼리 리뷰가 겹치지 않고, 묶음마다 비추천과 추천을 번갈아 담는다.

    비추천은 전체의 몇 %뿐이라 비율대로 담으면 불만 주제를 놓친다. 그래서 묶음의 절반까지 비추천을 넣는다.
    """
    rng = random.Random(42)
    is_down = lambda r: r["voted_up"] in ("0", "False", "false")
    usable = [r for r in rows if len(r["content"]) >= 20]
    negatives = [r for r in usable if is_down(r)]
    positives = [r for r in usable if not is_down(r)]
    rng.shuffle(negatives)
    rng.shuffle(positives)
    count = max(1, min(THEME_SAMPLES, len(usable) // 40))
    byte_limit = min(27000, max(12000, int(getattr(cfg, "MODEL_CONTEXT_LENGTH", 32768)) - 5000))
    samples = []
    for index in range(count):
        down, up = negatives[index::count], positives[index::count]
        payload = []
        for position in range(max(len(down), len(up))):
            for vote, group in ((0, down), (1, up)):
                if position >= len(group) or len(payload) >= THEME_SAMPLE:
                    continue
                candidate = {"up": vote, "t": group[position]["content"][:300]}
                if len(compact(payload + [candidate]).encode("utf-8")) <= byte_limit:
                    payload.append(candidate)
        if payload:
            samples.append(payload)
    return samples


def clean_themes(items):
    """AI가 낸 주제에서 이름이 없거나, 겹치거나, 느낌을 가리키는 것을 뺀다."""
    themes, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        key = "".join(name.split()).lower()
        if not name or name == "기타" or key in seen or any(word in name for word in FEELING_WORDS):
            continue
        seen.add(key)
        themes.append({"name": name, "desc": str(item.get("desc", "")).strip()[:30], "v": THEME_VERSION})
    return themes


async def find_themes(client, long_rows):
    progress.report("topics", force=True)
    p = path("themes_v3.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            themes = json.load(f)
        if themes and themes_are_current(themes):
            print(f"[A] 기존 주제 {len(themes)}개 사용")
            return themes

    samples = theme_samples(long_rows)
    print(f"[A] 리뷰 {sum(map(len, samples))}건을 {len(samples)}묶음으로 나눠 주제 후보를 찾습니다")
    sem = asyncio.Semaphore(limits.concurrency(CONCURRENCY))   # 무료 모델은 한 번에 하나씩

    async def candidates_of(sample):
        async with sem:
            return await ask(client, "A", SYSTEM_A, USER_A.format(n=len(sample), reviews=compact(sample)), 1500)

    found = await asyncio.gather(*(candidates_of(s) for s in samples))
    # 후보마다 몇 묶음에서 나왔는지 센다. 여러 묶음에 나온 후보가 이 게임의 주제일 가능성이 높다.
    candidates = {}
    for items in found:
        for theme in clean_themes(items):
            entry = candidates.setdefault(theme["name"], {"name": theme["name"], "desc": theme["desc"], "seen": 0})
            entry["seen"] += 1
    ranked = sorted(candidates.values(), key=lambda c: (-c["seen"], c["name"]))
    final = await ask(client, "A", SYSTEM_A, USER_A2.format(candidates=compact(ranked), limit=THEME_MAX), 1500)
    themes = clean_themes(final)[:THEME_MAX] or clean_themes(ranked)[:THEME_MAX]
    themes.append({"name": "기타", "desc": "목록에 없는 주제", "v": THEME_VERSION})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(themes, f, ensure_ascii=False, indent=2)
    print(f"[A] 후보 {len(ranked)}개 → 주제 {len(themes)}개: {', '.join(t['name'] for t in themes)}")
    return themes


# ── B. 전체 분류 ──────────────────────────────────────────────────────

SYSTEM_B = "게임 리뷰 분류기입니다. JSON 객체만 출력합니다. 설명하지 않습니다."

USER_B = """주제 (이름: 무엇이 들어가는지): {themes}
재미 종류: {fun}
리뷰 원문은 여러 언어일 수 있습니다. k 요약은 한국어로 쓰세요.

리뷰 (up=추천 여부):
{reviews}

리뷰마다 한 항목씩 {{"items":[...]}} 형식의 JSON 객체로 답하세요.
- s: P 긍정, N 부정, M 섞임, U 판단 불가
- s와 주제별 P/N은 리뷰 문장으로 판단하세요. 추천 여부(up)만으로 감정을 정하지 마세요.
  "시간이 사라진다", "잠을 못 잔다", "현생이 망한다"는 빠져들었다는 칭찬입니다 → P
  추천한(up=1) 리뷰가 "내 시간 돌려줘", "시간이 삭제된다", "주말이 사라졌다", "하지 마라 인생 망한다", "마약 같다"처럼
  시간·생활을 빼앗겼다고만 말하면 농담 섞인 칭찬입니다 → s는 P, t는 [].
  "시간이 삭제된다"는 저장 데이터가 지워졌다는 뜻이 아닙니다. 세이브·데이터·진행 상황이 사라졌다고 써야 저장 불만입니다.
  다른 회사나 다른 게임을 꾸짖는 말("○○는 이거 보고 반성해라")은 이 게임 칭찬입니다 → P.
  추천한(up=1) 짧은 글이 구체적인 문제를 말하지 않으면 N으로 하지 마세요. 뜻을 알 수 없으면 U입니다.
- f: 긍정·섞임 리뷰는 드러난 재미 종류를 1~2개 고르세요 (위 목록에서만). 근거가 전혀 없을 때만 []
- t: 리뷰가 구체적인 대상을 말했을 때만 넣고, 주제별 P 또는 N. 주제 이름만 적으세요(설명은 빼고).
  낱말이 같아도 설명과 뜻이 다르면 넣지 마세요. 예: "거점을 최적화"는 성능 얘기가 아닙니다.
  "재밌다", "갓겜" 같은 막연한 말은 주제가 아닙니다 → []
  글에 그 주제의 대상이 글자로 나오지 않으면 붙이지 마세요. 짐작으로 붙이지 않습니다.
  "여러 게임을 잘 섞었다", "노가다가 좋다", "업데이트 고맙다"처럼 어느 대상인지 알 수 없는 글 → []
  "귀엽다"만 있으면 외형 주제가 있을 때만 거기에 붙이고, 수집·육성 같은 다른 주제에는 붙이지 마세요.
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
→ {{"id":"4","s":"N","f":[],"t":[["최적화","N"]],"k":"실행 즉시 튕김"}}
"뭐야 내 시간 돌려줘요" (up=1)
→ {{"id":"5","s":"P","f":["몰두"],"t":[],"k":"시간 가는 줄 모름"}}
"시간이 삭제됩니다 살려주세요" (up=1)
→ {{"id":"6","s":"P","f":["몰두"],"t":[],"k":"시간이 순식간에 감"}}
"이것저것 다 섞어 놓은 비빔밥 같은 갓겜" (up=1)
→ {{"id":"7","s":"P","f":[],"t":[],"k":"여러 요소를 잘 섞음"}}"""


def clean_tags(raw, names):
    """[["주제","P"], ...]만 남긴다. AI가 형식을 틀리게 주는 경우가 있다."""
    tags = []
    for pair in raw or []:
        if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)):
            continue
        name = pair[0] if pair[0] in names else pair[0].split(":")[0].strip()   # "이름: 설명"으로 답한 경우
        if name in names and pair[1] in ("P", "N") and [name, pair[1]] not in tags:
            tags.append([name, pair[1]])
    return tags


async def classify(client, long_rows, themes):
    theme_names = [t["name"] for t in themes]
    fun_names = list(FUN_TYPES)
    out_path = path("analysis_v3.jsonl")
    done = read_jsonl(out_path)
    pending = [r for r in long_rows if r["recommendationid"] not in done]
    print(f"[B] 분류 대상 {len(long_rows)}건 / 이미 {len(done)}건 / 남은 {len(pending)}건")
    progress.report("classify", len(done), len(long_rows), force=True)
    if not pending:
        return done
    pending, copies = same_text_groups(pending)
    if copies:
        print(f"[B] 본문이 같은 리뷰 {sum(map(len, copies.values()))}건은 대표 리뷰 결과를 함께 씁니다")

    f_v3 = open(out_path, "a", encoding="utf-8")

    stats = {"ok": 0, "fail": 0, "stalled": 0}
    sem = asyncio.Semaphore(limits.concurrency(CONCURRENCY))

    def save(src, res):
        # AI가 답한 것만 적는다. 추천 여부·플레이 시간 같은 원본 값은 reviews.csv에서 읽는다.
        item = {
            "id": src["recommendationid"],
            "s": res.get("s") if res.get("s") in ("P", "N", "M", "U") else "U",
            "f": [x for x in (res.get("f") or []) if isinstance(x, str) and x in FUN_TYPES][:2],
            "t": clean_tags(res.get("t"), theme_names),
            "k": korean_only(res.get("k", ""))[:40],
        }
        f_v3.write(json.dumps(item, ensure_ascii=False) + "\n")
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
            if stats["stalled"] >= STALL_LIMIT:   # 이미 멈추기로 했으면 남은 묶음은 묻지 않는다
                raise FatalApiError(STALL_MESSAGE)
            before = stats["ok"]
            try:
                await attempt(batch)
            finally:
                f_v3.flush()
            stats["stalled"] = 0 if stats["ok"] > before else stats["stalled"] + 1
            if stats["stalled"] >= STALL_LIMIT:
                raise FatalApiError(STALL_MESSAGE)

    async def attempt(batch):
        payload = [brief(r) for r in batch]
        user = USER_B.format(themes=theme_guide(themes), fun=", ".join(fun_names), reviews=compact(payload))
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
            if not await single(r):
                missed.append(r)

    missed = []   # 한 건씩 물어도 답을 못 받은 리뷰. 모든 묶음이 끝난 뒤 한 번 더 묻는다

    async def single(r):
        try:
            res = await ask(client, "B", SYSTEM_B,
                            USER_B.format(themes=theme_guide(themes), fun=", ".join(fun_names),
                                          reviews=compact([brief(r)])), 150)
        except (FatalApiError, BudgetExceeded):
            raise
        except Exception:
            return False
        # 한 건만 물었으므로 답이 하나면 번호를 다르게 적어 와도 이 리뷰의 답이다
        answers = [x for x in res if isinstance(x, dict)]
        if len(answers) != 1:
            return False
        save(r, answers[0])
        return True

    batches = [pending[i:i + BATCH_B] for i in range(0, len(pending), BATCH_B)]
    tasks = [asyncio.create_task(run(b)) for b in batches]
    try:
        for i, t in enumerate(asyncio.as_completed(tasks), 1):
            await t
            f_v3.flush()
            if i % max(1, len(batches) // 10) == 0 or i == len(batches):
                print(f"  [B] {i}/{len(batches)} 묶음 · 성공 {stats['ok']} · 실패 {stats['fail']}")
        for r in [r for r in missed if r["recommendationid"] not in done]:
            if not await single(r):
                stats["fail"] += 1
        if missed:
            print(f"  [B] 빠진 {len(missed)}건을 다시 물어 {sum(r['recommendationid'] in done for r in missed)}건을 채웠습니다")
    except (FatalApiError, BudgetExceeded):
        for t in tasks:
            t.cancel()
        raise
    finally:
        f_v3.close()

    total = stats["ok"] + stats["fail"]
    if stats["ok"] == 0:
        raise RuntimeError(f"분류 결과가 0건입니다 ({stats['fail']}건 실패). 모델: {cfg.MODEL}")
    if total and stats["fail"] / total > 0.5:
        raise RuntimeError(f"분류 실패율 {stats['fail'] / total * 100:.0f}% ({stats['fail']}/{total}). 중단합니다.")
    return done


# ── C. 불만 심층 ──────────────────────────────────────────────────────

SYSTEM_C = "게임 기획자를 돕는 리뷰 분석가입니다. 리뷰에 없는 내용은 지어내지 않습니다. JSON 객체만 출력합니다."

USER_C = """주제 (이름: 무엇이 들어가는지): {themes}
리뷰 원문은 여러 언어일 수 있습니다. prob, why, fix는 한국어로 쓰세요.

불만이 있을 수 있는 리뷰입니다:
{reviews}

리뷰마다 불만만 주제별로 나눠 {{"items":[...]}} 형식의 JSON 객체로 답하세요. 칭찬은 넣지 마세요.
{{"items":[{{"id":"","p":[{{"t":"주제","prob":"","why":"","fix":""}}]}}]}}
- prob: 무엇이 불편하거나 싫은가, 25자 이내
- why: 리뷰가 직접 밝힌 원인. 추측하지 말고 없으면 빈 문자열
- fix: 유저가 "~해줬으면", "~하면 좋겠다"처럼 직접 요청한 해결책만. 없으면 빈 문자열
- t는 위 주제 이름에서만 (설명은 빼고 이름만)
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
                name = str(p.get("t", "")).split(":")[0].strip() if isinstance(p, dict) else ""
                if name in theme_names:
                    parts.append({"t": name, **{k: str(p.get(k, ""))[:60] for k in ("prob", "why", "fix")}})
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
                                USER_C.format(themes=theme_guide(themes), reviews=compact(payload)),
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
                                USER_C.format(themes=theme_guide(themes), reviews=compact(payload)), 900)
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
    stand_ins = dict(prev.get("stand_ins") or {})
    for name, count in STAND_INS.items():
        stand_ins[name] = stand_ins.get(name, 0) + count
    if stand_ins:
        merged["stand_ins"] = stand_ins
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
    STAND_INS.clear()
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
