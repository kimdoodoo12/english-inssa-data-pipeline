# English Slang Data Pipeline

**사전에 있는 영어 슬랭 중 "실제로 쓰이는 것"만 골라내는 데이터 파이프라인.**

Wiktionary에는 슬랭 표제어가 수만 개 있지만, 대부분은 아무도 쓰지 않거나 슬랭이 아닌 뜻으로 쓰인다.
이 파이프라인은 Reddit 댓글 덤프에서 **실사용 근거를 직접 수집해** 후보를 검증하고,
학습 서비스에 투입할 수 있는 데이터셋으로 좁힌다.

```
Wiktionary 슬랭 후보
        │  Reddit 실사용 빈도로 선별
   6,423 ─────────────────────────────  Stage 4
        │  LLM이 실제 문맥 60~200개로 슬랭 여부 판정   ← 후보의 48%(3,085)가 여기서 탈락
   3,338 ─────────────────────────────  LLM 판정 통과 (enough 2,412 + holdout 926)
        │  holdout 중 근거 부족 45 제외
   3,293 ─────────────────────────────  Stage 7 최종 데이터셋
        │  사람이 직접 검수
     384 ─────────────────────────────  서비스 투입 확정
```

압축 상태의 Reddit 월간 덤프 2개를 **단일 머신에서 스트리밍 처리**하고,
LLM 판정과 수동 검수는 **단어 단위로 진행분을 저장**해 중단에 대비했다.

| | |
|---|---|
| **Python** | 3.12 · 의존성 3개 (`mwparserfromhell`, `zstandard`, `openai`) |
| **LLM** | gpt-4.1-nano · `temperature=0` · JSON 스키마 강제 |
| **검증** | 단위 테스트 43개 · CI에서 데이터 품질 게이트 2종 |
| **산출물** | [`data_pipeline/output/`](data_pipeline/output/) — 저장소에 포함. 품질 리포트·테스트는 clone 후 바로 실행 가능 |

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

## 후보 선별 (Stage 1~4)

Wiktionary 슬랭 표제어를 Reddit 실사용 빈도로 6,423개까지 좁히는 구간이다. LLM은 쓰지 않는다.

```
Wiktionary 슬랭 표제어           33,009   (소문자 기준 고유 표제어, Stage 1 → slang_raw, 정의 항목 44,033)
        │  Reddit 전수 스캔 · 표면형 매칭, 한 번도 안 나온 3,735 제외
Reddit 매칭 후보                 29,123   (정의 항목 40,058)
        │  저빈도 · stopword · 기능어 구문 필터
Stage 3 통과                     15,198   (정의 항목 24,166)
        │  support_score 로 keep 선별
Stage 4 keep                       6,423
        │  LLM이 실제 문맥으로 슬랭 여부 판정
LLM 판정 통과                      3,338   (enough 2,412 + holdout 926)
        │  holdout 중 근거 부족 45 제외 (hits>=3 AND ratio>=1/60 미달)
최종 데이터셋                      3,293
        │  사람이 직접 검수
검수 통과                            384
```

33,009에서 3,735를 빼면 29,274인데 29,123과 151 차이가 나는 것은 정규화 과정에서 일부 표제어가 합쳐지기 때문이다.

Stage 3에서 13,925단어가 제거됐다. 사유별로는 `match_count <= 200` 13,753, stopword 단독 173, 기능어 구문 20이다
(한 단어가 사유를 둘 이상 가질 수 있어 합계가 13,925와 정확히 일치하지 않는다).
Stage 4는 15,198단어를 keep 6,423 / gray_zone 2,478 / prune 6,297로 나눈다.
이 숫자의 근거인 `data_pipeline/data/filter_summary.json`과 Stage 4 결과 `scored_candidates.json.gz`(Stage 5가 읽는 입력, 압축 해제 후 `data_pipeline/data/scored_candidates.json`으로 두면 된다)를 저장소에 포함했다. 웹에서 바로 볼 수 있는 일부 발췌는 `scored_candidates_sample.json`이다. 그 밖의 대용량 중간 산출물(`slang_raw`, `matched_candidates` 등)은 포함하지 않았다.

### Stage 1. 표제어와 라벨 추출 (`parse_wiktionary.py`)

`==English==` 섹션만 잘라 정의 줄의 라벨을 읽는다. 대상 라벨은 6종이다:
`slang`, `internet slang`, `internet`, `aave`, `informal`, `colloquial`.

→ 라벨은 네 가지 표기를 모두 본다: `{{lb|en|...}}`(`label`·`lbl` 포함), `{{context|...}}`(`cx`), `{{qualifier|...}}`(`q`), 괄호형 `(slang)`.
예문은 첫 `#:` 줄만 쓰고, `quote-*` 인용문은 예문으로 쓰지 않는다.

한계: 사용한 Wiktionary 덤프는 2025-09-20 기준이라 그 이후의 신조어는 반영되지 않았다.

### Stage 2. Reddit 전수 스캔 (`parse_slang_raw.py`)

Reddit API는 승인이 어려워 2025-09, 2025-12 월간 댓글 덤프로 대체했다.
댓글과 후보를 같은 방식으로 정규화(소문자, `_`·`-`는 공백)한 뒤 토큰화한다.
후보를 토큰 길이별 n-gram 인덱스로 만들어, **댓글 한 번 훑을 때** 모든 후보를 한꺼번에 대조한다.
`low key`, `no cap`처럼 여러 단어로 된 표현도 이렇게 잡는다.

```
"That was low key funny no cap"
1-gram: that / was / low / key / funny / no / cap
2-gram: that was / was low / low key / key funny / funny no / no cap
```

이 단계에서 후보마다 두 가지 사용량을 센다.

| 지표 | 뜻 | 예 |
|---|---|---|
| `match_count` | 후보가 등장한 **댓글 수**. 한 댓글에 여러 번 나와도 1회 | `rizz` 21,075 |
| `subreddit_count` | 후보가 등장한 **서로 다른 subreddit 수** | `rizz` 5,322 |

`match_count`는 "얼마나 자주 쓰이는가", `subreddit_count`는 "얼마나 넓은 커뮤니티에서 쓰이는가"를 나타낸다.

한계: 매칭은 **표면형 완전일치**다. 굴절형(`slays`, `vibing` 등)은 처리하지 않아 별개 문자열로 취급된다.

### Stage 3. 명백한 비슬랭 제거 (`filter_matched_candidates.py`)

- stopword만으로 이뤄진 후보 제거 (173단어)
- 기능어 구문 블랙리스트 20개(`on the`, `a lot`, `kind of` 등) 제거 (다단어 후보에만 적용)
- `match_count <= 200` 제거 (13,753단어)

200개 이하 컷에 걸린 후보는 두 달치 댓글에서도 거의 나오지 않는 단어다
(`abominate` 79, `mouse potato` 18 등). Stage 6은 단어당 최소 60개의 문맥을 요구하므로,
이 정도로 드문 후보는 어차피 판정할 수 없다.

한 달치 덤프에만 나온 단어(2,335개)는 모두 `match_count`가 82 이하여서 이 컷에서 함께 제거된다. 별도 필터가 아니라 `match_count <= 200` 컷의 결과다.
그 결과 살아남은 15,198단어는 **두 달 모두에서 등장한 단어**다.

### Stage 4. 사용량 점수화 (`rank_slang_candidates.py`)

```
support_score = 0.6 × log10(1 + match_count) + 0.4 × log10(1 + subreddit_count)

keep   : score >= 3.65     → 다음 단계로 통과 (6,423개)
gray   : 3.20 <= score < 3.65                  (2,478개)
prune  : score < 3.20                          (6,297개)
```

`support_score`는 **얼마나 자주, 얼마나 넓게 쓰이는가**를 재는 점수다. 슬랭인지 판별하지는 않는다.
(예: `hangry` = 0.6 × log10(1 + 7,067) + 0.4 × log10(1 + 2,257) ≈ 3.651)

- **두 지표를 합친 이유**: 댓글 수만 보면 특정 커뮤니티에서만 도는 전문용어가 올라온다.
  `scambaiting`은 7,076개 댓글에 나오지만 subreddit은 95개뿐이라 3.10(prune)이고,
  댓글 수가 비슷한 `bellybutton`(7,011개, 2,797 subreddit)은 3.69(keep)다.
- **가중치 0.6/0.4**: `match_count`를 `subreddit_count`보다 약간 더 중시하려는 설계 의도로 정했다. 별도 튜닝은 하지 않았다.
- **keep 컷 3.65**: 실제로 쓰이는 슬랭 여러 개를 positive seed로 두고 점수를 계산한 뒤,
  그 최저점보다 약간 낮은 값으로 정했다. 3.20~3.65는 버리지 않고 gray_zone으로 따로 남겼다.
  사람 검수(approved 384개)를 통과한 단어 중 최저점도 `hangry`(3.651), `pog`(3.679), `high key`(3.691)로 컷 바로 위에 모여 있다.
  컷을 ±0.05 옮기면 약 250개, ±0.1 옮기면 약 510개 변한다.
- sense(정의)별로 흩어진 행은 `normalized_word`로 묶고, `match_count`와 `subreddit_count`는 묶음 안의 **최댓값**을 쓴다.

한계: gray·prune은 이후 단계에서 판정하지 않았다. 컷 바로 아래에도 `shawty`, `newb`, `borked` 같은 단어가 있어 일부 슬랭이 누락됐을 수 있다.
positive seed 목록은 저장소에 남아 있지 않다.

임계값은 각 스크립트 상단 상수로 두었다 (Stage 3의 컷은 `--match-threshold` 옵션).

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

→ **Reservoir Sampling (Knuth)** 으로 한 번의 순회만으로 표본을 얻었다. (모든 단어의 표본이 가득 차면 스캔을 일찍 끝내므로, 아주 흔한 단어는 덤프 앞부분의 비중이 다소 커질 수 있다.)
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

**초기 60개의 근거**: 실제 슬랭 비율이 5% 이상이라고 보수적으로 가정하면(실측이 아닌 가정, p = 0.05),
문맥 n개에서 슬랭이 하나도 안 나올 확률은 (1 − p)ⁿ이다. n = 60이면 0.95⁶⁰ ≈ 4.6%라서,
60개에서 슬랭이 0개인 단어는 슬랭으로 쓰이지 않는다고 보고 drop한다.
K = 15는 한 단어의 여러 뜻 분포를 확인하기 위한 최소 증거 수이고,
최대 문맥 수(100~200)는 슬랭이 아닌 literal 뜻으로 쓰이는 경우를 고려해 `match_count` 구간별로 달리했다.

**후보의 48%(3,085개)가 배치 하나로 걸러졌다.**
drop 조건은 두 가지다: 초기 60개에서 K=0, 또는 캐시된 문맥이 60개 미만. 이번 데이터의 3,085개는 모두 앞의 경우다.
예를 들어 `money`는 사전에 "멋진, 훌륭한"이라는 슬랭 뜻이 있지만, 판정한 60개 문맥에서 슬랭 용법이 하나도 나오지 않아(K=0) 탈락했다.

**점수가 높다고 슬랭인 것은 아니다.** 판정 결과로 보면 Stage 4 점수 구간별 슬랭 비율은 3.65~4.0: 중앙값 6.7%·평균 18.3%, 4.0~5.0: 3.6%·13.1%, 5.0 이상: 0%·8.8%다.
다만 고점수 구간에도 `cap`(enough_evidence, 17/160, 점수 5.27)처럼 슬랭 비율이 10%대인 단어가 있어, 점수가 낮을수록 슬랭이라는 뜻은 아니다.
5.5 이상에서 탈락한 297개는 대부분 `get`, `one`, `people` 같은 일반어와 `https`, `reddit` 같은 사이트 잔재다.
그래서 Stage 4의 점수 컷은 1차 필터로만 쓰고, 슬랭 판별은 이 단계가 맡는다.

### 4. 수천 번의 API 호출은 반드시 중간에 끊긴다

→ **중단을 정상 경로로 가정했다.**
판정 결과를 단어 단위로 즉시 flush하고, 재실행 시 `decision == "drop"` 항목은 다시 판정하지 않는다. (drop이 아닌 단어는 재실행 시 처음부터 다시 판정한다.)
재판정 비용을 막으면서 판정 일관성도 유지된다.
수동 검수는 progress 파일로 진행분을 보존한다.

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
