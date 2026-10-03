"""원클릭 자동화 파이프라인

사용법:
  CLI:   python pipeline.py
  CLI:   python pipeline.py --app-id 730 --lang english
  API:   POST /pipeline/run  {"app_id": 1623730}

5단계 분석을 한 번에 실행:
  ① 모집단 조회 + 표본 설계
  ② 리뷰 수집 (Steam API)
  ③ LLM 다차원 분석 (비용 사전 견적)
  ④ 품질 점검
  ⑤ 신뢰도 검증
  ⑥ 인사이트 요약 생성
"""

import os
import sys
import json
import argparse
import threading
import shutil
from datetime import datetime

# Windows 콘솔 UTF-8 출력 (cp949 인코딩 에러 방지)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import cfg
import progress
from budget_control import activate as activate_budget, clear as clear_budget


class PipelineResult:
    """파이프라인 실행 결과"""
    def __init__(self):
        self.job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.started_at = datetime.now().isoformat()
        self.steps = {}
        self.status = "running"
        self.error = None

    def record(self, step: str, status: str, detail: dict = None):
        self.steps[step] = {
            "status": status,
            "detail": detail or {},
            "timestamp": datetime.now().isoformat(),
        }
        if status == "failed":
            self.status = "failed"
        self.save()

    def save(self):
        """화면이 읽는 진행 기록. 시작하자마자 써야 이전 실행의 '완료'를 읽지 않는다."""
        try:
            out_path = cfg.PIPELINE_RESULT
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def finish(self):
        if self.status != "failed":
            self.status = "done"
        self.finished_at = datetime.now().isoformat()

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": getattr(self, "finished_at", None),
            "config": cfg.summary(),
            "steps": self.steps,
            "error": self.error,
        }


def step_cost_estimate(result: PipelineResult) -> bool:
    """Remaining classification, complaint detail, and summary budget."""
    from token_budget import estimate_remaining

    print("\n" + "=" * 60)
    print("💰 [Step 0] 비용 사전 견적")
    print("=" * 60)

    est = estimate_remaining(cfg)
    if not est["to_analyze"] and not est["to_deep"] and not est["estimated_input_tokens"]:
        print(f"  ✅ 남은 AI 분석 없음 (전체 {est['n_reviews']}건, 분류 완료 {est['already_done']}건)")
        result.record("cost_estimate", "skipped", est)
        return True

    print(f"  새 분류 {est['to_analyze']}건 · 남은 불만 심층 {est['to_deep']}건 · 새 불만 심층 예상 {est['expected_new_deep']}건")
    print(f"  모델: {est['model']}")
    print(f"  예상 비용: ${est['estimated_usd']:.4f}")
    print(f"  예산 한도: ${est['budget_usd']:.2f}")

    if est["within_budget"]:
        print(f"  ✅ 예산 범위 내 — 분석 진행")
        result.record("cost_estimate", "done", est)
        return True
    else:
        print(f"  ❌ 예산 초과! 분석을 중단합니다.")
        print(f"     .env에서 BUDGET_USD를 올리거나, 리뷰 수를 줄이세요.")
        result.record("cost_estimate", "budget_exceeded", est)
        return False


def step_collect(result: PipelineResult):
    """Step 1: 리뷰 수집"""
    print("\n" + "=" * 60)
    print("📥 [Step 1] Steam 리뷰 수집")
    print("=" * 60)

    # 1) 실시간 모집단 및 Cochran 표본 설계 사이즈 계산
    target_size = 379  # 기본값 fallback
    try:
        import collect_reviews
        pop = collect_reviews.fetch_population()
        design = collect_reviews.decide_sample_size(pop)
        target_size = design["n_total"]
        print(f"  목표 표본 크기: {target_size}건 (Cochran 공식 산출)")
    except Exception as e:
        print(f"  ⚠️ 표본 설계 계산 실패 (Steam API 에러 등): {e}. 기본 379건으로 진행합니다.")

    # 2) 기존 수집 파일 유효성 검사
    existing_count = 0
    existing_language = None
    existing_scope = None
    if os.path.exists(cfg.REVIEWS_CSV):
        import csv
        try:
            with open(cfg.REVIEWS_CSV, "r", encoding="utf-8-sig", newline="") as f:
                existing_count = sum(1 for _ in csv.DictReader(f))
        except Exception:
            existing_count = 0
    if os.path.exists(cfg.SAMPLE_JSON):
        try:
            with open(cfg.SAMPLE_JSON, "r", encoding="utf-8") as stream:
                existing_params = json.load(stream).get("params") or {}
                existing_language = existing_params.get("language")
                existing_scope = (existing_params.get("since"), existing_params.get("sort") or "recent")
        except (OSError, ValueError):
            pass
    incremental_mode = getattr(result, "incremental", False)
    if incremental_mode and existing_count and existing_language != cfg.LANG:
        raise ValueError("다른 언어의 기존 리뷰에는 이어서 수집할 수 없습니다. 새 분석으로 시작하세요")

    # 3) 기존 수집량이 목표량을 채운 경우에만 수집 단계를 건너뜀 (장애 재개 용도)
    same_scope = existing_scope == (cfg.COLLECT_SINCE, cfg.COLLECT_SORT or "recent")
    if incremental_mode and existing_count and not same_scope:
        raise ValueError("기간이나 정렬이 다른 기존 리뷰에는 이어서 수집할 수 없습니다. 새 분석으로 시작하세요")
    if existing_count >= target_size and existing_language == cfg.LANG and same_scope and not incremental_mode:
        print(f"  ✅ 유효한 기존 리뷰 파일 존재 ({existing_count}건, 목표 {target_size}건 충족) — 수집 스킵")
        result.record("collect", "skipped", {"existing_reviews": existing_count, "target_reviews": target_size})
        return

    # 4) 표본 설계가 달라졌거나 기존 데이터가 너무 적은 경우 새로 수집
    cfg.INCREMENTAL = incremental_mode
    if incremental_mode:
        print("  🔄 [증분 분석 모드] 기존 리뷰 데이터를 보존하고, 신규 리뷰를 추가 수집합니다.")
    elif existing_count > 0:
        print(f"  🔄 기존 리뷰 수({existing_count}건)가 목표({target_size}건)에 미치지 못하므로 새로 전체 수집을 속행합니다.")
    
    # 새 수집의 이전 산출물은 보관한다. 다른 언어의 주제/분류를 섞지 않는다.
    if not incremental_mode and existing_count:
        names = ("reviews.csv", "sample_design.json", "themes_v3.json", "analysis_v3.jsonl",
                 "complaints_v3.jsonl", "analysis_v3.csv", "insights_v5.json",
                 "summary_v5_cache.json", "usage_v3.json", "quality_report.json", "verify_report.json",
                 "verify_set.csv")
        backup_dir = os.path.join(cfg.project_dir(), "previous-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(backup_dir, exist_ok=True)
        for name in names:
            source = os.path.join(cfg.project_dir(), name)
            if os.path.isfile(source):
                shutil.move(source, os.path.join(backup_dir, name))

    print(f"  App ID: {cfg.APP_ID}")
    print(f"  언어: {cfg.LANG}")

    try:
        from collect_reviews import main as collect_main
        collect_main()
        result.record("collect", "done", {"app_id": cfg.APP_ID, "target_reviews": target_size})
    except Exception as e:
        print(f"  ❌ 수집 실패: {e}")
        result.record("collect", "failed", {"error": str(e)})
        raise


def step_analyze(result: PipelineResult):
    """Step 2: LLM 다차원 분석"""
    print("\n" + "=" * 60)
    print("🤖 [Step 2] LLM 다차원 분석")
    print("=" * 60)

    if not os.path.exists(cfg.REVIEWS_CSV):
        raise FileNotFoundError("reviews.csv가 없습니다. 먼저 수집을 실행하세요.")

    # A different model must actually reclassify the game's reviews.
    usage_path = cfg.project_file("usage_v3.json")
    if os.path.exists(usage_path) and os.path.exists(cfg.project_file("analysis_v3.jsonl")):
        try:
            with open(usage_path, "r", encoding="utf-8") as stream:
                previous_model = json.load(stream).get("model")
        except (OSError, ValueError):
            previous_model = None
        if previous_model and previous_model != cfg.MODEL:
            backup_dir = os.path.join(cfg.project_dir(), "previous-model-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            os.makedirs(backup_dir, exist_ok=True)
            for name in ("themes_v3.json", "analysis_v3.jsonl", "complaints_v3.jsonl",
                         "analysis_v3.csv", "insights_v5.json",
                         "summary_v5_cache.json", "usage_v3.json", "quality_report.json", "verify_report.json",
                         "verify_set.csv"):
                source = os.path.join(cfg.project_dir(), name)
                if os.path.isfile(source):
                    shutil.move(source, os.path.join(backup_dir, name))
            print(f"  분석 모델 변경: 이전 결과 보관 후 {cfg.MODEL}로 다시 분류합니다")

    try:
        # v3: 게임별 주제 + 재미 종류 + 불만 심층을 한 번의 분류 흐름으로 만든다.
        from analyze_reviews_v3 import main as analyze_main
        analyze_main()
        result.record("analyze", "done")
    except Exception as e:
        print(f"  ❌ 분석 실패: {e}")
        result.record("analyze", "failed", {"error": str(e)})
        raise


def step_quality(result: PipelineResult):
    """Step 3: 품질 점검"""
    print("\n" + "=" * 60)
    print("🔍 [Step 3] 데이터 품질 점검")
    print("=" * 60)

    try:
        from quality_check import main as quality_main
        quality_main()
        # 결과 읽기
        if os.path.exists(cfg.QUALITY_JSON):
            with open(cfg.QUALITY_JSON, "r", encoding="utf-8") as f:
                qr = json.load(f)
            score = qr.get("overall_score", 0)
            grade = qr.get("grade", "UNKNOWN")
            print(f"  종합 점수: {score} ({grade})")
            result.record("quality", "done", {"score": score, "grade": grade})
        else:
            result.record("quality", "done")
    except Exception as e:
        print(f"  ❌ 품질 점검 실패: {e}")
        result.record("quality", "failed", {"error": str(e)})
        raise


def step_verify(result: PipelineResult):
    """Step 4: 추천 여부와 AI 감성 분류의 일치 점검"""
    print("\n" + "=" * 60)
    print("🎯 [Step 4] 추천 여부와 AI 분류 일치 점검")
    print("=" * 60)

    verify_set = cfg.project_file("verify_set.csv")
    if (os.path.exists(cfg.VERIFY_JSON) and os.path.exists(verify_set)
            and os.path.getmtime(verify_set) >= os.path.getmtime(cfg.ANALYSIS_CSV)):
        result.record("verify", "skipped", {"reason": "기존 분석 검증 재사용"})
        return
    try:
        from verify_analysis import main as verify_main
        verify_main()
        if os.path.exists(cfg.VERIFY_JSON):
            with open(cfg.VERIFY_JSON, "r", encoding="utf-8") as f:
                vr = json.load(f)
            agreement = vr.get("agreement_or_mixed_pct", vr.get("accuracy_lenient_pct", 0))
            print(f"  추천 여부 일치 또는 혼합/중립: {agreement}%")
            result.record("verify", "done", {"agreement_or_mixed_pct": agreement})
        else:
            result.record("verify", "done")
    except Exception as e:
        print(f"  ❌ 신뢰도 검증 실패: {e}")
        result.record("verify", "failed", {"error": str(e)})
        raise


def step_insights(result: PipelineResult):
    """Step 5: 인사이트 요약 생성"""
    print("\n" + "=" * 60)
    print("📊 [Step 5] 인사이트 요약 생성")
    print("=" * 60)

    try:
        from build_insights_v5 import main as insights_v5
        insights_v5()
    except Exception as e:
        print(f"  ❌ 인사이트 생성 실패: {e}")
        result.record("insights", "failed", {"error": str(e)})
        raise

    result.record("insights", "done")


_PIPELINE_LOCK = threading.Lock()


def run_pipeline(app_id: int = None, lang: str = None, budget: float = None, target_error_pct: float = None, custom_sample_size: int = None, incremental: bool = False, model: str = None, since: str = None, sort: str = "recent") -> dict:
    # The analyzer and usage counters share process-wide configuration.
    with _PIPELINE_LOCK:
        return _run_pipeline_unlocked(app_id, lang, budget, target_error_pct,
                                      custom_sample_size, incremental, model, since, sort)


def _run_pipeline_unlocked(app_id: int = None, lang: str = None, budget: float = None, target_error_pct: float = None, custom_sample_size: int = None, incremental: bool = False, model: str = None, since: str = None, sort: str = "recent") -> dict:
    """전체 파이프라인 실행

    Args:
        app_id: Steam App ID (None이면 config 기본값)
        lang: 리뷰 언어 (None이면 config 기본값)
        budget: 예산 한도 USD (None이면 config 기본값)
        target_error_pct: 오차한계 % (None이면 기본값)
        custom_sample_size: 사용자 지정 수집 수 (None이면 기본값)
        incremental: 기존 수집/분석 데이터에 증분으로 덧붙여서 분석 진행 여부

    Returns:
        파이프라인 실행 결과 dict
    """
    # 런타임 설정 오버라이드
    if app_id is not None:
        cfg.APP_ID = app_id
    if lang is not None:
        cfg.LANG = lang
    if budget is not None:
        cfg.BUDGET_USD = budget
    if target_error_pct is not None:
        cfg.TARGET_ERROR_PCT = target_error_pct
    if model:
        cfg.MODEL = model
    if custom_sample_size is not None:
        cfg.CUSTOM_SAMPLE_SIZE = custom_sample_size
    else:
        cfg.CUSTOM_SAMPLE_SIZE = None  # Reset if not explicitly requested
    cfg.COLLECT_SINCE = since or None
    cfg.COLLECT_SORT = sort if sort in ("helpful", "random") else "recent"

    result = PipelineResult()
    result.incremental = incremental
    progress.report("collect", 0, None, force=True)   # 이전 실행의 진행 기록을 지운다
    result.save()

    game_name = cfg.get_game_name()
    print("╔" + "═" * 58 + "╗")
    print(f"║  🎮 {game_name} (AppID: {cfg.APP_ID})")
    print(f"║  📋 언어: {cfg.LANG} | 모델: {cfg.MODEL}")
    print(f"║  💰 예산: ${cfg.BUDGET_USD:.2f}")
    print("╚" + "═" * 58 + "╝")

    try:
        from model_catalog import get_model
        model_info = get_model(cfg.MODEL)
        if model_info is None:
            raise ValueError("현재 OpenRouter에서 선택한 모델을 찾을 수 없습니다")
        cfg.MODEL_COST_INPUT = model_info["input_cost"]
        cfg.MODEL_COST_OUTPUT = model_info["output_cost"]
        cfg.MODEL_JSON_MODE = model_info["json_schema"]
        cfg.MODEL_FREE = model_info["free"]   # 무료 모델은 분당 20회 한도에 맞춰 천천히 보낸다
        cfg.MODEL_CONTEXT_LENGTH = model_info["context_length"]
        activate_budget(model_info, cfg.BUDGET_USD)
        # Step 1: 리뷰 수집 (먼저 수행해야 견적 가능)
        step_collect(result)

        # Step 0(-> 1.5): 비용 견적 (예산 초과 시 중단)
        if not step_cost_estimate(result):
            result.status = "failed"
            result.error = "설정한 분석 예산보다 예상 비용이 큽니다. 수집량이나 모델을 조정하세요."
            result.finish()
            return result.to_dict()

        # Step 2~5: 파이프라인 실행
        step_analyze(result)
        progress.report("finish", force=True)
        step_quality(result)
        step_verify(result)
        step_insights(result)

    except Exception as e:
        result.status = "failed"
        result.error = str(e)
    finally:
        clear_budget()
        result.finish()

    # 결과 저장
    out_path = cfg.PIPELINE_RESULT
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

    # 최종 요약
    print("\n" + "╔" + "═" * 58 + "╗")
    print(f"║  파이프라인 완료: {result.status.upper()}")
    for step_name, step_data in result.steps.items():
        icon = "✅" if step_data["status"] == "done" else "⏭️" if step_data["status"] == "skipped" else "❌"
        print(f"║  {icon} {step_name}: {step_data['status']}")
    print("╚" + "═" * 58 + "╝")

    return result.to_dict()


def main():
    parser = argparse.ArgumentParser(description="Steam 리뷰 분석 자동화 파이프라인")
    parser.add_argument("--app-id", type=int, default=None,
                        help=f"Steam App ID (기본: {cfg.APP_ID})")
    parser.add_argument("--lang", type=str, default=None,
                        help=f"리뷰 언어 (기본: {cfg.LANG})")
    parser.add_argument("--budget", type=float, default=None,
                        help=f"LLM 예산 USD (기본: ${cfg.BUDGET_USD})")
    args = parser.parse_args()

    run_pipeline(app_id=args.app_id, lang=args.lang, budget=args.budget)


if __name__ == "__main__":
    main()
