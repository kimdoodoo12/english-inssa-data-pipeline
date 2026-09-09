# 영어 슬랭 학습 데이터셋 구축 파이프라인 보고서

**작성일**: 2026-05-19  
**프로젝트**: English-INSSA-Data-Pipeline (GitHub: `kimdoodoo12/english-inssa-data-pipeline`)  
**최종 산출물**: `data_pipeline/output/final_dataset.jsonl` (3,293개 단어)

---

## 1. 프로젝트 개요

이 프로젝트는 한국인 영어 학습자를 위한 영어 슬랭 학습 데이터셋을 구축하는 파이프라인이다.

핵심 흐름은 다음과 같다.

1. Wiktionary에서 슬랭 후보 단어와 영어 정의를 추출한다.
2. Reddit 댓글 덤프에서 후보 단어의 실제 사용 빈도를 집계한다.
3. 빈도와 분산 기준으로 후보를 1차 필터링한다.
4. Stage 6에서 `reddit_slang_llm_judger.py`가 Reddit 실제 문맥을 LLM으로 판정해, 해당 단어가 실제 슬랭/비격식 의미로 쓰였는지 확인한다.
5. Stage 7에서 Stage 6 결과를 기반으로 최종 우선순위를 계산한다.
6. 이후 한국어 정의, 서비스 카테고리, 학습용 예문을 추가하고 수동 검수로 공개 단어를 확정한다.

실행 환경:

```bash
pip install -r data_pipeline/requirements.txt   # mwparserfromhell, zstandard, openai
export OPENAI_API_KEY=sk-...
# 실행 위치: 저장소 루트 (스크립트 내 경로가 모두 data_pipeline/ 기준 상대경로)
```

---

## 2. 전체 파이프라인

```text
Stage 1  parse_wiktionary.py
         -> output/slang_raw.json

Stage 2  parse_slang_raw.py
         -> matched_candidates.json

Stage 3  filter_matched_candidates.py
         -> data/filtered_candidates.json

Stage 4  rank_slang_candidates.py
         -> data/scored_candidates.json
         -> keep 후보 6,423개

Stage 5  reddit_context_cache_builder.py
         -> candidate_context_cache.jsonl
         -> 후보별 Reddit 실제 문맥 수집

Stage 6  reddit_slang_llm_judger.py
         -> output/word_summary.jsonl
         -> LLM으로 문맥별 슬랭 여부 판정
         -> 영어 슬랭 정의와 slang_category 생성

Stage 7  rank_final_candidates.py
         -> output/ranked_candidates.jsonl
         -> Stage 6 수치 기반 최종 우선순위 계산

후처리  add_korean_definitions.py
         -> final_dataset.jsonl에 definition_ko, category 추가

후처리  review_tool.py
         -> service_public_approved.json / service_public_pending.json

후처리  generate_examples.py
         -> 승인 단어에 example_en, example_ko 추가
```

---

## 3. 단계별 설명

### Stage 1: `parse_wiktionary.py`

**역할**: Wiktionary XML에서 영어 슬랭 후보를 추출한다.

입력:

- `data_pipeline/dump/enwiktionary-20250920-pages-articles-multistream.xml.bz2`

출력:

- `data_pipeline/output/slang_raw.json`

처리 방식:

- `ElementTree.iterparse`로 bz2 XML을 스트리밍 처리한다.
- `==English==` 섹션만 대상으로 한다.
- 정의 라인의 라벨에서 `slang`, `internet slang`, `internet`, `aave`, `informal`, `colloquial` 등을 감지한다.
- 후보 단어, 영어 정의, 예문, 원본 라벨을 저장한다.

### Stage 2: `parse_slang_raw.py`

**역할**: Reddit 댓글 덤프에서 후보 단어의 실제 등장 횟수를 집계한다.

입력:

- `data_pipeline/output/slang_raw.json`
- Reddit comments `.zst` 파일 2개

출력:

- `matched_candidates.json`
- `unmatched_candidates.json`
- `candidate_usage_stats.json`

처리 방식:

- 후보 단어를 n-gram 인덱스로 구성한다.
- Reddit JSONL을 zstandard 스트리밍으로 읽는다.
- 댓글 body를 토큰화하고 후보 n-gram과 매칭한다.
- 단어별 `match_count`, 등장 subreddit 샘플 등을 누적한다.

### Stage 3: `filter_matched_candidates.py`

**역할**: 통계 기반 1차 필터링으로 명백한 비슬랭 또는 너무 낮은 빈도 후보를 제거한다.

출력:

- `data_pipeline/data/filtered_candidates.json`
- `data_pipeline/data/dropped_candidates.json`
- `data_pipeline/data/filter_summary.json`

주요 제거 조건:

- Reddit 매칭 이력이 없는 후보
- stopword만으로 구성된 후보
- 기능어 구문 블랙리스트에 해당하는 후보
- 기본 기준 `match_count < 200`

### Stage 4: `rank_slang_candidates.py`

**역할**: Reddit 사용량 기반 support score를 계산하고 Stage 5 이후 처리 대상을 고른다.

출력:

- `data_pipeline/data/scored_candidates.json`

점수식:

```text
support_score = 0.6 * log10(1 + match_count)
              + 0.4 * log10(1 + subreddit_count)
```

분류 기준:

| label | 기준 |
|---|---:|
| `keep` | `support_score >= 3.65` |
| `gray_zone` | `3.20 <= support_score < 3.65` |
| `prune` | `support_score < 3.20` |

Stage 5 이후에는 `keep` 후보 6,423개만 사용한다.

### Stage 5: `reddit_context_cache_builder.py`

**역할**: Stage 6 LLM 판정을 위해 후보 단어별 Reddit 실제 문맥을 수집한다.

입력:

- `data_pipeline/data/scored_candidates.json`
- Reddit comments `.zst` 파일 2개

출력:

- `data_pipeline/candidate_context_cache.jsonl`
- `data_pipeline/candidate_context_summary.jsonl`

후보별 목표 문맥 수:

| match_count | target_n |
|---:|---:|
| `< 10,000` | 100 |
| `10,000 ~ 50,000` | 120 |
| `50,000 ~ 200,000` | 140 |
| `200,000 ~ 1,000,000` | 160 |
| `> 1,000,000` | 200 |

처리 방식:

- Reddit `.zst` 파일 2개를 각각 별도 프로세스로 병렬 스캔한다.
- 후보 단어가 포함된 댓글 문맥을 수집한다.
- Reservoir Sampling, seed 42를 사용해 단어별 문맥 수를 제한한다.
- `context_id` 해시 기준으로 중복 문맥을 제거한다.

---

## 4. Stage 6: `reddit_slang_llm_judger.py`

**Stage 6의 핵심 역할은 `reddit_slang_llm_judger.py`가 수행한다.**  
이 단계는 Stage 5에서 모은 Reddit 문맥을 LLM으로 판정해, 후보 단어가 실제로 슬랭 또는 비격식 의미로 쓰였는지 확인한다.

### 입력과 출력

입력:

- `data_pipeline/data/scored_candidates.json`
- `data_pipeline/candidate_context_cache.jsonl`

출력:

- `data_pipeline/output/word_summary.jsonl`
- `data_pipeline/output/context_judgments.jsonl`

모델:

- 기본값: `gpt-4.1-nano`
- 환경변수 `OPENAI_MODEL`로 변경 가능
- `temperature=0`
- JSON 응답 강제

### Stage 6 처리 로직

단어별 처리 흐름:

```text
1. Stage 4의 keep 후보만 로드한다.
2. Stage 5에서 수집한 Reddit 문맥을 단어별로 가져온다.
3. 문맥이 60개 미만이면 drop 처리한다.
4. 초기 60개 문맥을 LLM으로 판정한다.
5. 슬랭 판정 수 K가 0이면 drop 처리한다.
6. K가 15 이상이면 enough_evidence 처리한다.
7. 0 < K < 15이면 문맥을 20개씩 추가 판정한다.
8. 최대 문맥 수까지 확인한 뒤:
   - K >= 15이면 enough_evidence
   - K < 15이면 holdout
9. 슬랭으로 판정된 문맥만 모아 영어 정의와 slang_category를 생성한다.
```

상수:

| 이름 | 값 | 의미 |
|---|---:|---|
| `N0` | 60 | 최초 판정 문맥 수 |
| `BATCH_SIZE` | 20 | 추가 판정 배치 크기 |
| `K_TARGET` | 15 | 충분한 슬랭 근거 기준 |
| `API_MAX_RETRIES` | 5 | API 재시도 횟수 |

`n_max` 기준:

| match_count | n_max |
|---:|---:|
| `< 10,000` | 100 |
| `10,000 ~ 50,000` | 120 |
| `50,000 ~ 200,000` | 140 |
| `200,000 ~ 1,000,000` | 160 |
| `> 1,000,000` | 200 |

### Stage 6 결과 해석

| decision | 의미 |
|---|---|
| `drop` | 문맥 부족 또는 초기 60개에서 슬랭 사용 0건 |
| `enough_evidence` | 슬랭 판정이 15건 이상으로 충분함 |
| `holdout` | 일부 슬랭 근거는 있으나 15건에는 도달하지 못함 |

실제 결과:

| 항목 | 개수 |
|---|---:|
| Stage 6 전체 후보 | 6,423 |
| `drop` | 3,085 |
| `enough_evidence` | 2,412 |
| `holdout` | 926 |

---

## 5. Stage 7: `rank_final_candidates.py`

**역할**: Stage 6 결과를 기반으로 최종 학습 우선순위를 계산한다.  
Stage 7에서는 AI를 새로 호출하지 않는다.

입력:

- `data_pipeline/output/word_summary.jsonl`
- `data_pipeline/stratified_review_output/ranked_candidates_full.json` (보조 필드)

> **선행 조건**: 보조 입력 파일은 `definition_en`, `slang_category` 등 부가 필드를 채우는 데 쓰인다.
> `stratified_review_output/`은 `.gitignore` 대상이라 저장소에 포함되지 않으므로, 이 단계를
> 재실행하려면 해당 파일을 별도로 확보해야 한다.

출력:

- `data_pipeline/output/ranked_candidates.jsonl`

필터 조건:

- `decision == "drop"`은 제외한다.
- `decision == "holdout"`은 `slang_hits >= 3`이고 `slang_ratio >= 1.67%`인 경우만 포함한다.

점수식:

```text
slang_ratio     = slang_hits / sampled
raw_expected    = match_count * slang_ratio
expected_capped = min(raw_expected, 3,000,000)

cat_conf:
  internet_slang = 1.0
  aave           = 1.0
  general_slang  = 0.85
  colloquial     = 0.65
  unknown        = 0.5

cat_penalty    = (1 - cat_conf) * 0.4
penalty        = min(0.95, cat_penalty * (1 - slang_ratio))
priority_score = log10(1 + expected_capped) * (1 - penalty)
```

최종 출력:

- `output/ranked_candidates.jsonl`: 3,293개

---

## 6. 후처리 및 서비스 데이터 구성

### `add_korean_definitions.py`

**역할**: `final_dataset.jsonl`에 한국어 정의와 서비스 카테고리를 추가한다.

입력/출력:

- `data_pipeline/output/final_dataset.jsonl` in-place 업데이트

AI 사용:

- 영어 정의를 자연스러운 한국어 정의로 번역
- 서비스용 카테고리 1~3개 선택
- `--recategorize` 실행 시 카테고리만 재분류

서비스 카테고리:

```text
칭찬·인정, 긍정·동의, 감탄·놀람, 강조 표현,
일상 대화, SNS·인터넷 반응, 줄임말·약어, 감정 표현,
비판·부정 반응, 관계·연애, 유머·밈, 게임·커뮤니티,
돈·라이프스타일, 주의/거친 표현
```

### `review_tool.py`

**역할**: 공개 후보 단어를 사람이 최종 승인/보류/수정 필요로 검수한다.

출력:

- `data_pipeline/output/service_public_approved.json`
- `data_pipeline/output/service_public_pending.json`
- `data_pipeline/output/service_public_needs_revision.json`

AI 사용:

- 없음

### `generate_examples.py`

**역할**: 수동 승인된 단어에 학습용 영어 예문과 한국어 번역을 생성한다.

입력/출력:

- `data_pipeline/output/service_public_approved.json` in-place 업데이트
- 진행 체크포인트: `data_pipeline/output/example_gen_progress.jsonl`

AI 사용:

- 승인 단어별 `example_en`, `example_ko` 생성

---

## 7. 최종 산출물 현황

| 파일 | 개수 | 설명 |
|---|---:|---|
| `output/word_summary.jsonl` | 6,423 | Stage 6 LLM 판정 결과 |
| `output/ranked_candidates.jsonl` | 3,293 | Stage 7 최종 후보 |
| `output/final_dataset.jsonl` | 3,293 | 한국어 정의와 카테고리 포함 전체 데이터 |
| `output/db_insert_draft.json` | 3,293 | 초안 내보내기. **서비스 투입본 아님** — `example_en`이 AI 예문 생성 이전의 Reddit 원문이다 |
| `output/service_public_approved.json` | 384 | 수동 검수 후 서비스 공개 승인 |
| `output/service_public_pending.json` | 2,909 | 추가 검수 보류 |

---

## 8. AI 사용 내역

| 단계 | 스크립트 | AI 사용 여부 | AI 역할 |
|---|---|---|---|
| Stage 1 | `parse_wiktionary.py` | 없음 | 규칙 기반 Wiktionary 파싱 |
| Stage 2 | `parse_slang_raw.py` | 없음 | Reddit 빈도 집계 |
| Stage 3 | `filter_matched_candidates.py` | 없음 | 규칙 기반 필터링 |
| Stage 4 | `rank_slang_candidates.py` | 없음 | 통계 점수 계산 |
| Stage 5 | `reddit_context_cache_builder.py` | 없음 | Reddit 문맥 수집 |
| Stage 6 | `reddit_slang_llm_judger.py` | 있음 | 문맥별 슬랭 판정, 영어 정의 생성, slang_category 생성 |
| Stage 7 | `rank_final_candidates.py` | 없음 | Stage 6 결과 기반 점수 계산 |
| 후처리 | `add_korean_definitions.py` | 있음 | 한국어 정의 번역, 서비스 카테고리 생성/재분류 |
| 후처리 | `generate_examples.py` | 있음 | 학습용 영어 예문과 한국어 번역 생성 |
| 후처리 | `review_tool.py` | 없음 | 수동 검수 |

---

## 9. 공개 프롬프트

아래는 Stage 6 이후 AI를 사용한 모든 프롬프트 템플릿이다. `{word}`, `{definition_en}`, `{context_text}` 등은 실행 시 실제 값으로 대체된다.

### 9.1 Stage 6 문맥별 슬랭 판정 프롬프트

사용 위치:

- `reddit_slang_llm_judger.py`
- `CLASSIFY_SYSTEM_PROMPT`
- `build_classify_prompt`

System prompt:

```text
You are a slang usage classifier with broad knowledge of contemporary English slang. For each Reddit context, decide if the target word is used in a slang or informal sense based on your own knowledge. Reply with JSON only.
```

User prompt template:

```text
Word: "{word}"

Reddit contexts:
[1] {context_text_1}

[2] {context_text_2}

...

For each context [1]~[{n}]:
- is_slang: true if the word is used in a slang, casual, or informal sense

Return JSON only: {"results": [{"is_slang": true}, ...]}
```

### 9.2 Stage 6 영어 정의 및 slang_category 생성 프롬프트

사용 위치:

- `reddit_slang_llm_judger.py`
- `GENERATE_DEF_SYSTEM_PROMPT`
- `build_definition_prompt`

System prompt:

```text
You are an expert English slang lexicographer for a Korean language-learning app. Based on the Reddit examples provided, write a concise slang definition (1-2 sentences) and categorize the slang type. Focus on the SLANG or informal meaning only. If examples are ambiguous, use your own knowledge of contemporary English slang. Reply with JSON only.
```

User prompt template:

```text
Word: "{word}"
Slang usage rate: {slang_ratio:.0%} in Reddit

Reddit examples (slang usage only):
[1] {slang_context_1}
[2] {slang_context_2}
...

Write a concise slang definition for "{word}" based strictly on the examples above.
Also categorize: "internet_slang" (memes/online), "aave" (African American Vernacular), "general_slang" (common spoken slang), or "colloquial" (informal but not typical slang).
Return JSON only: {"definition_en": "...", "slang_category": "internet_slang|aave|general_slang|colloquial"}
```

### 9.3 한국어 정의 및 서비스 카테고리 생성 프롬프트

사용 위치:

- `add_korean_definitions.py`
- `SYSTEM_PROMPT`
- `USER_TEMPLATE`

System prompt:

```text
You are a Korean-English bilingual dictionary editor for a language learning app targeting Korean adults. For each slang word, do two things:
1. Translate the English definition to natural, concise Korean (1-2 sentences max).
2. Pick the best category from this list: "칭찬·인정", "긍정·동의", "감탄·놀람", "강조 표현", "일상 대화", "SNS·인터넷 반응", "줄임말·약어", "감정 표현", "비판·부정 반응", "관계·연애", "유머·밈", "게임·커뮤니티", "돈·라이프스타일", "주의/거친 표현"

Rules for translation:
- Use Korean only (no English unless the slang term itself is kept).
- Capture the SLANG meaning, not the literal dictionary meaning.
- Do NOT explain etymology or origin.

Rules for category:
- Choose 1 to 3 categories from the provided list (most relevant first).
- Return as a JSON array, e.g. ["긍정·동의", "일상 대화"].
- Base it on how the word is actually used, not its literal meaning.
```

User prompt template:

```text
Process these slang words. Return JSON: {"results": [{"word": "...", "definition_ko": "...", "category": ["...", "..."]}, ...]}

- word: "{word_1}", definition_en: "{definition_en_1}"
- word: "{word_2}", definition_en: "{definition_en_2}"
...
```

### 9.4 서비스 카테고리 재분류 프롬프트

사용 위치:

- `add_korean_definitions.py --recategorize`
- `RECATEGORIZE_SYSTEM`
- `RECATEGORIZE_TMPL`

System prompt:

```text
You are a categorization expert for a Korean English slang learning app. For each slang word, pick the best category from this list: "칭찬·인정", "긍정·동의", "감탄·놀람", "강조 표현", "일상 대화", "SNS·인터넷 반응", "줄임말·약어", "감정 표현", "비판·부정 반응", "관계·연애", "유머·밈", "게임·커뮤니티", "돈·라이프스타일", "주의/거친 표현"

Rules:
- Choose 1 to 3 categories (most relevant first).
- Return as a JSON array.
- '줄임말·약어': the word is formed from initials or shortened letters of a phrase. Check the definition - if it says 'acronym for', 'stands for', or spells out a full phrase (like 'Greatest Of All Time', 'Fear Of Missing Out'), include '줄임말·약어'.
- Always pair '줄임말·약어' with a meaning-based category.
- Regular slang words that are NOT abbreviations (sus, cap, rizz, slay) do NOT get '줄임말·약어'.
```

User prompt template:

```text
Categorize these slang words. Return JSON: {"results": [{"word": "...", "category": ["...", "..."]}, ...]}

- word: "{word_1}", definition_en: "{definition_en_1}"
- word: "{word_2}", definition_en: "{definition_en_2}"
...
```

### 9.5 학습용 예문 생성 프롬프트

사용 위치:

- `generate_examples.py`
- `SYSTEM_PROMPT`
- `USER_TEMPLATE`

System prompt:

```text
You are a language learning content editor for Korean adults learning English slang.
For each slang word, generate:
1. One natural, everyday conversational English example sentence (1 sentence, under 20 words)
   - Casual context: texting friends, hanging out, social media comment
   - No profanity, no sexual content, no violent content
   - The slang word must appear in the sentence
   - The context must clearly show the slang's meaning
2. A natural Korean translation of that sentence (not literal - match the feeling and tone)

Return JSON: {"results": [{"word": "...", "example_en": "...", "example_ko": "..."}, ...]}
```

User prompt template:

```text
Process these slang words. Return JSON: {"results": [{"word": "...", "example_en": "...", "example_ko": "..."}]}

- word: "{word_1}", definition_en: "{definition_en_1}", definition_ko: "{definition_ko_1}", category: ["{category_1}"]
- word: "{word_2}", definition_en: "{definition_en_2}", definition_ko: "{definition_ko_2}", category: ["{category_2}"]
...
```

---

## 10. 발표용 요약

Stage 6의 `reddit_slang_llm_judger.py`는 이 파이프라인에서 AI가 처음 핵심 판정에 쓰이는 단계다. 이 단계에서 Reddit 실제 문맥을 LLM으로 문맥별 판정해 후보 단어가 실제 슬랭/비격식 의미로 쓰이는지 확인했고, 슬랭으로 확인된 문맥을 바탕으로 영어 정의와 슬랭 유형을 생성했다. 이후 Stage 7은 AI를 추가 호출하지 않고 Stage 6 결과를 수치화해 최종 우선순위를 계산했으며, 서비스용 한국어 정의와 예문 생성에는 별도 고정 프롬프트를 사용했다.

---

## 11. 하드코딩 경로 및 재현 시 주의사항

### 하드코딩된 절대 경로

```text
C:\Users\User\Downloads\reddit\comments\RC_2025-09.zst   # parse_slang_raw.py:24
C:\Users\User\Downloads\reddit\comments\RC_2025-12.zst   # parse_slang_raw.py:25
                                                          # reddit_context_cache_builder.py:26-27 (동일)
data_pipeline/dump/enwiktionary-20250920-pages-articles-multistream.xml.bz2  # parse_wiktionary.py:10
```

Reddit 덤프 경로는 최초 구축 당시 작업 PC 기준이다. 다른 환경에서 Stage 2·5를 재실행하려면 각 스크립트 상단의 `REDDIT_DUMP_PATHS`를 수정해야 한다.

### Stage 2 → Stage 3 경로 불일치

Stage 2 출력 경로(`matched_candidates.json` 등)는 실행 디렉터리 기준 상대경로로 하드코딩되어 있으므로, Stage 3 실행 시 `--input` 인자로 실제 경로를 명시해야 한다.

### 저장소에 포함되지 않는 입력

`.gitignore` 대상이라 clone만으로는 확보되지 않는 파일들이다.

| 경로 | 필요 단계 |
|------|-----------|
| `data_pipeline/dump/` | Stage 1 |
| Reddit `.zst` 덤프 2개 | Stage 2, 5 |
| `data_pipeline/data/` (중간 산출물) | Stage 3, 4, 5, 6 |
| `data_pipeline/candidate_context_cache.jsonl` (~643MB) | Stage 6 |
| `data_pipeline/stratified_review_output/ranked_candidates_full.json` | Stage 7 |

Stage 1–7의 결과물인 `data_pipeline/output/` 하위 파일은 저장소에 커밋되어 있으므로, 서비스 투입 스크립트(6장)는 clone 직후 바로 실행 가능하다.

### 미사용 리소스

`data_pipeline/resources/` 의 4개 파일(`canonical_cue_map.json`, `domain_lexicon.json`, `polarity_lexicon.json`, `cannot_link_rules.json`)은 초기 실험 단계의 산물로, **현재 파이프라인의 어떤 스크립트도 참조하지 않는다.**

---

## 12. 검증 및 데이터 품질

Stage 1–7과 서비스 투입 스크립트는 외부 덤프에 의존하지만, 아래 검증 도구는
저장소에 커밋된 `output/` 만 읽으므로 clone 직후 실행된다.

### `quality_report.py`

| 항목 | 내용 |
|------|------|
| 입력 | `output/` 의 산출물 5종 |
| 출력 | 표준출력 (`--format md` 로 마크다운) |
| 종료 코드 | `--strict` 지정 시, 검증 실패 항목이 있으면 1 |

집계 항목: 단계별 퍼널, `slang_category`·`difficulty_tier` 분포, 서비스 카테고리 분포,
허용 어휘 검증, `final_dataset` ↔ `service_public_approved` 카테고리 교차 검증.

### `sync_service_categories.py`

`service_public_{approved,pending}.json` 의 `category` 를 `final_dataset.jsonl` 기준으로
재동기화한다. 기본 동작은 dry-run이며, 미반영 변경이 있으면 종료 코드 1을 반환한다
(`--apply` 로 실제 반영).

`category` 필드의 소유자는 `add_korean_definitions.py` 이고 그 출력 대상이
`final_dataset.jsonl` 이므로, 이 파일을 단일 진실 공급원으로 삼는다.

### 이 도구들로 발견한 결함

**① LLM 응답 스키마 미검증**

`add_korean_definitions.py` 는 허용 카테고리 14개를 프롬프트로만 제약하고 응답을 검증하지
않았다. 그 결과 목록 밖의 값 4종이 데이터에 섞였다.

| 규정 외 값 | 해당 단어 |
|------------|-----------|
| `성·성적 표현` | rubber, hump, poppers |
| `감각·신체` | batter |
| `감사·인사` | bless |
| `스포츠·게임` | wicket |

→ `validate_categories()` 를 추가해 응답 단계에서 허용 어휘를 강제하고, 반복 관측된 값은
`CATEGORY_ALIASES` 로 흡수했다. 매핑되지 않는 값은 버리고 실행 종료 시 집계를 출력한다.

**② 산출물 간 카테고리 불일치**

`service_public_approved.json` 은 `final_dataset.jsonl` 의 파생본이지만, `--recategorize` 가
원본에만 적용되면서 두 파일이 어긋났다.

| 비교 대상 | 불일치 |
|-----------|--------|
| `service_public_approved.json` (384건) | 209건 (54.4%) |
| `service_public_pending.json` (2,909건) | 1,650건 (56.7%) |

→ 검증 로직을 넣은 뒤 전체를 한 번에 재분류하고, 파생본을 재동기화했다.
CI에서 두 스크립트를 실행해 재발을 막는다.

### 단위 테스트

```bash
python -m pytest tests/ -q
```

파이프라인 본체는 수백 GB의 외부 데이터를 요구해 통합 테스트가 어렵다. 대신 결과 랭킹을
좌우하는 순수 함수만 떼어내 검증한다.

- `compute_support_score` — 공식 일치, 음수 입력 클램프, 가중치 대소관계
- `classify_support` — keep/gray_zone/prune 임계값 경계
- `compute_priority` — 캡 적용, 미등록 카테고리 기본 신뢰도, 0 나눗셈
- `is_holdout_included` — 두 조건 동시 충족 여부
- `get_target_n` / `get_n_max` — tier 경계, **Stage 5와 Stage 6의 tier 표 일치 교차 검증**
- `validate_categories` / `normalize_categories` — 허용 어휘 강제, 별칭 매핑, 두 구현의 동등성

Stage 5의 수집 목표량과 Stage 6의 소진 상한이 어긋나면 판정이 조용히 조기 종료되므로,
두 값의 일치를 테스트로 고정했다.
