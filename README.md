# AI 리뷰데이터 분석기

Steam 리뷰를 표본 수집하고 OpenRouter 모델로 분류해, 게임기획자가 **전체 반응 → 핵심 주제 → 플레이 구간 → 실제 원문** 순서로 확인하는 로컬 분석 도구입니다.

## 현재 구조

```text
Steam 리뷰 API
  → 표본 설계·수집
  → v3 리뷰 분류
     A. 게임별 주제 발견
     B. 전체 리뷰 반응·주제·재미 분류
     C. 불만 리뷰만 문제·원인·요청 심층 분석
  → 요약과 할 일 (AI 한 번)
  → 대시보드: 건수·비율·자료 상태 점검은 화면을 열 때 원본에서 다시 계산
```

분석 결과 화면은 특정 게임에 맞춘 고정 문구를 사용하지 않습니다. Steam 게임 정보와 분석 파일을 기준으로 같은 구조를 모든 게임에 적용합니다.

## 주요 기능

- Steam App ID와 언어를 지정한 리뷰 수집
- 목표 오차와 최소 비추천 리뷰 수를 반영한 표본 설계
- 게임마다 반복되는 주제를 먼저 찾는 동적 주제 분류
- 긍정·부정·혼합·판단 어려움의 반응 분류
- 8가지 재미 유형과 주제별 긍정·부정 언급 집계
- 불만 리뷰에 한정한 문제·원인·사용자 요청 분리
- 플레이 시간별 비추천 비율과 작은 표본 경고
- 주제별 실제 리뷰 원문 확인
- OpenRouter 모델 목록·가격 조회와 무료/유료 구분
- 실행 전 예상 비용, 실행 중 예산 예약, 한도 초과 중단

## 토큰 사용 최적화

1. 한 글자짜리처럼 내용이 없는 리뷰는 LLM에 보내지 않습니다.
2. 주제는 최대 150건에서 한 번 발견하고 전체 리뷰에 재사용합니다.
3. 전체 분류는 15건씩 묶고 짧은 키의 JSON으로 응답받습니다. 본문이 같은 리뷰는 한 번만 보냅니다.
4. 불만 심층 분석은 불만 가능성이 있는 리뷰에만 실행합니다.
5. v5 요약은 원문 전체가 아닌 집계 결과를 한 번만 전달합니다.
6. JSONL 결과를 이어 쓰므로 완료한 리뷰는 재실행하지 않습니다.
7. 모델별 실제 단가로 남은 작업 비용을 계산하고 예산을 예약합니다.
8. 요약 캐시는 입력 해시가 같으면 재사용합니다.

무료 모델과 유료 모델은 같은 출력 계약을 사용합니다. 모델의 JSON 형식 지원 여부와 문맥 길이는 실행 전에 확인하며, 결과 누락 시 해당 묶음만 다시 시도합니다.

분석 시작 창에서 무료와 유료 모델을 버튼으로 나눠 고릅니다. 무료 모델(`:free`)은 OpenRouter 한도(분당 20회, 하루 50회 · 평생 10달러 이상 충전한 계정은 하루 1,000회)에 맞춰 한 번에 하나씩 약 3.3초 간격으로 요청합니다. 하루 한도에 닿으면 멈추고, 같은 설정으로 다시 실행하면 이미 분석한 리뷰는 건너뛰고 이어서 분석합니다. 리뷰 언어는 한국어만 · 모든 언어 · 다른 언어 하나 중에서 고릅니다.

## 설치

```powershell
git clone https://github.com/ChoKyungHwan98/Ai_review.git
cd Ai_review
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에 `OPENROUTER_API_KEY`를 입력합니다.

## 실행

```powershell
# 대시보드
python -m uvicorn main:app --host 127.0.0.1 --port 8765

# 전체 파이프라인
python pipeline.py --app-id 1623730 --lang koreana --budget 5

# 단계별 실행
python collect_reviews.py
python analyze_reviews_v3.py
python build_insights_v5.py
python quality_check.py        # 자료 상태 점검을 콘솔로 보기 (저장하지 않음)
```

대시보드: `http://127.0.0.1:8765/dashboard`

## 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | 필수 | OpenRouter 키 |
| `APP_ID` | `1623730` | 기본 Steam App ID |
| `LANG_CODE` | `koreana` | Steam 리뷰 언어 |
| `MODEL` | `google/gemini-2.5-flash-lite` | 분석 모델 |
| `BUDGET_USD` | `5.0` | 실행당 최대 분석 예산 |
| `TARGET_ERROR_PCT` | `5` | 목표 오차 범위 |
| `MIN_NEG_REVIEWS` | `100` | 비추천 리뷰 최소 목표 |
| `MIN_REVIEW_LEN` | `2` | AI 분석 최소 글자 수 |
| `FREE_DAILY_REQUESTS` | `1000` | 무료 모델 하루 요청 한도 (10달러 미만 충전 계정은 `50`) |

## 결과 파일

게임별 파일은 소스 저장소 밖의 형제 폴더 `../프로젝트/<Steam App ID>/`에 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `reviews.csv` | Steam 원본 리뷰 |
| `sample_design.json` | 모집단과 표본 설계 |
| `themes_v3.json` | 게임별 주제 목록 |
| `analysis_v3.jsonl` | 리뷰별 반응·재미·주제 분류 |
| `complaints_v3.jsonl` | 불만의 문제·원인·요청 |
| `usage_v3.json` | 단계별 호출·토큰·모델 단가 |
| `insights_v5.json` | AI 요약 · 할 일 · 주제 설명 |
| `pipeline_result.json` | 실행 상태와 단계별 결과 |

건수, 비율, 자료 상태 점검은 파일로 저장하지 않습니다. 화면을 열 때 `reviews.csv`와 `analysis_v3.jsonl`에서 다시 계산합니다. '분석 결과' 내보내기 CSV도 그때 만듭니다.

## 주요 API

| 방식 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/dashboard` | 대시보드 |
| `GET` | `/dashboard/data/v5` | 현재 분석 결과 |
| `GET` | `/dashboard/evidence` | 선택 주제의 근거 리뷰 |
| `GET` | `/api/games` | 분석이 완료된 게임 목록 |
| `GET` | `/api/models` | 선택 가능한 OpenRouter 모델 |
| `POST` | `/pipeline/run` | 분석 시작 |
| `GET` | `/pipeline/result` | 최근 실행 상태 |
| `GET` | `/api/games/review-stats` | Steam 리뷰 수와 눈금별 수집 계획 |

## 폴더 구성

```text
.
├── main.py                    # FastAPI 서버
├── config.py                  # 경로·모델·예산 설정
├── pipeline.py                # 전체 실행 흐름
├── collect_reviews.py         # Steam 수집과 표본 설계
├── analyze_reviews_v3.py      # 주제·반응·재미·불만 분석
├── sampling.py                # 표본 수식 (수집 계획 · 오차 · Wilson 범위)
├── dashboard_evidence.py      # 화면의 모든 건수·비율 집계와 근거 원문
├── build_insights_v5.py       # AI 요약과 할 일
├── model_catalog.py           # OpenRouter 모델과 가격
├── openrouter_limits.py       # 무료 모델 요청 속도와 하루 한도
├── progress.py                # 분석 진행 단계와 건수 (진행 화면용)
├── analysis_design.py         # 주제 자동 합치기와 분석 설계서
├── budget_control.py          # 실행 중 예산 통제
├── token_budget.py            # 실행 전 비용 견적
├── quality_check.py           # 자료 상태 점검 (화면을 열 때 계산)
├── static/                    # 대시보드 화면
├── tests/                     # 핵심 회귀 검사
└── docs/                      # 구조·조사·화면 설계 문서
```

## 화면 설계와 Impeccable

대시보드는 결론을 먼저 보여주고, 차트와 실제 리뷰로 근거를 확인하는 구조를 사용합니다. [Impeccable](https://github.com/pbakaus/impeccable)의 검출기로 AI가 만든 화면에 흔한 패턴(옆줄 테두리, 얇은 테두리 + 넓은 그림자, 장식용 줄무늬·광택, 낮은 글자 대비, 이모지 아이콘 등)을 검사합니다. 실행 프로그램의 의존성이 아니므로 저장소에 복제하거나 `requirements.txt`에 추가하지 않습니다.

```bash
npx impeccable detect static/          # 소스 검사
npx impeccable detect http://127.0.0.1:8765/dashboard   # 켜진 화면 검사
```

자세한 설명은 [포트폴리오용 제품 설계서](docs/포트폴리오용_제품_설계서.md), [파이프라인 구조](docs/파이프라인_구조.md), [리뷰 진단 시각화 설계](docs/리뷰진단_시각화_설계.md)를 참고하세요.
