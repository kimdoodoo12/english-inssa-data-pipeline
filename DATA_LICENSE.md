# 데이터 출처 및 라이선스

이 저장소의 **코드**는 MIT 라이선스를 따른다 ([LICENSE](LICENSE)).
`data_pipeline/output/`와 `data_pipeline/data/` 아래의 **데이터셋**은 아래 출처에서 파생된 것으로, 별도의 조건이 적용된다.

---

## 1. Wiktionary (영어 표제어·정의)

| 항목 | 내용 |
|------|------|
| 출처 | [English Wiktionary](https://en.wiktionary.org/) — `enwiktionary-20250920-pages-articles-multistream.xml.bz2` |
| 라이선스 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 및 [GFDL](https://www.gnu.org/licenses/fdl-1.3.html) |
| 사용 범위 | 슬랭 후보 표제어(`word`)와 원본 영어 정의·예문·라벨 추출 (Stage 1) |

Wiktionary는 **동일조건변경허락(ShareAlike)** 조건이 붙는다. 따라서 Wiktionary에서 파생된 필드를
재배포할 경우 **CC BY-SA 4.0을 그대로 승계**해야 하며, 출처를 표기해야 한다.

해당 필드·파일:

| 파일 | Wiktionary에서 온 내용 |
|------|------------------------|
| `final_dataset.jsonl` 등 `output/` 아래 파일 | 표제어 `word` |
| `data_pipeline/data/scored_candidates.json.gz` (Stage 4 결과, 15,198단어) | 표제어(`normalized_word`, `surface_forms`), **Wiktionary 정의문과 예문**(`definitions[].definition_en`, `definitions[].example_en`, 마크업만 제거한 텍스트), 원본 라벨(`source_label`) |
| `data_pipeline/data/scored_candidates_sample.json` | 위 파일에서 발췌한 50단어 |
| `data_pipeline/data/filter_summary.json` | 표제어와 건수 집계만 있고 정의·예문은 없다 |

`scored_candidates.json.gz`와 `scored_candidates_sample.json`은 Wiktionary 텍스트를 그대로 담고 있으므로
**CC BY-SA 4.0 승계 대상**이다.

> `definition_en`은 Stage 6에서 Reddit 문맥을 근거로 LLM이 새로 생성한 것이지, Wiktionary 정의를
> 그대로 옮긴 것이 아니다. 다만 후보 선정 자체가 Wiktionary에 의존하므로, 데이터셋 전체를
> CC BY-SA 4.0으로 배포하는 것이 안전한 해석이다.

## 2. Reddit 댓글 덤프 (사용 빈도·문맥)

| 항목 | 내용 |
|------|------|
| 출처 | Reddit 월간 댓글 덤프 `RC_2025-09`, `RC_2025-12` (Pushshift 계열 아카이브) |
| 사용 범위 | 단어별 출현 횟수(`match_count`), subreddit 수, 슬랭 판정용 문맥 샘플링 (Stage 2·5·6) |

### ⚠️ 원문 포함 현황

대부분의 산출물은 집계 통계만 담고 있으나, **두 파일의 `example_en` 필드에는 Reddit 댓글
원문이 들어 있다.** (`build_final_dataset.py`가 길이 15~350자 범위의 댓글을 골라 담는다. 저장소에는 이 문장들만 있고 수집 문맥 전체는 없다.)

| 파일 | `example_en` 출처 | 원문 포함 |
|------|-------------------|-----------|
| `service_public_approved.json` (384) | LLM이 생성한 학습용 예문 | 아니오 |
| `service_public_pending.json` (2,909) | **Reddit 댓글 원문** | **예** |
| `db_insert_draft.json` (3,293) | **Reddit 댓글 원문** | **예** |
| `final_dataset.jsonl`, `ranked_candidates.jsonl`, `word_summary.jsonl` | 해당 필드 없음 | 아니오 |
| `data/scored_candidates.json.gz`, `data/scored_candidates_sample.json`, `data/filter_summary.json` | `definitions[].example_en` 필드는 있으나 Reddit 원문이 아니라 Wiktionary 예문임 (그 외 건수 정보) | 아니오 |

수집 문맥 전체를 담은 `candidate_context_cache.jsonl`(~643MB)은 `.gitignore` 대상이라
저장소에 없다. 그러나 위 두 파일을 통해 댓글 원문이 재배포되고 있다. `service_public_pending.json`의 2,909건은
`db_insert_draft.json`의 같은 단어 항목과 동일한 문장이므로, 서로 다른 댓글은 3,293건이다.

이 원문에는 비속어·성적 표현, 실존 인물을 지칭하는 내용, 차별적 표현이 포함되어 있다.
Reddit 이용약관상 사용자 생성 콘텐츠의 대량 재배포는 제한되며, 공개 저장소에서는
개인정보·콘텐츠 적절성 문제가 발생할 수 있다.

**→ 해소 방법**: 두 파일의 `example_en` 을 제거하거나, `service_public_approved.json`
처럼 LLM 생성 예문으로 대체한다. 단어별 통계(`match_count` 등)는 원문이 아니므로 영향받지 않는다.

## 3. LLM 생성 필드

| 필드 | 생성 단계 | 모델 (코드 기본값) |
|------|-----------|------|
| `definition_en` | Stage 6 | gpt-4.1-nano |
| `definition_ko`, `category` | `add_korean_definitions.py` | gpt-4.1-nano |
| `example_en`, `example_ko` | `generate_examples.py` | gpt-4.1-nano |

OpenAI 이용약관상 출력물의 권리는 이용자에게 귀속된다. 다만 위 1항의 ShareAlike 조건이
데이터셋 전체에 미치는 범위를 고려해 함께 배포한다.

---

## 요약

```
코드          → MIT
데이터셋      → CC BY-SA 4.0 (Wiktionary 승계)
Reddit 원문   → service_public_pending.json, db_insert_draft.json 의 example_en 에 포함 (조치 필요)
```

이 데이터셋을 사용할 경우 다음과 같이 출처를 표기하면 된다.

> Slang candidates derived from English Wiktionary (CC BY-SA 4.0).
> Usage frequency computed from public Reddit comment archives.
