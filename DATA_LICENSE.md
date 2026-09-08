# 데이터 출처 및 라이선스

이 저장소의 **코드**는 MIT 라이선스를 따른다 ([LICENSE](LICENSE)).
`data_pipeline/output/` 아래의 **데이터셋**은 아래 출처에서 파생된 것으로, 별도의 조건이 적용된다.

---

## 1. Wiktionary (영어 표제어·정의)

| 항목 | 내용 |
|------|------|
| 출처 | [English Wiktionary](https://en.wiktionary.org/) — `enwiktionary-20250920-pages-articles-multistream.xml.bz2` |
| 라이선스 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 및 [GFDL](https://www.gnu.org/licenses/fdl-1.3.html) |
| 사용 범위 | 슬랭 후보 표제어(`word`)와 원본 영어 정의 추출 (Stage 1) |

Wiktionary는 **동일조건변경허락(ShareAlike)** 조건이 붙는다. 따라서 Wiktionary에서 파생된 필드를
재배포할 경우 **CC BY-SA 4.0을 그대로 승계**해야 하며, 출처를 표기해야 한다.

해당 필드: `word`

> `definition_en`은 Stage 6에서 Reddit 문맥을 근거로 LLM이 새로 생성한 것이지, Wiktionary 정의를
> 그대로 옮긴 것이 아니다. 다만 후보 선정 자체가 Wiktionary에 의존하므로, 데이터셋 전체를
> CC BY-SA 4.0으로 배포하는 것이 안전한 해석이다.

## 2. Reddit 댓글 덤프 (사용 빈도·문맥)

| 항목 | 내용 |
|------|------|
| 출처 | Reddit 월간 댓글 덤프 `RC_2025-09`, `RC_2025-12` (Pushshift 계열 아카이브) |
| 사용 범위 | 단어별 출현 횟수(`match_count`), subreddit 수, 슬랭 판정용 문맥 샘플링 (Stage 2·5·6) |
| 재배포 | **하지 않음** |

**이 저장소에 커밋된 산출물에는 Reddit 원문 댓글 텍스트가 포함되어 있지 않다.**
Reddit 데이터는 집계 통계(출현 횟수, subreddit 개수)와 LLM 판정의 중간 입력으로만 쓰였고,
원문을 담은 `candidate_context_cache.jsonl`(~643MB)은 `.gitignore` 대상이라 저장소에 없다.

따라서 사용자 생성 콘텐츠의 재배포나 개인정보 노출 문제는 발생하지 않는다.

## 3. LLM 생성 필드

| 필드 | 생성 단계 | 모델 |
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
Reddit 원문   → 미포함 (통계만 사용)
```

이 데이터셋을 사용할 경우 다음과 같이 출처를 표기하면 된다.

> Slang candidates derived from English Wiktionary (CC BY-SA 4.0).
> Usage frequency computed from public Reddit comment archives.
