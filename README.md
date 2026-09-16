# English Slang Data Pipeline

**사전에 있는 영어 슬랭 중 "실제로 쓰이는 것"만 골라내는 데이터 파이프라인.**

Wiktionary에는 슬랭 표제어가 수만 개 있지만, 대부분은 아무도 쓰지 않거나 슬랭이 아닌 뜻으로 쓰인다.
이 파이프라인은 Reddit 댓글 덤프에서 **실사용 근거를 직접 수집해** 후보를 검증하고,
학습 서비스에 투입할 수 있는 데이터셋으로 좁힌다.

```
Wiktionary 슬랭 후보
        │  Reddit 실사용 빈도로 선별
   6,423 ─────────────────────────────  Stage 4
        │  LLM이 실제 문맥 60~200개로 슬랭 여부 판정
   3,293 ─────────────────────────────  Stage 7   ← 후보의 48%가 여기서 탈락
        │  사람이 직접 검수
     384 ─────────────────────────────  서비스 투입 확정
```

압축 상태의 Reddit 월간 덤프 2개를 **단일 머신에서 스트리밍 처리**하고,
모든 단계가 **중단 후 재시작해도 안전**하도록 설계했다.

| | |
|---|---|
| **Python** | 3.12 · 의존성 3개 (`mwparserfromhell`, `zstandard`, `openai`) |
| **LLM** | gpt-4.1-nano · `temperature=0` · JSON 스키마 강제 |
| **검증** | 단위 테스트 43개 · CI에서 데이터 품질 게이트 2종 |
| **산출물** | [`data_pipeline/output/`](data_pipeline/output/) — 저장소에 포함, clone 후 바로 실행 가능 |

---

## 결과물

`service_public_approved.json` — 검수를 통과한 384개 중 일부.

| word | 한국어 뜻 | 예문 | 번역 |
|---|---|---|---|
| `goat` | 역대 최고를 의미하며, 어떤 분야에서 최고의 사람을 칭할 때 | Serena is the goat at tennis! | 세리나는 테니스의 최고야! |
| `sick` | 멋지고 인상적이거나 훌륭한 것을 표현할 때 쓰이며, 강한 긍정을 나타냄 | That new car is sick! | 저 새 차 진짜 멋지다! |
| `dm` | 온라인에서 누군가에게 비공개 메시지를 보내는 것 | I just sent you a dm about the meetup. | 모임 관련해서 방금 너한테 메시지 보냈어. |
| `gif` | 반복 재생되는 짧은 애니메이션 이미지로, 감정이나 반응을 표현할 때 | Check out this funny gif I found! | 이 웃긴 gif 하나 찾았어, 봐봐! |

각 단어는 우선순위 점수, 슬랭 유형, 난이도 tier, 서비스 카테고리를 함께 갖는다.

---

## 이 문제의 어려운 지점

### 1. 데이터가 메모리에 들어가지 않는다

Reddit 월간 댓글 덤프는 압축 상태로만 다룰 수 있고, 전체 레코드 수를 미리 알 수 없다.
Wiktionary 덤프도 마찬가지다.

→ **양쪽 모두 스트리밍으로 처리했다.** Wiktionary는 `bz2` + `ElementTree.iterparse`,
Reddit은 `zstandard` 스트리밍. 메모리 사용량이 입력 크기와 무관하게 일정하다.

### 2. 어느 문맥을 뽑을지 미리 정할 수 없다

LLM 판정에는 단어당 100~200개의 실제 사용 문맥이 필요하다. 그런데 스트림 앞에서부터
N개를 자르면 덤프 앞부분(특정 시간대·서브레딧)에 치우친 표본이 된다.

→ **Reservoir Sampling (Knuth)** 으로 한 번의 순회만으로 균등 확률 표본을 얻었다.
`RANDOM_SEED=42` 고정으로 재실행 시 동일한 표본이 재현된다.
빈도가 높은 단어일수록 의미가 분산되므로, `match_count` 구간별로 목표 표본 수를 100~200개로 차등했다.

### 3. LLM 호출이 곧 비용이다

후보 6,423개 × 문맥 200개를 전부 판정하면 감당이 안 된다.

→ **2단계 조기 종료**로 끊었다.

```
초기 60개 배치 판정 → 슬랭 판정 수 K
  K == 0     → 즉시 drop         (추가 호출 없음)
  K >= 15    → enough_evidence   (추가 호출 없음)
  0 < K < 15 → 20개씩 추가, K=15 도달 또는 상한 소진 시 holdout
```

**후보의 48%(3,085개)가 배치 하나로 걸러졌다.**
예를 들어 `cap`은 60개 문맥이 전부 "market cap", "salary cap"이라 K=0으로 탈락했다.
사전에는 "거짓말"이라는 슬랭 뜻이 있지만 실제 Reddit에서는 그 뜻으로 거의 쓰이지 않는다는 의미다.

### 4. 수천 번의 API 호출은 반드시 중간에 끊긴다

→ **중단을 정상 경로로 가정했다.**
판정 결과를 단어 단위로 즉시 flush하고, 재실행 시 `decision == "drop"` 항목은 건너뛴다.
재판정 비용을 막으면서 판정 일관성도 유지된다.
예문 생성·수동 검수도 각각 progress 파일로 같은 방식을 따른다.

### 5. 빈도만으로 랭킹하면 슬랭이 아닌 단어가 상위에 온다

→ LLM이 부여한 `slang_category`별 신뢰도를 **penalty로 반영**했다.

```
slang_ratio     = slang_hits / sampled
expected_capped = min(match_count × slang_ratio, 3,000,000)
cat_conf        = internet_slang·aave 1.0 / general_slang 0.85 / colloquial 0.65 / 미상 0.5
penalty         = min(0.95, (1 - cat_conf) × 0.4 × (1 - slang_ratio))
priority_score  = log10(1 + expected_capped) × (1 - penalty)
```

---

## LLM 출력을 신뢰하지 않는다

이 파이프라인은 LLM을 세 곳에서 쓴다 — 슬랭 판정, 정의 생성, 예문 생성.
전부 `temperature=0` + JSON 스키마 강제이고, **프롬프트 전문이 문서에 공개되어 있다**
([PIPELINE_REPORT.md §9](PIPELINE_REPORT.md)).

그런데 프롬프트만으로는 부족했다.

카테고리는 14개로 제한하도록 프롬프트에 명시했지만 응답을 검증하지 않았더니,
목록 밖의 값 4종이 데이터에 섞여 있었다 (`성·성적 표현`, `감각·신체`, `감사·인사`, `스포츠·게임`).

→ `validate_categories()`를 추가해 **응답 단계에서 허용 어휘를 강제**했다.
반복 관측되는 변형은 별칭으로 흡수하고, 나머지는 버린 뒤 종료 시 집계를 출력한다.
전체 재분류를 다시 돌렸을 때 이 로직이 실제로 1건(`인터넷 반응` — 절단형)을 차단했다.

---

## 산출물을 검증한다

외부 데이터 없이 실행되는 품질 리포트를 만들었다. 커밋된 `output/`만 읽는다.

```bash
python data_pipeline/quality_report.py --strict
```

단계별 퍼널, 필드 분포, **허용 어휘 검증**, **산출물 간 카테고리 정합성 교차 검증**을 집계하고,
실패 항목이 있으면 종료 코드 1을 반환한다.

**이 리포트를 도입하자마자 결함이 드러났다.**

`service_public_approved.json`은 `final_dataset.jsonl`의 파생본인데, 재분류가 원본에만
적용되면서 두 파일이 어긋나 있었다.

| 파일 | 카테고리 불일치 |
|---|---|
| `service_public_approved.json` (384건) | **209건 (54.4%)** |
| `service_public_pending.json` (2,909건) | **1,650건 (56.7%)** |

검증 로직을 넣은 뒤 3,293개를 한 번에 재분류하고, `sync_service_categories.py`로 파생본을
단일 진실 공급원 기준으로 재동기화했다. 현재 상태는 이렇다.

```
=== 어휘 검증 ===
✅ 모든 카테고리가 허용 목록 내에 있음
=== 교차 검증 — final_dataset ↔ service_public_approved ===
✅ 두 파일의 category 값이 모두 일치
```

재발은 CI가 막는다. `quality_report.py --strict`와 `sync_service_categories.py`(dry-run)가
실패하면 병합되지 않는다.

---

## 테스트

```bash
python -m pytest tests/ -q     # 43 passed
```

파이프라인 본체는 수백 GB의 외부 데이터를 요구해 통합 테스트가 어렵다.
대신 **결과 랭킹을 좌우하는 순수 함수**를 떼어내 검증한다 — 점수 공식, 임계값 경계,
tier 경계, LLM 응답 검증.

그중 하나는 회귀 고정이 아니라 **실제 교차 검증**이다.
Stage 5의 문맥 수집 목표량(`get_target_n`)과 Stage 6의 소진 상한(`get_n_max`)은
같은 tier 표를 써야 한다. 어긋나면 판정이 **조용히** 조기 종료된다. 이 일치를 테스트로 고정했다.

---

## 실행

```bash
pip install -r data_pipeline/requirements.txt
```

Stage 1·2·5는 외부 덤프가 필요하다. 경로는 환경변수로 지정한다.

```bash
# PowerShell
$env:REDDIT_DUMP_PATHS = "D:\reddit\RC_2025-09.zst;D:\reddit\RC_2025-12.zst"
$env:WIKTIONARY_DUMP_PATH = "D:\dump\enwiktionary-...xml.bz2"
$env:OPENAI_API_KEY = "sk-..."

# bash
export REDDIT_DUMP_PATHS="/data/RC_2025-09.zst:/data/RC_2025-12.zst"
```

모든 스크립트는 **저장소 루트**에서 실행한다.

```
Stage 1  parse_wiktionary.py              Wiktionary XML(bz2) → 슬랭 후보 추출
Stage 2  parse_slang_raw.py               Reddit 전수 스캔 → 단어별 사용 횟수
Stage 3  filter_matched_candidates.py     stopword·기능어·저빈도 필터
Stage 4  rank_slang_candidates.py         support_score → keep 6,423개
Stage 5  reddit_context_cache_builder.py  문맥 샘플링 (~643MB)
Stage 6  reddit_slang_llm_judger.py       LLM 판정 + 영어 정의 생성
Stage 7  rank_final_candidates.py         priority_score 랭킹 → 3,293개

[데이터셋 조립]
         build_final_dataset.py           → final_dataset.jsonl
         enrich_dataset.py                → difficulty_tier 부여 (rank≤200 / ≤700 / 나머지)
         add_korean_definitions.py        → 한국어 정의 + 서비스 카테고리
         review_tool.py                   → 수동 검수 CLI
         generate_examples.py             → 학습용 예문 (승인 단어만)
         export_db.py                     → DB 투입용 초안 내보내기
```

**Stage 1–7은 이미 실행 완료 상태다.** 산출물이 저장소에 있어 품질 리포트, 테스트,
서비스 투입 스크립트는 외부 데이터 없이 바로 돌릴 수 있다.

---

## 더 읽을 것

| 문서 | 내용 |
|---|---|
| [PIPELINE_REPORT.md](PIPELINE_REPORT.md) | 단계별 알고리즘, **LLM 프롬프트 전문**, 검증 도구, 재현 조건 |
| [DATA_REPORT_FINAL.md](DATA_REPORT_FINAL.md) | 졸업작품 보고서용 데이터 구조·출처 분석 |
| [DATA_LICENSE.md](DATA_LICENSE.md) | 데이터 출처와 라이선스 (Wiktionary CC BY-SA 승계) |

**라이선스**: 코드 MIT · 데이터셋 CC BY-SA 4.0

---

<sub>졸업작품 팀 프로젝트에서 시작한 저장소이며, `data_pipeline/` 이하 데이터 파이프라인 전체를 단독으로 설계·구현했다.</sub>
