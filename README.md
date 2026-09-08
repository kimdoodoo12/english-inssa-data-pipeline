# English Slang Data Pipeline

Wiktionary 영어 슬랭 후보를 Reddit 댓글 덤프로 검증하고, LLM 판정과 수동 검수를 거쳐
**한국인 학습자용 슬랭 데이터셋**을 만드는 7단계 배치 파이프라인.

압축된 Reddit 월간 덤프 2개를 단일 머신에서 스트리밍 처리해, 후보 6,423개를
서비스 투입 가능한 384개까지 좁힌다. 모든 단계는 중단 후 재시작이 안전하다.

```
Wiktionary 덤프          Reddit 댓글 덤프 2개 (.zst)
      │                          │
      └──────────┬───────────────┘
                 ▼
      6,423  Reddit 사용 빈도로 선별된 후보
                 ▼  LLM 슬랭 판정
      3,293  최종 데이터셋 (한국어 정의 + 예문 + 카테고리)
                 ▼  수동 검수
        384  서비스 투입 확정
```

| | |
|---|---|
| **언어** | Python 3.12 |
| **의존성** | `mwparserfromhell`, `zstandard`, `openai` (3개) |
| **LLM** | gpt-4.1-nano |
| **산출물** | [`data_pipeline/output/`](data_pipeline/output/) — 저장소에 포함, clone 후 바로 확인 가능 |
| **라이선스** | 코드 MIT / 데이터 CC BY-SA 4.0 ([상세](DATA_LICENSE.md)) |

---

## 결과물 미리보기

`output/service_public_approved.json` — 수동 검수를 통과한 384개 중 일부.

| word | definition_ko | example_en | example_ko |
|---|---|---|---|
| `dm` | 온라인에서 누군가에게 비공개 메시지 또는 다이렉트 메시지를 보내는 것 | I just sent you a dm about the meetup. | 모임 관련해서 방금 너한테 다이렉트 메시지 보냈어. |
| `gif` | 반복 재생되는 짧은 애니메이션 이미지로, 온라인에서 감정이나 반응, 유머를 표현할 때 사용한다. | Check out this funny gif I found! | 이 웃긴 gif 하나 찾았어, 봐봐! |
| `goat` | 역대 최고를 의미하며, 어떤 분야에서 최고의 사람을 칭할 때 사용됩니다. | Serena is the goat at tennis! | 세리나는 테니스의 최고야! |
| `sick` | 멋지고 인상적이거나 훌륭한 것을 표현할 때 쓰이며, 강한 긍정을 나타냅니다. | That new car is sick! | 저 새 차 진짜 멋지다! |

---

## 파이프라인

```
Stage 1  parse_wiktionary.py              Wiktionary XML(bz2) 스트리밍 → 슬랭 후보 추출
Stage 2  parse_slang_raw.py               Reddit 전수 스캔 → 단어별 사용 횟수 집계
Stage 3  filter_matched_candidates.py     stopword·기능어·저빈도 필터
Stage 4  rank_slang_candidates.py         support_score 산정 → keep 6,423개
Stage 5  reddit_context_cache_builder.py  단어별 Reddit 문맥 샘플링 (~643MB)
Stage 6  reddit_slang_llm_judger.py       LLM 슬랭 판정 + 영어 정의 생성
Stage 7  rank_final_candidates.py         priority_score 랭킹 → 3,293개
```

이후 서비스 투입 단계(반복 실행 가능): 한국어 정의·카테고리 부여 → 예문 생성 → 수동 검수 CLI.

전체 흐름과 각 단계의 알고리즘은 **[PIPELINE_REPORT.md](PIPELINE_REPORT.md)** 에 정리되어 있다.

---

## 설계 결정

### 1. Reservoir Sampling — 스트림에서 편향 없이 뽑기

LLM 판정에는 단어당 100~200개의 실제 사용 문맥이 필요하다. 그런데 Reddit 덤프는
압축 상태로만 읽을 수 있고 전체를 메모리에 올릴 수 없으며, **전체 개수를 미리 알 수 없다.**

앞에서부터 N개를 자르면 덤프 앞부분(특정 시간대·서브레딧)에 치우친다.
Knuth의 reservoir sampling으로 한 번의 순회만으로 균등 확률 샘플을 얻었다.
`RANDOM_SEED=42`를 고정해 재실행 시 동일한 샘플이 재현된다.

`match_count` 구간별로 목표 수집량을 100~200개로 차등했다. 빈도가 높은 단어일수록
의미가 분산되어 더 많은 문맥이 필요하기 때문이다.

### 2. 조기 종료로 LLM 호출 비용 통제

6,423개 단어 × 문맥 200개를 전부 판정하면 호출량이 감당되지 않는다. 2단계로 끊었다.

```
초기 60개 배치 판정 → 슬랭 판정 수 K
  K == 0   → 즉시 drop        (추가 호출 없음)
  K >= 15  → enough_evidence  (추가 호출 없음)
  0 < K < 15 → 20개씩 추가 배치, K=15 도달 또는 상한 소진 시 holdout
```

**후보의 48%(3,085개)가 초기 60개 배치 하나로 걸러졌다.** 판정 대상 문맥 수를
크게 줄인 지점이다. 대표적으로 `cap`은 60개 문맥이 전부 "market cap", "salary cap"
같은 비속어 용법이어서 K=0으로 drop됐다.

### 3. 중단·재시작 안전성

Stage 6은 수천 회의 API 호출이 필요해, 중간에 끊기는 것을 정상 경로로 가정했다.

- 판정 결과를 단어 단위로 즉시 flush — 프로세스가 죽어도 진행분이 남는다
- 재실행 시 `decision == "drop"`인 단어는 건너뛴다 — 재판정 비용 방지 + 판정 일관성 유지
- 예문 생성·수동 검수도 각각 별도 progress 파일로 같은 방식을 따른다

### 4. 슬랭 순도 보정

`match_count`만으로 랭킹하면 슬랭이 아닌 일반어가 상위에 온다.
LLM이 부여한 `slang_category`별 신뢰도를 penalty로 반영했다.

```
slang_ratio     = slang_hits / sampled
expected_capped = min(match_count × slang_ratio, 3,000,000)
cat_conf        = internet_slang·aave 1.0 / general_slang 0.85 / colloquial 0.65 / 미상 0.5
penalty         = min(0.95, (1 - cat_conf) × 0.4 × (1 - slang_ratio))
priority_score  = log10(1 + expected_capped) × (1 - penalty)
```

---

## 데이터 품질

산출물만으로 실행되는 품질 리포트를 제공한다. 외부 데이터가 필요 없다.

```bash
python data_pipeline/quality_report.py
```

집계 항목: 단계별 퍼널, `slang_category`·`difficulty_tier` 분포, 서비스 카테고리 분포,
**허용 어휘 검증**, **산출물 간 카테고리 정합성 교차 검증**.

`--strict` 를 붙이면 검증 실패 시 종료 코드 1을 반환한다. CI가 이 값을 게이트로 쓴다.

### 퍼널

| 단계 | 결과 |
|---|---|
| Stage 4 — 사용 빈도 선별 | **6,423** |
| Stage 6 — LLM 판정 | drop 3,085 (48.0%) · enough_evidence 2,412 · holdout 926 |
| Stage 7 — 최종 랭킹 | **3,293** (enough_evidence 2,412 + holdout 통과 881, 45개 탈락) |
| 수동 검수 | **승인 384** · 보류 2,909 |

| 분포 | |
|---|---|
| `slang_category` | general 1,751 · internet 1,329 · colloquial 196 · aave 15 |
| `difficulty_tier` | supplemental 2,593 · common 500 · essential 200 |
| `is_vulgar` | 10건 (0.3%) |

### 이 리포트로 발견하고 고친 것

품질 리포트를 도입하면서 두 건의 결함이 드러났다.

**① LLM 응답 스키마 미검증** — 카테고리는 14개로 제한했지만 프롬프트로만 제약하고
응답을 검증하지 않아, 목록 밖의 값 4종(`성·성적 표현`, `감각·신체`, `감사·인사`, `스포츠·게임`)이
데이터에 섞여 있었다. `validate_categories()`를 추가해 응답 단계에서 허용 어휘로 강제하고,
반복 관측된 값은 별칭 매핑으로 흡수했다.

**② 산출물 간 카테고리 불일치** — `service_public_approved.json`은 `final_dataset.jsonl`의
파생본인데, 재분류가 원본에만 적용되면서 **384건 중 209건(54.4%)의 카테고리가 어긋나 있었다.**
보류 목록도 2,909건 중 1,650건(56.7%)이 어긋나 있었다. 검증 로직을 넣은 뒤 3,293개 전체를
한 번에 재분류하고, `sync_service_categories.py`로 파생본을 단일 진실 공급원 기준으로
재동기화했다.

재분류 실행 중 검증 로직이 목록 밖의 값(`인터넷 반응` — `SNS·인터넷 반응`의 절단형) 1건을
실제로 차단했다. 반복 관측되는 형태이므로 별칭 매핑에 추가해 데이터 손실을 막았다.

**검증 결과**

```
$ python data_pipeline/quality_report.py --strict
=== 어휘 검증 ===
✅ 모든 카테고리가 허용 목록 내에 있음
=== 교차 검증 — final_dataset ↔ service_public_approved ===
✅ 두 파일의 category 값이 모두 일치
```

재발 방지를 위해 CI에서 `quality_report.py --strict` 와 `sync_service_categories.py`(dry-run)를
실행한다. 파생본이 어긋난 채로는 병합되지 않는다.

---

## 실행

```bash
pip install -r data_pipeline/requirements.txt
```

Stage 1·2·5는 외부 덤프 파일이 필요하다. 경로는 환경변수로 지정한다.

```bash
# Windows PowerShell
$env:REDDIT_DUMP_PATHS = "D:\reddit\RC_2025-09.zst;D:\reddit\RC_2025-12.zst"
$env:WIKTIONARY_DUMP_PATH = "D:\dump\enwiktionary-20250920-pages-articles-multistream.xml.bz2"
$env:OPENAI_API_KEY = "sk-..."

# bash
export REDDIT_DUMP_PATHS="/data/RC_2025-09.zst:/data/RC_2025-12.zst"
```

모든 스크립트는 **저장소 루트**에서 실행한다.

```bash
python data_pipeline/parse_wiktionary.py       # Stage 1
python data_pipeline/parse_slang_raw.py        # Stage 2
# ... Stage 3–7
```

Stage 1–7은 데이터셋 최초 구축 시 1회 실행 완료 상태다. 산출물이 저장소에 포함되어 있어
**품질 리포트와 서비스 투입 스크립트는 외부 데이터 없이 바로 실행된다.**

재현에 필요한 외부 파일 목록은 [PIPELINE_REPORT.md](PIPELINE_REPORT.md)의 7장에 정리되어 있다.

## 테스트

```bash
python -m pytest tests/ -q
```

파이프라인 본체는 수백 GB 외부 데이터를 요구해 통합 테스트가 어렵다. 대신 결과 랭킹을
좌우하는 순수 함수 — 점수 공식, 임계값 경계, tier 경계, LLM 응답 검증 — 를 검증한다.
Stage 5의 수집 목표량과 Stage 6의 소진 상한이 일치하는지 확인하는 교차 검증도 포함된다.
두 값이 어긋나면 판정이 조용히 조기 종료된다.

---

## 저장소 구조

```
data_pipeline/
  parse_wiktionary.py  parse_slang_raw.py  filter_matched_candidates.py
  rank_slang_candidates.py  reddit_context_cache_builder.py
  reddit_slang_llm_judger.py  rank_final_candidates.py     # Stage 1–7
  add_korean_definitions.py  generate_examples.py  review_tool.py
  quality_report.py  sync_service_categories.py
  output/                                                   # 산출물 (커밋됨)
tests/
PIPELINE_REPORT.md                                          # 단계별 상세 설계 문서
DATA_LICENSE.md
```
