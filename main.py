"""Steam 리뷰 수집·분석 대시보드 서버.

실행: uvicorn main:app --host 127.0.0.1 --port 8765
문서: http://127.0.0.1:8765/docs
대시보드: http://127.0.0.1:8765/dashboard

데이터 출처 및 라이선스:
- Steam 리뷰 데이터: Valve Steam Web API (공개 API, 비상업적 분석 목적)
- LLM 분석: OpenRouter API (선택 모델)
- 본 프로젝트는 교육·포트폴리오 목적으로 제작되었습니다.
"""

import os, csv, json
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import cfg
from dashboard_evidence import build_evidence, evidence_page, load_sources, review_hours
import analysis_design
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
# 대시보드 (Tailwind + Chart.js 기반 웹 어플리케이션 UI)
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
    return {"games": visible}


@app.delete("/api/games/{app_id}", summary="프로젝트 삭제", include_in_schema=False)
def trash_game(app_id: int):
    """프로젝트를 목록에서 빼고 폴더를 휴지통으로 옮깁니다.

    지우지 않고 옮기기만 하므로 사용자가 직접 되돌릴 수 있습니다.
    """
    games = _load_games()
    remaining = [game for game in games if str(game.get("app_id")) != str(app_id)]
    if len(remaining) == len(games):
        raise HTTPException(status_code=404, detail="해당 프로젝트를 찾지 못했습니다.")

    source = _game_dir(app_id)
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
    import httpx, math
    if language not in REVIEW_LANGUAGES:
        raise HTTPException(status_code=422, detail="지원하지 않는 리뷰 언어입니다")
    url = f"https://store.steampowered.com/appreviews/{app_id}"
    try:
        # 선택 언어의 리뷰 통계
        r_kr = httpx.get(url, params={
            "json": 1, "filter": "recent", "language": language,
            "review_type": "all", "purchase_type": "all",
            "num_per_page": 0, "filter_offtopic_activity": 0,
        }, timeout=15.0)
        r_kr.raise_for_status()
        qs_kr = r_kr.json().get("query_summary", {})

        # 전체 언어 리뷰 통계
        r_all = httpx.get(url, params={
            "json": 1, "filter": "recent", "language": "all",
            "review_type": "all", "purchase_type": "all",
            "num_per_page": 0, "filter_offtopic_activity": 0,
        }, timeout=15.0)
        r_all.raise_for_status()
        qs_all = r_all.json().get("query_summary", {})

        total_kr = qs_kr.get("total_reviews", 0)
        pos_kr = qs_kr.get("total_positive", 0)
        neg_kr = qs_kr.get("total_negative", 0)
        total_all = qs_all.get("total_reviews", 0)
        score = qs_all.get("review_score_desc", "")

        # 수집기와 같은 보수적 p=0.5: 특정 게임의 추천율 외 다른 비율에도 적용할 계획 건수.
        p_val = 0.5

        neg_rate_raw = neg_kr / total_kr if total_kr > 0 else 0.5

        def cochran_n(N, p=0.5, z=1.96, e=0.05):
            n0 = (z**2 * p * (1-p)) / (e**2)
            return math.ceil(n0 / (1 + (n0-1)/N)) if N > 0 else 0

        MIN_NEG = cfg.MIN_NEG_REVIEWS

        sample_5pct = cochran_n(total_kr, p=p_val, e=0.05) if total_kr > 0 else 0
        sample_3pct = cochran_n(total_kr, p=p_val, e=0.03) if total_kr > 0 else 0

        # 부정 리뷰 최소 100건 확보에 필요한 총 수집량
        n_for_neg = math.ceil(MIN_NEG / neg_rate_raw) if neg_rate_raw > 0 else sample_5pct

        # 실제 수집 목표: 두 조건 중 더 큰 값 (collect_reviews.decide_sample_size와 동일)
        actual_5pct = min(max(sample_5pct, n_for_neg), total_kr)
        actual_3pct = min(max(sample_3pct, n_for_neg), total_kr)

        # 어떤 조건이 수집량을 결정했는지
        driver_5 = "MIN_NEG" if n_for_neg > sample_5pct else "Cochran"
        driver_3 = "MIN_NEG" if n_for_neg > sample_3pct else "Cochran"

        return {
            "app_id": app_id,
            "global": {
                "total": total_all,
                "score": score,
            },
            "selected": {
                "total": total_kr,
                "positive": pos_kr,
                "negative": neg_kr,
                "pos_rate": round(pos_kr / total_kr * 100, 1) if total_kr > 0 else 0,
                "neg_rate": round(neg_kr / total_kr * 100, 1) if total_kr > 0 else 0,
            },
            "language": language,
            "korean": {"total": total_kr, "positive": pos_kr, "negative": neg_kr,
                       "pos_rate": round(pos_kr / total_kr * 100, 1) if total_kr else 0,
                       "neg_rate": round(neg_kr / total_kr * 100, 1) if total_kr else 0} if language == "koreana" else None,
            "sample_design": {
                # Cochran 순수 통계 최솟값
                "sample_5pct": actual_5pct,
                "sample_3pct": actual_3pct,
                "p_applied": round(p_val, 4),
                # 실제 수집 결정 근거
                "cochran_5pct": sample_5pct,
                "cochran_3pct": sample_3pct,
                "n_for_neg": n_for_neg,
                "min_neg_required": MIN_NEG,
                "driver_5pct": driver_5,
                "driver_3pct": driver_3,
            },
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
def download_reviews_csv(app_id: int = None):
    """수집된 리뷰 데이터를 CSV로 다운로드합니다."""
    from fastapi.responses import FileResponse
    import os
    
    csv_path = cfg.project_file("reviews.csv", app_id)
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="리뷰 CSV 파일을 찾을 수 없습니다")
    
    filename = f"reviews_{app_id or 'all'}.csv"
    return FileResponse(csv_path, media_type="text/csv", filename=filename)


@app.get("/api/analysis/download", summary="분석 결과 CSV 다운로드", include_in_schema=False)
def download_analysis_csv(app_id: int = None):
    """AI 분석 결과를 CSV로 다운로드합니다."""
    from fastapi.responses import FileResponse
    import os
    
    csv_path = cfg.project_file("analysis_v3.csv", app_id)
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="분석 CSV 파일을 찾을 수 없습니다")
    
    filename = f"analysis_{app_id or 'all'}.csv"
    return FileResponse(csv_path, media_type="text/csv", filename=filename)

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
        data = json.load(f)
    # 이전 버전의 플레이 구간별 오차는 비무작위/가중 표본에 적용할 수 없으므로 노출하지 않는다.
    for cohort in data.get("playtime", []):
        cohort.pop("margin", None)
    data["game"] = {
        "app_id": app_id,
        "name": (game_info.get("name_kr") or game_info.get("name")) if game_info else f"App {app_id}",
        "header_image": (game_info or {}).get("header_image", ""),
    }
    data["evidence"] = build_evidence(os.path.dirname(path), app_id)
    supporting_files = {
        "sample_design_full": "sample_design.json",
        "quality_report": "quality_report.json",
    }
    for key, filename in supporting_files.items():
        source = _game_file(app_id, filename)
        if source:
            try:
                with open(source, "r", encoding="utf-8") as stream:
                    data[key] = json.load(stream)
            except (OSError, ValueError):
                data[key] = None
        else:
            data[key] = None
    if data.get("quality_report"):
        import quality_check
        data["quality_report"] = quality_check.rescore(data["quality_report"])
    data["reviews"] = review_rows(os.path.dirname(path))
    apply_design(data)
    return data


def review_rows(directory):
    """Build review cards from the same source files used for evidence.

    The optional analysis_v3.csv can be absent after a resumed analysis and its
    content column is truncated. reviews.csv keeps the complete original text.
    """
    source = load_sources(directory)
    if not source:
        return []
    reviews, analyzed = source[:2]
    sentiments = {"P": "POSITIVE", "N": "NEGATIVE", "M": "MIXED", "U": "NEUTRAL"}
    area_names = {"graphics": "content", "gameplay": "content", "story": "content",
                  "performance": "technical", "value": "value"}
    areas = {}
    themes_path = os.path.join(directory, "themes_v3.json")
    if os.path.exists(themes_path):
        with open(themes_path, encoding="utf-8") as stream:
            areas = {theme["name"]: area_names.get(theme.get("area"), "content")
                     for theme in json.load(stream) if theme.get("name")}
    # Older projects may only have area tags in the optional analysis CSV.
    legacy_tags = {}
    analysis_path = os.path.join(directory, "analysis_v3.csv")
    if os.path.exists(analysis_path):
        with open(analysis_path, encoding="utf-8-sig", newline="") as stream:
            legacy_tags = {row["recommendationid"]: row.get("keywords", "")
                           for row in csv.DictReader(stream)}
    rows = []
    for rid, item in analyzed.items():
        original = reviews[rid]
        tags = [pair[0] for pair in item.get("t") or []
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] != "기타"]
        for tag in legacy_tags.get(rid, "").split("|"):
            name, _, area = tag.partition("@")
            if name and area:
                areas.setdefault(name, area)
        hours = review_hours(original)
        rows.append({
            "recommendationid": rid,
            "content": original.get("content", ""),
            "voted_up": original.get("voted_up", ""),
            "playtime_h": hours,
            "overall_sentiment": sentiments.get(item.get("s"), "NEUTRAL"),
            "key_phrase": item.get("k", ""),
            "keywords": "|".join(f"{name}@{areas[name]}" if name in areas else name
                                  for name in dict.fromkeys(tags)),
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
            tags = []
            for tag in str(row.get("keywords") or "").split("|"):
                name, _, area = tag.partition("@")
                name = alias.get(name.strip(), name.strip())
                if name and not any(t.startswith(name + "@") for t in tags):
                    tags.append(f"{name}@{area}" if area else name)
            row["keywords"] = "|".join(tags)
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


@app.get(
    "/pipeline/estimate",
    summary="비용 사전 견적",
    description="현재 리뷰 데이터 기준 LLM 분석 비용을 미리 계산합니다.",
)
def pipeline_estimate(app_id: int = None):
    from token_budget import estimate_remaining
    return estimate_remaining(cfg, app_id)


@app.get(
    "/pipeline/config",
    summary="현재 설정 조회",
    description="App ID, 모델, 예산 등 현재 파이프라인 설정을 반환합니다.",
)
def pipeline_config():
    return {
        **cfg.summary(),
        "game_name": cfg.get_game_name(),
        "budget_usd": cfg.BUDGET_USD,
        "reviews_csv_exists": os.path.exists(cfg.REVIEWS_CSV),
        "analysis_csv_exists": os.path.exists(cfg.ANALYSIS_CSV),
        "insights_json_exists": os.path.exists(cfg.project_file("insights_v5.json")),
    }


def _count_csv_rows(path: str) -> int:
    """CSV 데이터 행 수. 리뷰 본문에 줄바꿈이 있으므로 줄 수로 세면 안 된다."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # 헤더
            return sum(1 for _ in reader)
    except Exception:
        return 0


@app.get(
    "/pipeline/result",
    summary="마지막 파이프라인 결과",
    description="가장 최근 파이프라인 실행 결과를 반환합니다.",
)
def pipeline_last_result(app_id: Optional[int] = None):
    # app_id를 주면 그 게임을 본다. 서버를 다시 켜면 cfg는 기본 게임을 가리키므로
    # 화면이 보고 있는 게임과 달라질 수 있다.
    if app_id:
        folder = cfg.project_dir(app_id)
        result_path = os.path.join(folder, "pipeline_result.json")
        analysis_csv = os.path.join(folder, "analysis_v3.csv")
        reviews_csv = os.path.join(folder, "reviews.csv")
    else:
        result_path = cfg.PIPELINE_RESULT
        analysis_csv = cfg.ANALYSIS_CSV
        reviews_csv = cfg.REVIEWS_CSV

    if not os.path.exists(result_path):
        return {"status": "no_runs", "message": "아직 파이프라인이 실행된 적 없습니다."}
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 분석 단계는 오래 걸린다. 지금 몇 건까지 했는지 보여줄 수 있어야
    # 멈춘 것인지 도는 것인지 구분된다.
    analyzed = _count_csv_rows(analysis_csv)
    collected = _count_csv_rows(reviews_csv)

    target = 0
    steps = data.get("steps") or {}
    cost = (steps.get("cost_estimate") or {}).get("detail") or {}
    if isinstance(cost.get("n_reviews"), int):
        target = cost["n_reviews"]
    if not target:
        target = collected

    elapsed = None
    started = data.get("started_at")
    if started:
        try:
            end = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else datetime.now()
            elapsed = max(0, int((end - datetime.fromisoformat(started)).total_seconds()))
        except Exception:
            elapsed = None

    data["live"] = {
        "collected": collected,
        "analyzed": analyzed,
        "analyze_target": target,
        "analyze_pct": round(analyzed / target * 100, 1) if target else 0.0,
        "elapsed_sec": elapsed,
        "progress": progress.read(os.path.dirname(result_path)),   # 단계별 건수
    }
    return data
