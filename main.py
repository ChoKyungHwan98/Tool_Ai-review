"""Steam 리뷰 수집·분석 대시보드 서버.

실행: uvicorn main:app --host 127.0.0.1 --port 8765
문서: http://127.0.0.1:8765/docs
대시보드: http://127.0.0.1:8765/dashboard

데이터 출처 및 라이선스:
- Steam 리뷰 데이터: Valve Steam Web API (공개 API, 비상업적 분석 목적)
- LLM 분석: OpenRouter API (선택 모델)
- 본 프로젝트는 교육·포트폴리오 목적으로 제작되었습니다.
"""

import csv
import io
import json
import os
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import cfg
from dashboard_evidence import build_evidence, evidence_page, korean_only, load_sources, review_hours
import analysis_design
import quality_check
import sampling
import steam_histogram
import progress


app = FastAPI(
    title="리뷰 분석 API",
    description="Steam 리뷰를 수집하고 v3 분석과 v5 인사이트를 제공하는 API",
    version="5.0.0",
)

# 이 서버는 내 컴퓨터에서만 쓴다. 화면을 여는 곳은 이 서버 자신과 스튜디오(studio.local)뿐이므로
# 그 밖의 웹사이트가 브라우저를 통해 삭제·분석 실행(유료 모델 호출)을 보내지 못하게 막는다.
ALLOWED_ORIGIN = r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|https://studio\.local)$"
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=ALLOWED_ORIGIN,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def block_foreign_origin(request, call_next):
    """CORS는 응답을 읽지 못하게 할 뿐 요청 자체는 막지 않는다. 바꾸는 요청은 출처를 직접 확인한다."""
    import re
    from fastapi.responses import JSONResponse
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and not re.match(ALLOWED_ORIGIN, origin):
        return JSONResponse(status_code=403, content={"detail": "허용되지 않은 출처의 요청입니다"})
    return await call_next(request)


# 화면 HTML은 주소가 고정이라 Cache-Control이 없으면 브라우저 휴리스틱 캐시가 걸린다.
# 스튜디오의 WebView2가 옛 화면을 계속 띄우게 되므로 항상 서버에 확인하게 한다.
NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}


@app.get("/", summary="메인 화면", include_in_schema=False)
def root():
    return FileResponse(os.path.join(cfg.PROGRAM_DIR, "static", "index.html"), headers=NO_CACHE_HEADERS)


# ====================================================================
# 대시보드
# ====================================================================

# 정적 파일 마운트 (프로그램 폴더 안의 UI만 제공)
STATIC_DIR = os.path.join(cfg.PROGRAM_DIR, "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/dashboard", response_class=HTMLResponse, summary="대시보드 페이지", include_in_schema=False)
def dashboard_page():
    """리뷰 분석 대시보드 (어플리케이션 UI)"""
    path = os.path.join(cfg.PROGRAM_DIR, "static", "dashboard.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="dashboard.html 없음")
    return FileResponse(path, headers=NO_CACHE_HEADERS)


# ── 멀티게임 관리 API ─────────────────────────────────────────────
GAMES_JSON = cfg.GAMES_JSON

def _load_games():
    if os.path.exists(GAMES_JSON):
        with open(GAMES_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("games", [])
    return []

def _save_games(games):
    os.makedirs(os.path.dirname(GAMES_JSON), exist_ok=True)
    with open(GAMES_JSON, "w", encoding="utf-8") as f:
        json.dump({"games": games}, f, ensure_ascii=False, indent=2)

def _game_dir(app_id):
    return cfg.project_dir(app_id)

def _game_file(app_id, filename):
    """Return a file from the one canonical 프로젝트/{app_id} store."""
    path = os.path.join(_game_dir(app_id), filename)
    return path if os.path.exists(path) else None


@app.get("/api/games", summary="분석된 게임 목록", include_in_schema=False)
def list_games():
    # An abandoned or failed analysis is not a project in the library.
    visible = []
    for game in _load_games():
        folder = os.path.join(cfg.PROJECTS_DIR, str(game.get("app_id")))
        if os.path.isfile(os.path.join(folder, "insights_v5.json")):
            visible.append(game)
    return {"games": visible, "unfinished": unfinished_runs()}


_GAME_INFO = {}   # 끝나지 못한 분석은 games.json에 없어서 이름을 Steam에 묻는다. 한 번 물은 것은 기억해 둔다.


def unfinished_runs():
    """끝나지 못한 분석(멈췄거나 지금 돌고 있는 것). 뒤에 남은 것을 사람이 모르는 일이 없도록 홈이 보여 준다."""
    known = {str(game.get("app_id")): game for game in _load_games()}
    try:
        names = sorted(os.listdir(cfg.PROJECTS_DIR))
    except OSError:
        return []
    runs = []
    for name in names:
        folder = os.path.join(cfg.PROJECTS_DIR, name)
        if not name.isdigit() or os.path.isfile(os.path.join(folder, "insights_v5.json")):
            continue
        if not os.path.isfile(os.path.join(folder, "pipeline_result.json")):
            continue
        record = pipeline_last_result(int(name))
        if record.get("status") not in ("failed", "running"):
            continue
        info = known.get(name) or _GAME_INFO.get(name)
        if info is None:
            try:
                info = _GAME_INFO[name] = search_steam_game(int(name))
            except Exception:
                info = {"name": f"App {name}", "header_image": ""}
        runs.append({"app_id": int(name), "name": info.get("name"), "header_image": info.get("header_image", ""),
                     "status": "running" if record["status"] == "running" else "stopped",
                     "error": record.get("error"), "kept": record["kept"],
                     "progress": record["live"]["progress"], "request": record.get("request")})
    return runs


@app.delete("/api/games/{app_id}", summary="프로젝트 삭제", include_in_schema=False)
def trash_game(app_id: int):
    """프로젝트를 목록에서 빼고 폴더를 휴지통으로 옮깁니다.

    지우지 않고 옮기기만 하므로 사용자가 직접 되돌릴 수 있습니다.
    """
    games = _load_games()
    remaining = [game for game in games if str(game.get("app_id")) != str(app_id)]
    # 끝나지 못한 분석은 목록에 없지만 폴더에는 모은 리뷰가 남아 있다. 그것도 치울 수 있어야 한다.
    source = os.path.join(cfg.PROJECTS_DIR, str(int(app_id)))
    if len(remaining) == len(games) and not os.path.isdir(source):
        raise HTTPException(status_code=404, detail="해당 프로젝트를 찾지 못했습니다.")
    import pipeline
    if pipeline.RUNNING["app_id"] == app_id:
        raise HTTPException(status_code=409, detail="지금 분석하고 있는 게임은 지울 수 없습니다. 분석이 끝나거나 멈춘 뒤에 지우세요.")

    if os.path.isdir(source):
        trash_root = os.path.join(os.path.dirname(source), ".review-trash")
        os.makedirs(trash_root, exist_ok=True)
        destination = os.path.join(trash_root, f"{app_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        os.rename(source, destination)

    _save_games(remaining)
    return {"app_id": app_id, "remaining": len(remaining)}


@app.get("/api/games/search", summary="Steam 게임 검색", include_in_schema=False)
def search_steam_game(app_id: int):
    """Steam Store API에서 게임 정보를 조회합니다."""
    import httpx
    try:
        r = httpx.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": app_id, "l": "korean"},
            timeout=10.0,
        )
        data = r.json().get(str(app_id), {})
        if not data.get("success"):
            raise HTTPException(status_code=404, detail=f"App ID {app_id}를 찾을 수 없습니다")
        info = data["data"]
        return {
            "app_id": app_id,
            "name": info.get("name", f"App {app_id}"),
            "header_image": info.get("header_image", ""),
            "type": info.get("type", ""),
            "short_description": info.get("short_description", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


REVIEW_LANGUAGES = {"koreana", "english", "japanese", "schinese", "tchinese", "all"}


@app.get("/api/games/review-stats", summary="Steam 리뷰 모집단 통계", include_in_schema=False)
def review_population_stats(app_id: int, language: str = "koreana"):
    """Steam appreviews API로 선택한 언어의 리뷰 통계를 조회합니다."""
    import httpx
    if language not in REVIEW_LANGUAGES:
        raise HTTPException(status_code=422, detail="지원하지 않는 리뷰 언어입니다")
    url = f"https://store.steampowered.com/appreviews/{app_id}"
    query = {"json": 1, "filter": "recent", "review_type": "all", "purchase_type": "all",
             "num_per_page": 0, "filter_offtopic_activity": 0}
    try:
        r_kr = httpx.get(url, params={**query, "language": language}, timeout=15.0)   # 선택한 언어
        r_kr.raise_for_status()
        qs_kr = r_kr.json().get("query_summary", {})
        r_all = httpx.get(url, params={**query, "language": "all"}, timeout=15.0)     # 모든 언어
        r_all.raise_for_status()
        qs_all = r_all.json().get("query_summary", {})

        total_kr = qs_kr.get("total_reviews", 0)
        pos_kr = qs_kr.get("total_positive", 0)
        neg_kr = qs_kr.get("total_negative", 0)
        total_all = qs_all.get("total_reviews", 0)
        score = qs_all.get("review_score_desc", "")

        # 손잡이 눈금마다 실제로 모을 건수. 수집기(collect_reviews.decide_sample_size)와 같은 함수로 계산한다.
        plans = []
        for margin in sampling.PRECISION_STOPS if total_kr else ():
            plan = sampling.plan_sample_size(total_kr, neg_kr, margin, cfg.MIN_NEG_REVIEWS)
            plans.append({"margin": margin, "cochran": plan["n_by_error"], "actual": plan["n_total"],
                          "min_neg_driven": plan["min_neg_driven"]})
        return {
            "app_id": app_id,
            "language": language,
            "global": {"total": total_all, "score": score},
            "selected": {
                "total": total_kr, "positive": pos_kr, "negative": neg_kr,
                "pos_rate": round(pos_kr / total_kr * 100, 1) if total_kr > 0 else 0,
                "neg_rate": round(neg_kr / total_kr * 100, 1) if total_kr > 0 else 0,
            },
            "min_neg": cfg.MIN_NEG_REVIEWS,
            "plans": plans,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/games/review-histogram", summary="Steam 전체 리뷰의 날짜별 추천·비추천 수", include_in_schema=False)
def review_histogram(app_id: int = Query(..., gt=0), refresh: bool = False):
    """저장해 둔 결과를 주고, refresh=true일 때만 Steam에서 다시 받는다. AI를 부르지 않는다."""
    folder = _game_dir(app_id)
    cached = steam_histogram.load(folder) if os.path.isdir(folder) else None
    if cached and not refresh and "events" in cached:   # 공지가 없는 예전 저장본은 한 번 다시 받는다
        return cached
    try:
        data = steam_histogram.fetch(app_id)
    except Exception:
        if cached:
            return {**cached, "stale": True}   # 못 받으면 지난 결과를 그대로 보여준다
        raise HTTPException(status_code=502, detail="Steam에서 리뷰 추이를 가져오지 못했습니다")
    if os.path.isdir(folder):
        steam_histogram.save(folder, data)
    return data


@app.get("/api/models", summary="OpenRouter 모델 목록 조회", include_in_schema=False)
def get_openrouter_models():
    """Live text-capable models with published prices; never label an old slug free."""
    from model_catalog import list_models
    try:
        models = list_models()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"모델 목록을 확인할 수 없습니다: {exc}") from exc
    import openrouter_limits
    return {"models": models, "free_count": sum(m["free"] for m in models),
            "paid_count": sum(not m["free"] for m in models),
            "free_limits": {"per_minute": openrouter_limits.FREE_RPM, "per_day": openrouter_limits.FREE_DAILY}}




@app.get("/api/reviews/download", summary="리뷰 CSV 다운로드", include_in_schema=False)
def download_reviews_csv(app_id: int = Query(..., gt=0)):
    """수집한 리뷰 원문 파일을 그대로 내려준다."""
    csv_path = _game_file(app_id, "reviews.csv")
    if not csv_path:
        raise HTTPException(status_code=404, detail="리뷰 CSV 파일을 찾을 수 없습니다")
    return FileResponse(csv_path, media_type="text/csv", filename=f"reviews_{app_id}.csv")


@app.get("/api/analysis/download", summary="분석 결과 CSV 다운로드", include_in_schema=False)
def download_analysis_csv(app_id: int = Query(..., gt=0)):
    """AI 분류 결과를 표로 내려준다. 저장해 둔 파일이 아니라 지금의 분류(analysis_v3.jsonl)에서 만든다."""
    path = _game_file(app_id, "analysis_v3.jsonl")
    source = load_sources(os.path.dirname(path)) if path else None
    if not source:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다")
    reviews, analyzed = source[:2]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["recommendationid", "voted_up", "playtime_h", "sentiment", "fun", "topics", "key_phrase", "content"])
    for rid, item in analyzed.items():
        row = reviews[rid]
        hours = review_hours(row)
        topics = [f"{pair[0]}:{pair[1]}" for pair in item.get("t") or []
                  if isinstance(pair, (list, tuple)) and len(pair) == 2]
        writer.writerow([rid, row.get("voted_up", ""), "" if hours is None else round(hours, 1),
                         SENTIMENT_NAMES.get(item.get("s"), "NEUTRAL"), "|".join(item.get("f") or []),
                         "|".join(topics), item.get("k", ""), row.get("content", "")])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="analysis_{app_id}.csv"'})


SENTIMENT_NAMES = {"P": "POSITIVE", "N": "NEGATIVE", "M": "MIXED", "U": "NEUTRAL"}


@app.get("/dashboard/data/v5", summary="기획자용 결론 데이터", include_in_schema=False)
def dashboard_data_v5(app_id: int = None):
    """현재 대시보드가 사용하는 v5 요약과 검증 자료. 분석 전이면 404."""
    games = _load_games()
    if app_id is None:
        if not games:
            raise HTTPException(status_code=404, detail="완료된 분석 프로젝트가 없습니다")
        app_id = games[0]["app_id"]
    game_info = next((g for g in games if g["app_id"] == app_id), None)
    path = _game_file(app_id, "insights_v5.json")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="insights_v5.json 없음 — 분석을 먼저 실행하세요")
    with open(path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    folder = os.path.dirname(path)
    # 저장된 파일에서는 AI가 쓴 것(요약·할 일·주제 설명)만 읽는다. 건수와 점수는 지금 다시 센다.
    data = {key: saved.get(key) for key in ("generated_at", "summary", "themes", "actions", "usage")}
    if saved.get("summary_source") == "rule":
        # AI 요약이 실패한 분석. 예전에는 불만 조각 하나를 그대로 적어 두었는데, 그것은 AI가 요약한 내용이 아니다.
        data["actions"] = [{**a, "prob": "", "why": "", "fix": []} for a in data.get("actions") or []]
    data["game"] = {
        "app_id": app_id,
        "name": (game_info.get("name_kr") or game_info.get("name")) if game_info else f"App {app_id}",
        "header_image": (game_info or {}).get("header_image", ""),
    }
    data["evidence"] = build_evidence(folder, app_id)
    data["sample_design_full"] = None
    design_path = _game_file(app_id, "sample_design.json")
    if design_path:
        try:
            with open(design_path, "r", encoding="utf-8") as stream:
                data["sample_design_full"] = json.load(stream)
        except (OSError, ValueError):
            pass
    data["quality_report"] = quality_check.run_quality_check(folder) if data["evidence"] else None
    data["reviews"] = review_rows(folder)
    apply_design(data)
    return data


def review_rows(directory):
    """리뷰 원문 화면의 카드. 원문은 reviews.csv, 분류는 analysis_v3.jsonl에서 읽는다."""
    source = load_sources(directory)
    if not source:
        return []
    reviews, analyzed = source[:2]
    rows = []
    for rid, item in analyzed.items():
        original = reviews[rid]
        tags = [pair[0] for pair in item.get("t") or []
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] != "기타"]
        rows.append({
            "recommendationid": rid,
            "content": original.get("content", ""),
            "voted_up": original.get("voted_up", ""),
            "playtime_h": review_hours(original),
            "overall_sentiment": SENTIMENT_NAMES.get(item.get("s"), "NEUTRAL"),
            "key_phrase": korean_only(item.get("k", "")),
            "keywords": "|".join(dict.fromkeys(tags)),        # 주제 이름
            "language": original.get("language", ""),
            "helpful": original.get("votes_up", ""),          # 리뷰 원문 화면의 정렬에 쓴다
            "created": original.get("timestamp_created", ""),
        })
    return rows


def apply_design(data):
    """자동으로 합친 주제 이름을 설명·할 일·리뷰 태그에도 똑같이 적용하고, 분석 설계서를 붙인다."""
    evidence = data.get("evidence") or {}
    alias = {m["from"]: m["to"] for m in evidence.get("merges") or []}
    alias = {name: analysis_design.resolve(name, alias) for name in alias}
    if alias:
        themes = {}
        for t in data.get("themes") or []:
            name = alias.get(t.get("name"), t.get("name"))
            if name not in themes:
                themes[name] = {**t, "name": name}
        data["themes"] = list(themes.values())
        actions = {}
        for a in data.get("actions") or []:
            name = alias.get(a.get("theme"), a.get("theme"))
            if name not in actions:
                actions[name] = {**a, "theme": name}
        data["actions"] = list(actions.values())
        for row in data.get("reviews") or []:
            names = [alias.get(name, name) for name in str(row.get("keywords") or "").split("|") if name]
            row["keywords"] = "|".join(dict.fromkeys(names))
    data["design_log"] = analysis_design.design_log(
        data.get("sample_design_full"), evidence.get("counts"), data.get("usage"),
        evidence.get("n_ai_topics", 0), evidence.get("merges") or [], evidence.get("themes") or [],
        (data.get("game") or {}).get("name", ""))


@app.get("/dashboard/evidence", include_in_schema=False)
def dashboard_topic_evidence(app_id: int = Query(..., gt=0), theme: str = Query(..., min_length=1, max_length=200),
                             sentiment: str = Query("N", pattern="^(P|N|all)$"),
                             page: int = Query(1, ge=1)):
    path = _game_file(app_id, "analysis_v3.jsonl")
    result = evidence_page(os.path.dirname(path), app_id, theme, sentiment, page) if path else None
    if result is None:
        raise HTTPException(status_code=404, detail="이 주제의 분석 원문이 없습니다")
    return result





# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  분석 파이프라인 실행 및 상태 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from fastapi import BackgroundTasks
from pipeline import run_pipeline


class PipelineRunRequest(BaseModel):
    app_id: int
    lang: str = "koreana"
    budget: float = 5.0
    target_error_pct: Optional[float] = None  # 슬라이더가 2.5 같은 소수 오차를 보낸다
    model: Optional[str] = None                # 화면에서 고른 분석 모델
    custom_sample_size: Optional[int] = None
    incremental: bool = False
    since: Optional[str] = None                # YYYY-MM-DD. 이 날짜 이후 리뷰만
    sort: str = "recent"                       # recent 최신순 · helpful 공감순 · random 무작위

@app.post("/pipeline/run", summary="파이프라인 실행", include_in_schema=False)
def trigger_pipeline(request: PipelineRunRequest, background_tasks: BackgroundTasks):
    if request.app_id <= 0 or request.lang not in REVIEW_LANGUAGES:
        raise HTTPException(status_code=422, detail="게임 번호 또는 리뷰 언어를 확인하세요")
    if not 0 < request.budget <= 100:
        raise HTTPException(status_code=422, detail="분석 예산은 0달러보다 크고 100달러 이하여야 합니다")
    if request.sort not in ("recent", "helpful", "random"):
        raise HTTPException(status_code=422, detail="정렬은 최신순 또는 공감순만 고를 수 있습니다")
    if request.since:
        try:
            since_day = datetime.strptime(request.since, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=422, detail="수집 시작 날짜 형식은 YYYY-MM-DD입니다")
        if since_day > datetime.now():
            raise HTTPException(status_code=422, detail="수집 시작 날짜가 오늘보다 늦습니다")
    from model_catalog import get_model
    try:
        model = get_model(request.model or cfg.MODEL)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"모델 가격을 확인할 수 없습니다: {exc}") from exc
    if model is None:
        raise HTTPException(status_code=422, detail="현재 OpenRouter 목록에 없는 모델입니다")

    def run_and_publish():
        result = run_pipeline(
            app_id=request.app_id, lang=request.lang, budget=request.budget,
            target_error_pct=request.target_error_pct, model=model["id"],
            custom_sample_size=request.custom_sample_size, incremental=request.incremental,
            since=request.since, sort=request.sort,
        )
        if result.get("status") != "done" or (result.get("steps", {}).get("insights") or {}).get("status") != "done":
            return
        games = _load_games()
        if any(str(g.get("app_id")) == str(request.app_id) for g in games):
            return
        try:
            info = search_steam_game(request.app_id)
        except Exception:
            info = {"app_id": request.app_id, "name": f"App {request.app_id}",
                    "header_image": "", "type": "game", "short_description": ""}
        games.append(info)
        _save_games(games)

    background_tasks.add_task(
        run_and_publish
    )
    return {"status": "started", "message": "파이프라인이 백그라운드에서 실행되었습니다."}


def kept_work(folder):
    """멈춘 분석이 폴더에 남겨 둔 것. 화면이 "여기까지 남아 있습니다"로 보여 준다."""
    def lines(name):
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8") as stream:
                return sum(1 for line in stream if line.strip())
        except OSError:
            return 0
    try:
        with open(os.path.join(folder, "reviews.csv"), "r", encoding="utf-8-sig", newline="") as stream:
            collected = max(0, sum(1 for _ in csv.reader(stream)) - 1)   # 머리줄 제외
    except OSError:
        collected = 0
    kept = {"collected": collected,
            "classified": lines("analysis_v3.jsonl"), "deep": lines("complaints_v3.jsonl"),
            "themes": os.path.exists(os.path.join(folder, "themes_v3.json")), "scanned": 0, "scan_total": None}
    if not kept["collected"]:
        try:
            with open(os.path.join(folder, "scan_state.json"), "r", encoding="utf-8") as stream:
                kept["scanned"] = int(json.load(stream).get("count") or 0)
            kept["scan_total"] = (progress.read(folder) or {}).get("total")
        except (OSError, ValueError):
            pass
    return kept


@app.get(
    "/pipeline/result",
    summary="마지막 파이프라인 결과",
    description="가장 최근 파이프라인 실행 결과를 반환합니다.",
)
def pipeline_last_result(app_id: Optional[int] = None):
    # app_id를 주면 그 게임을 본다. 서버를 다시 켜면 cfg는 기본 게임을 가리키므로
    # 화면이 보고 있는 게임과 달라질 수 있다.
    # 폴더를 만들지 않고 본다. 조회만으로 빈 프로젝트 폴더가 생기면 안 된다.
    result_path = os.path.join(cfg.PROJECTS_DIR, str(int(app_id)), "pipeline_result.json") if app_id else cfg.PIPELINE_RESULT

    if not os.path.exists(result_path):
        return {"status": "no_runs", "message": "아직 파이프라인이 실행된 적 없습니다."}
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    elapsed = None
    started = data.get("started_at")
    if started:
        try:
            end = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else datetime.now()
            elapsed = max(0, int((end - datetime.fromisoformat(started)).total_seconds()))
        except Exception:
            elapsed = None

    # 기록은 "진행 중"인데 이 프로그램 안에서 돌고 있지 않으면, 프로그램이 꺼지면서 끊긴 것이다.
    import pipeline
    if data.get("status") == "running" and pipeline.RUNNING["app_id"] != (data.get("request") or {}).get("app_id", app_id):
        data["status"] = "failed"
        data["error"] = "프로그램이 꺼지면서 분석이 끊겼습니다."
    data["kept"] = kept_work(os.path.dirname(result_path))
    data["live"] = {
        "elapsed_sec": elapsed,
        "progress": progress.read(os.path.dirname(result_path)),   # 단계별 건수
    }
    return data
