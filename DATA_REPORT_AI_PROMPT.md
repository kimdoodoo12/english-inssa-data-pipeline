# 데이터 수집·전처리에서의 AI 활용 보고서 (보완)

> 본 문서는 졸업작품 최종보고서의 "데이터 수집 및 전처리" 항목을 보완하기 위해, 프로젝트 코드를 직접 확인하여 **AI(GPT)와 프롬프트가 실제로 활용된 부분**을 정리한 것이다.
> 모든 프롬프트는 코드에 실제로 존재하는 원문을 옮긴 것이며, 코드에서 확인되지 않는 내용은 `[확인 필요]`로 표시하였다.
> (확인 근거 파일: `reddit_slang_llm_judger.py`, `add_korean_definitions.py`, `generate_examples.py`)

---

## 1. 데이터 수집·전처리 과정 개요

본 프로젝트의 데이터는 단순히 한 곳에서 내려받은 것이 아니라, **사전 데이터에서 후보를 추출하고 → 실제 사용 데이터로 검증하고 → AI로 의미와 학습 콘텐츠를 보완하는** 다단계 과정을 거쳐 구축되었다. 전체 과정은 크게 다음 세 국면으로 나눌 수 있다.

1. **수집 단계** — 영어 사전(Wiktionary) 덤프에서 슬랭·비격식 후보 단어를 규칙 기반으로 추출하고, 실제 온라인 대화 데이터(Reddit 댓글 덤프)에서 각 단어의 사용 빈도와 실제 사용 문맥을 수집하였다.
2. **정제(전처리) 단계** — 사용 빈도가 지나치게 낮거나 슬랭으로 보기 어려운 후보를 통계·규칙 기반으로 제거하고, 중복 문맥을 정리하였다.
3. **가공(보완) 단계** — 선별된 단어에 대해 AI(GPT)를 활용하여 의미 판정, 영어·한국어 정의, 학습 카테고리, 학습용 예문을 생성하였다.

이 가운데 **AI는 수집·정제 단계에는 직접 개입하지 않고, 주로 "검증과 가공" 단계에서 활용되었다.** 즉 데이터를 모으고 거르는 작업은 규칙과 통계로 처리하고, 사람이 일일이 판단하기 어려운 "이 단어가 실제로 슬랭으로 쓰이는가", "이 의미를 한국어로 어떻게 설명할 것인가", "학습용 예문을 어떻게 만들 것인가"와 같은 **언어적 판단 작업에 AI를 보조 수단으로 사용**하였다.

> `[확인 필요]` Reddit 댓글 덤프 파일(`RC_2025-09.zst`, `RC_2025-12.zst`)은 로컬 파일로 처리되며, 코드에는 외부 API 호출이 없다(오프라인 덤프 사용). 실제 수집 기간·범위는 사용자 확인 필요.

---

## 2. AI를 활용한 부분과 목적

AI(OpenAI GPT, 코드 기본 모델 `gpt-4.1-nano`, `temperature=0`, JSON 응답 강제)는 데이터 파이프라인에서 다음 세 가지 목적으로 활용되었다.

| 활용 위치 (코드 파일) | 단계 | AI 활용 목적 | 입력 | 출력 |
|---|---|---|---|---|
| `reddit_slang_llm_judger.py` | 검증 (Stage 6) | ① 단어가 실제 슬랭으로 쓰이는지 **문맥별 판정** ② 슬랭 의미의 **영어 정의·유형 생성** | 단어 + Reddit 실제 댓글 문맥 | 문맥별 슬랭 여부, 영어 정의(`definition_en`), 슬랭 유형(`slang_category`) |
| `add_korean_definitions.py` | 가공 | 영어 정의를 **자연스러운 한국어 정의로 번역** + **학습 카테고리 분류** | 단어 + 영어 정의 | 한국어 정의(`definition_ko`), 카테고리(`category`) |
| `generate_examples.py` | 가공 | 학습자용 **영어 예문과 한국어 번역 생성** | 단어 + 영어/한국어 정의 + 카테고리 | 예문(`example_en`), 예문 번역(`example_ko`) |

핵심은, **AI가 데이터를 "만들어낸" 것이 아니라, 실제 Reddit 사용 데이터를 근거로 "판정하고 정리하는" 역할을 했다**는 점이다. 특히 슬랭 판정과 영어 정의 생성은 모두 "Reddit에서 실제로 수집한 문맥을 근거로 하라"는 지시를 프롬프트에 명시하고 있어, AI의 임의 생성을 줄이고 실제 데이터에 기반하도록 설계되어 있다.

AI를 사용하지 않은 단계(규칙·통계·사람 판단만 사용): Wiktionary 파싱, Reddit 빈도 집계, 규칙 기반 필터링, 통계 점수 랭킹, 문맥 수집, 최종 우선순위 계산, 수동 검수(`review_tool.py`).

---

## 3. 보고서용 프롬프트 정리 (코드 원문 기반)

아래 프롬프트는 모두 **코드에 실제로 존재하는 원문**이다. 따라서 보고서에는 "대표 예시"가 아니라 **실제 사용된 프롬프트**로 기재할 수 있다. (변수 `{word}`, `{definition_en}` 등은 실행 시 실제 값으로 치환된다.)

### 3.1 슬랭 사용 여부 판정 프롬프트 — `reddit_slang_llm_judger.py`

**역할(System)**: 모델에게 "현대 영어 슬랭에 대한 폭넓은 지식을 가진 슬랭 사용 분류기"의 역할을 부여하고, 각 Reddit 문맥에서 해당 단어가 슬랭/비격식 의미로 쓰였는지 판단하게 한다.

```text
[System]
You are a slang usage classifier with broad knowledge of contemporary English slang.
For each Reddit context, decide if the target word is used in a slang or informal sense
based on your own knowledge. Reply with JSON only.

[User]
Word: "{word}"

Reddit contexts:
[1] {context_text_1}

[2] {context_text_2}
...

For each context [1]~[{n}]:
- is_slang: true if the word is used in a slang, casual, or informal sense

Return JSON only: {"results": [{"is_slang": true}, ...]}
```

### 3.2 영어 슬랭 정의·유형 생성 프롬프트 — `reddit_slang_llm_judger.py`

**역할(System)**: "한국어 학습 앱을 위한 영어 슬랭 사전 편집자" 역할을 부여하고, 제공된 Reddit 예시에 근거하여 간결한 슬랭 정의(1~2문장)와 슬랭 유형 분류를 생성하게 한다.

```text
[System]
You are an expert English slang lexicographer for a Korean language-learning app.
Based on the Reddit examples provided, write a concise slang definition (1-2 sentences)
and categorize the slang type. Focus on the SLANG or informal meaning only.
If examples are ambiguous, use your own knowledge of contemporary English slang.
Reply with JSON only.

[User]
Word: "{word}"
Slang usage rate: {slang_ratio}% in Reddit

Reddit examples (slang usage only):
[1] {slang_context_1}
[2] {slang_context_2}
...

Write a concise slang definition for "{word}" based strictly on the examples above.
Also categorize: "internet_slang" (memes/online), "aave" (African American Vernacular),
"general_slang" (common spoken slang), or "colloquial" (informal but not typical slang).
Return JSON only: {"definition_en": "...", "slang_category": "internet_slang|aave|general_slang|colloquial"}
```

### 3.3 한국어 정의·카테고리 생성 프롬프트 — `add_korean_definitions.py`

**역할(System)**: "한국 성인 대상 언어 학습 앱의 한영 사전 편집자" 역할을 부여하고, 영어 정의를 자연스러운 한국어로 번역하며, 정해진 14개 카테고리 중 1~3개를 고르게 한다. 직역이 아닌 슬랭 의미 중심 번역, 어원 설명 금지 규칙이 포함되어 있다.

```text
[System]
You are a Korean-English bilingual dictionary editor for a language learning app
targeting Korean adults. For each slang word, do two things:
1. Translate the English definition to natural, concise Korean (1-2 sentences max).
2. Pick the best category from this list:
   "칭찬·인정", "긍정·동의", "감탄·놀람", "강조 표현", "일상 대화", "SNS·인터넷 반응",
   "줄임말·약어", "감정 표현", "비판·부정 반응", "관계·연애", "유머·밈", "게임·커뮤니티",
   "돈·라이프스타일", "주의/거친 표현"

Rules for translation:
- Use Korean only (no English unless the slang term itself is kept).
- Capture the SLANG meaning, not the literal dictionary meaning.
- Do NOT explain etymology or origin.

Rules for category:
- Choose 1 to 3 categories from the provided list (most relevant first).
- Return as a JSON array, e.g. ["긍정·동의", "일상 대화"].
- Base it on how the word is actually used, not its literal meaning.

[User]
Process these slang words. Return JSON: {"results": [{"word": "...", "definition_ko": "...", "category": ["...", "..."]}, ...]}

- word: "{word_1}", definition_en: "{definition_en_1}"
- word: "{word_2}", definition_en: "{definition_en_2}"
...
```

> 참고: 동일 스크립트에는 카테고리만 다시 분류하는 재분류 프롬프트(`--recategorize` 옵션)도 존재한다. 이 프롬프트는 "줄임말·약어"를 판별하는 규칙(예: 'acronym for', 'stands for', 'Greatest Of All Time' 등)을 추가로 명시하고 있다.

### 3.4 학습용 예문 생성 프롬프트 — `generate_examples.py`

**역할(System)**: "한국 성인 영어 슬랭 학습자를 위한 학습 콘텐츠 편집자" 역할을 부여하고, 20단어 이하의 자연스러운 일상 대화체 영어 예문 1개와 그 한국어 번역을 생성하게 한다. 비속어·성적·폭력적 내용 금지, 슬랭 단어가 문장에 반드시 포함되고 의미가 드러나야 한다는 규칙이 포함되어 있다.

```text
[System]
You are a language learning content editor for Korean adults learning English slang.
For each slang word, generate:
1. One natural, everyday conversational English example sentence (1 sentence, under 20 words)
   - Casual context: texting friends, hanging out, social media comment
   - No profanity, no sexual content, no violent content
   - The slang word must appear in the sentence
   - The context must clearly show the slang's meaning
2. A natural Korean translation of that sentence (not literal - match the feeling and tone)

Return JSON: {"results": [{"word": "...", "example_en": "...", "example_ko": "..."}, ...]}

[User]
Process these slang words. Return JSON: {"results": [{"word": "...", "example_en": "...", "example_ko": "..."}]}

- word: "{word_1}", definition_en: "{...}", definition_ko: "{...}", category: ["{...}"]
- word: "{word_2}", definition_en: "{...}", definition_ko: "{...}", category: ["{...}"]
...
```

---

## 4. "대표 프롬프트 예시"를 새로 만들어도 되는가에 대한 판단

**결론: 새로 지어낼 필요가 없다.** 본 프로젝트는 AI를 사용한 모든 지점(슬랭 판정·영어 정의·한국어 정의·카테고리·예문)에서 **실제 프롬프트가 코드에 그대로 남아 있다.** 따라서 보고서에는 위 3장의 **실제 프롬프트 원문을 그대로 인용**하는 것이 정확하고 신뢰도가 높다. 임의로 만든 예시 프롬프트를 넣으면 오히려 실제 구현과 어긋날 수 있으므로 권장하지 않는다.

다만 보고서의 가독성을 위해 다음 정도의 가공은 무방하다.

- 긴 프롬프트에서 핵심 지시 문장만 발췌하여 인용하고, 전문은 부록으로 분리
- 영어 프롬프트 옆에 한국어 의역(역할 설명)을 병기 — 단, "이는 코드 원문의 의역"임을 명시
- 변수 자리(`{word}` 등)에 실제 데이터 예시(예: `dm`)를 넣은 "실행 예시"를 추가로 제시

> `[확인 필요]` 위 프롬프트는 코드에 정의된 텍스트이며, 실제 데이터 생성에 사용된 모델 버전·실행 시점의 프롬프트가 코드와 동일했는지(중간에 수정·재실행되었는지)는 코드만으로는 확정할 수 없다.

---

## 5. 보고서에 넣으면 좋은 자료 추천

### 5.1 그림 (Figure)

- **AI 활용 위치 표시 흐름도**: 전체 파이프라인 중 AI가 개입하는 3개 지점(슬랭 판정 / 한국어 정의·카테고리 / 예문 생성)을 색으로 강조한 다이어그램. "수집·정제는 규칙·통계, 가공·검증은 AI"라는 역할 분담을 한눈에 보여줄 수 있다.
- **슬랭 판정 의사결정 흐름도**: 초기 60개 문맥 판정 → K=0이면 제외(drop), K≥15이면 채택(enough_evidence), 그 사이면 20개씩 추가 판정 → 최종 채택/보류(holdout)로 이어지는 판정 로직. AI가 "한 번에 생성"이 아니라 "근거를 누적해 판단"하는 구조임을 강조할 수 있다.

### 5.2 표 (Table)

- **표 A. AI 활용 요약표** (본 문서 2장) — AI 활용 위치·목적·입출력 정리
- **표 B. 단계별 AI 사용 여부표** — 각 스크립트별 AI 사용/미사용과 그 이유
- **표 C. 슬랭 유형(slang_category) 분류 기준표** — internet_slang / aave / general_slang / colloquial 의미

| slang_category | 의미 |
|---|---|
| `internet_slang` | 밈·온라인에서 쓰이는 인터넷 슬랭 |
| `aave` | African American Vernacular English (흑인 영어) 유래 |
| `general_slang` | 일반적인 구어 슬랭 |
| `colloquial` | 슬랭은 아니지만 비격식 구어 표현 |

### 5.3 코드 설명 (보고서용 간략 서술)

- **AI 호출 공통 설정 설명**: "모든 AI 호출은 `temperature=0`(무작위성 제거)과 JSON 형식 강제(`response_format`) 옵션을 사용하여, 결과의 일관성과 후처리 안정성을 확보하였다." — 이는 세 스크립트 모두에서 코드로 확인됨.
- **배치 처리 설명**: 한국어 정의·예문 생성은 단어를 묶음(배치)으로 처리하여 API 호출 효율을 높였다. (`add_korean_definitions.py`, `generate_examples.py`에서 배치 단위 처리 확인)

### 5.4 프롬프트 예시 (실행 예시)

보고서에 "실제로 이렇게 동작한다"를 보여주는 실행 예시 1건을 넣으면 효과적이다. 예:

```text
입력 단어: "dm"
→ (AI 판정) Reddit 문맥 다수에서 슬랭/비격식 사용 확인
→ (AI 생성) 영어 정의: "To send a private message ... privately."
            slang_category: "internet_slang"
→ (AI 번역) 한국어 정의: "온라인에서 누군가에게 비공개 메시지를 보내는 것 ..."
→ (AI 생성) 예문: "I just sent you a dm about the meetup."
            번역: "모임 관련해서 방금 너한테 다이렉트 메시지 보냈어."
```

> 위 예시의 정의·예문 값은 실제 산출물 `service_public_approved.json`의 `dm` 레코드에서 가져온 것이다.

---

## 6. 정리

- 데이터 **수집·정제**는 규칙과 통계 기반으로 자동화되었고, **AI는 검증과 가공 단계의 언어적 판단**(슬랭 여부 판정, 정의·번역·카테고리·예문 생성)에 한정해 활용되었다.
- AI 사용 지점의 프롬프트는 모두 코드에 원문이 남아 있어, 보고서에 **실제 프롬프트를 그대로 인용**할 수 있다(새로 만들 필요 없음).
- 모든 AI 호출은 `temperature=0`과 JSON 강제 출력으로 일관성을 확보했고, 슬랭 판정·정의 생성은 "Reddit 실제 문맥을 근거로 하라"는 지시를 명시해 데이터 기반 생성이 되도록 설계되었다.

### `[확인 필요]` 목록
1. 실제 데이터 생성에 사용된 GPT 모델 버전(코드 기본값 `gpt-4.1-nano`이나 실행 시 변경 가능)
2. 코드의 프롬프트와 실제 실행 당시 프롬프트의 동일 여부(중간 수정·재실행 여부)
3. Reddit 덤프의 실제 수집 기간·범위
4. AI 생성 결과에 대한 사람 검수(review_tool) 시 별도 판단 기준
