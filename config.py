"""중앙 설정 — 모든 스크립트가 이 파일에서 설정을 가져옴

사용법:
    from config import cfg
    print(cfg.APP_ID, cfg.LANG, cfg.MODEL)

환경변수(.env)로 오버라이드 가능:
    APP_ID=730  → CS2 분석
    LANG=english → 영어 리뷰 분석
"""

import os
from pathlib import Path
from dotenv import load_dotenv


# The tool follows the same boundary as the other desktop tools:
# executable/source files live in ``프로그램`` and user-owned data lives in
# one sibling ``프로젝트`` store.  All modules should resolve files through
# this object instead of relying on the process working directory.
PROGRAM_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = PROGRAM_DIR.parent / "프로젝트"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(PROGRAM_DIR / ".env")


class Config:
    """프로젝트 전역 설정"""

    # ── Steam 수집 ──────────────────────────────────────────
    APP_ID: int = int(os.getenv("APP_ID", "1623730"))  # 기본: Palworld
    LANG: str = os.getenv("LANG_CODE", "koreana")
    STEAM_API_URL: str = "https://store.steampowered.com/appreviews/{appid}"

    # ── LLM 분석 ────────────────────────────────────────────
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_URL: str = "https://openrouter.ai/api/v1/chat/completions"
    MODEL: str = os.getenv("MODEL", "google/gemini-2.5-flash-lite")

    # 비용 설정 (USD per 1M tokens 기준)
    MODEL_COST_INPUT: float = float(os.getenv("MODEL_COST_INPUT", "0.10"))   # $/1M input tokens
    MODEL_COST_OUTPUT: float = float(os.getenv("MODEL_COST_OUTPUT", "0.40")) # $/1M output tokens
    BUDGET_USD: float = float(os.getenv("BUDGET_USD", "5.0"))  # 기본 예산 $5

    # ── 표본 설계 ───────────────────────────────────────────
    TARGET_ERROR_PCT: float = float(os.getenv("TARGET_ERROR_PCT", "5"))
    MIN_NEG_REVIEWS: int = int(os.getenv("MIN_NEG_REVIEWS", "100"))
    MIN_REVIEW_LEN: int = int(os.getenv("MIN_REVIEW_LEN", "2"))
    CUSTOM_SAMPLE_SIZE: int = None
    COLLECT_SINCE: str = None      # YYYY-MM-DD. 이 날짜 이후 리뷰만 모은다 (None이면 전체 기간)
    COLLECT_SORT: str = "recent"   # recent 최신순 · helpful 공감순 · random 무작위

    # ── 파일 경로 ───────────────────────────────────────────
    PROGRAM_DIR: str = str(PROGRAM_DIR)
    PROJECTS_DIR: str = str(PROJECTS_DIR)
    GAMES_JSON: str = os.path.join(PROJECTS_DIR, "games.json")

    def project_dir(self, app_id: int | None = None) -> str:
        """Return (and create) the single per-game project directory."""
        value = self.APP_ID if app_id is None else int(app_id)
        if value <= 0:
            raise ValueError("APP_ID는 양수여야 합니다.")
        path = os.path.join(self.PROJECTS_DIR, str(value))
        os.makedirs(path, exist_ok=True)
        return path

    def project_file(self, filename: str, app_id: int | None = None) -> str:
        return os.path.join(self.project_dir(app_id), filename)

    @property
    def REVIEWS_CSV(self) -> str:
        return self.project_file("reviews.csv")

    @property
    def SAMPLE_JSON(self) -> str:
        return self.project_file("sample_design.json")

    @property
    def PIPELINE_RESULT(self) -> str:
        return self.project_file("pipeline_result.json")

    def get_game_name(self) -> str:
        """Steam Store API에서 게임 이름을 가져옵니다."""
        import httpx
        try:
            r = httpx.get(
                f"https://store.steampowered.com/api/appdetails",
                params={"appids": self.APP_ID, "l": "korean"},
                timeout=10.0,
            )
            data = r.json().get(str(self.APP_ID), {}).get("data", {})
            return data.get("name", f"App {self.APP_ID}")
        except Exception:
            return f"App {self.APP_ID}"

    def summary(self) -> dict:
        """현재 설정 요약 (디버그/로그용)"""
        return {
            "app_id": self.APP_ID,
            "lang": self.LANG,
            "model": self.MODEL,
            "budget_usd": self.BUDGET_USD,
            "target_error_pct": self.TARGET_ERROR_PCT,
        }


cfg = Config()
