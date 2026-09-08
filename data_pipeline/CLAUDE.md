# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

한국인 영어 학습자를 위한 영어 슬랭 선별 데이터 파이프라인. Wiktionary에서 슬랭 후보를 추출하고, Reddit 댓글 덤프에서 실제 사용 빈도를 검증한 뒤, LLM으로 각 단어의 대표 슬랭 의미를 선별해 학습 사이트용 데이터셋을 구축한다.

## Environment Setup

```bash
pip install -r data_pipeline/requirements.txt
# mwparserfromhell, zstandard, openai
```

Required environment variable for LLM scripts:
```bash
export OPENAI_API_KEY=sk-...
```

## Pipeline Execution Order

모든 스크립트는 **저장소 루트**에서 실행한다. 스크립트 내 경로는 전부 `data_pipeline/` 으로 시작하는 상대 경로다.

```
Stage 1  parse_wiktionary.py             → data_pipeline/output/slang_raw.json
Stage 2  parse_slang_raw.py              → matched_candidates.json  (※ 실행 디렉터리 기준)
Stage 3  filter_matched_candidates.py    → data_pipeline/data/filtered_candidates.json
Stage 4  rank_slang_candidates.py        → data_pipeline/data/scored_candidates.json (KEEP 6,423개)
Stage 5  reddit_context_cache_builder.py → data_pipeline/candidate_context_cache.jsonl (~643MB)
Stage 6  reddit_slang_llm_judger.py      → data_pipeline/output/word_summary.jsonl
Stage 7  rank_final_candidates.py        → data_pipeline/output/ranked_candidates.jsonl
```

> **Stage 2 → 3 경로 주의**: Stage 2는 출력을 실행 디렉터리 기준 상대경로(`matched_candidates.json`)로 쓰고,
> Stage 3의 기본 입력은 `data_pipeline/data/matched_candidates.json`이다.
> 파일을 옮기거나 Stage 3에 `--input`을 명시할 것.

> **Stage 7 선행 조건**: `rank_final_candidates.py`는 `word_summary.jsonl` 외에
> `data_pipeline/stratified_review_output/ranked_candidates_full.json`(보조 필드)을 함께 읽는다.
> 이 디렉터리는 `.gitignore` 대상이라 저장소에 포함되지 않는다.

위 Stage 1–7은 최초 데이터셋 구축 시 실행 완료. output/final_dataset.jsonl (3,293개)가 최종 산출물.

### 서비스 투입 스크립트 (반복 실행 가능)

```
add_korean_definitions.py   → definition_ko + category 필드 추가/재분류
                               python data_pipeline/add_korean_definitions.py
                               python data_pipeline/add_korean_definitions.py --recategorize

generate_examples.py        → example_en (학습자용 예문) + example_ko 생성
                               python data_pipeline/generate_examples.py

review_tool.py              → 단어 수동 검수 CLI (승인/보류/수정필요)
                               python data_pipeline/review_tool.py
```

### 검증 스크립트 (외부 데이터 불필요)

커밋된 `output/` 만 읽으므로 clone 직후 실행된다.

```
quality_report.py           → 퍼널·분포 집계 + 어휘 검증 + 산출물 간 교차 검증
                               python data_pipeline/quality_report.py
                               python data_pipeline/quality_report.py --strict   # 위반 시 exit 1

sync_service_categories.py  → 파생본(approved/pending)의 category 를
                               final_dataset 기준으로 재동기화
                               python data_pipeline/sync_service_categories.py           # dry-run, 미반영 시 exit 1
                               python data_pipeline/sync_service_categories.py --apply   # 실제 반영
```

테스트: `python -m pytest tests/ -q`

## 주요 출력 파일

| 파일 | 단어 수 | 용도 |
|------|---------|------|
| `output/service_public_approved.json` | 384 | **최종 서비스 투입 단어** (수동 검수 완료) |
| `output/service_public_pending.json` | 2,909 | 보류 (추가 검토 필요) |
| `output/final_dataset.jsonl` | 3,293 | 전체 후보 (definition_ko + category 포함) |
| `output/db_insert.json` | 3,293 | DB INSERT용 (final_dataset.jsonl 후처리 산출물) |

## Architecture

### Large File Handling
- Wiktionary: `bz2` + `ElementTree.iterparse`
- Reddit: `zstandard` streaming over `.zst` dumps (RC_2025-09.zst, RC_2025-12.zst)
- Context collection: reservoir sampling (Knuth's algorithm, seed=42)

### Key Data Directories
- `data_pipeline/output/` — 파이프라인 산출물 (저장소에 커밋됨)
- `data_pipeline/data/` — 중간 처리 파일 (filtered/scored candidates). `.gitignore` 대상
- `data_pipeline/dump/` — Wiktionary 원본 덤프. `.gitignore` 대상
- `data_pipeline/resources/` — 초기 실험용 사전 파일 4종.
  **현재 파이프라인 어느 스크립트도 참조하지 않는다** (레거시)

### Stage 5: Context Cache Builder
scored_candidates.json의 KEEP 후보 6,423개 대상으로 Reddit 문맥 수집.

**Tier별 수집 수 (get_target_n):**

| match_count | target_n |
|-------------|----------|
| < 10K       | 100      |
| 10K~50K     | 120      |
| 50K~200K    | 140      |
| 200K~1M     | 160      |
| > 1M        | 200      |

멀티프로세스: 2개 `.zst` 파일을 `multiprocessing.Process`로 동시 스캔.

### Stage 6: LLM Sense 분류
Reddit 문맥을 LLM(GPT-4.1-nano)으로 판정:
- 초기 60개 배치 → `is_slang=True/False` 투표
- K=0이면 `decision=drop`, K≥15이면 `enough_evidence` (조기 종료)
- 0<K<15이면 20개씩 추가 배치 → K_TARGET=15 도달 or n_max 소진 → `holdout`
- Drop 단어는 재실행 시 보존 (skip)

**Drop 조건:**
1. 캐시 맥락 < 60개
2. 초기 60개에서 슬랭 판정 0개 (K=0)

### Stage 7: Final Ranking
```
slang_ratio     = slang_hits / sampled
expected_capped = match_count * slang_ratio  (cap: 3,000,000)
cat_conf        = CATEGORY_CONF[slang_category]
                  internet_slang/aave=1.0, general_slang=0.85, colloquial=0.65, None=0.5
cat_penalty     = (1 - cat_conf) * 0.4
penalty         = min(0.95, cat_penalty * (1 - slang_ratio))
priority_score  = log10(1 + expected_capped) * (1 - penalty)
```

holdout 포함 기준: `slang_hits >= 3` AND `slang_ratio >= 1.67%`

### add_korean_definitions.py
- `definition_ko` + `category` 필드를 GPT-4.1-nano로 생성 (배치 20개)
- `--recategorize` 옵션으로 category만 재분류 가능 (`--limit N` 으로 소규모 검증)
- **LLM 응답의 category 는 `validate_categories()` 로 허용 어휘를 강제한다.**
  프롬프트 제약만으로는 목록 밖의 값이 새어 나오므로, 별칭은 `CATEGORY_ALIASES` 로 흡수하고
  나머지는 버린 뒤 종료 시 집계를 출력한다
- 재분류는 10배치마다 중간 저장하며, 예외 발생 시에도 `finally` 로 진행분을 보존한다
- 14개 카테고리: 칭찬·인정, 긍정·동의, 감탄·놀람, 강조 표현, 일상 대화, SNS·인터넷 반응, 줄임말·약어, 감정 표현, 비판·부정 반응, 관계·연애, 유머·밈, 게임·커뮤니티, 돈·라이프스타일, 주의/거친 표현

### Stage 4 Scoring Formula
```
support_score = 0.6 * log10(1 + match_count) + 0.4 * log10(1 + subreddit_count)
```
Labels: `keep` (≥ 3.65), `gray_zone` (3.20–3.65), `prune` (< 3.20)

## Hardcoded Paths
```
C:\Users\User\Downloads\reddit\comments\RC_2025-09.zst
C:\Users\User\Downloads\reddit\comments\RC_2025-12.zst
data_pipeline/dump/enwiktionary-20250920-pages-articles-multistream.xml.bz2
```
