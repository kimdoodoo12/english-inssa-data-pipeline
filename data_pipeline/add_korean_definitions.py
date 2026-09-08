"""
add_korean_definitions.py

final_dataset.jsonl의 각 단어에 definition_ko (한국어 정의) 및 category 필드를 추가.

배치 처리로 API 비용 최소화:
  - GPT-4.1-nano 사용 (저비용)
  - 20개 단어씩 배치 → 전체 ~3300개 = 약 165회 API 호출
  - 예상 비용: < $0.10 USD

실행:
  python data_pipeline/add_korean_definitions.py
  python data_pipeline/add_korean_definitions.py --recategorize
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from openai import OpenAI

FINAL_PATH = Path("data_pipeline/output/final_dataset.jsonl")

OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
BATCH_SIZE     = 20
API_MAX_RETRY  = 4
API_RETRY_BASE = 2.0

CATEGORIES_KO = [
    "칭찬·인정", "긍정·동의", "감탄·놀람", "강조 표현",
    "일상 대화", "SNS·인터넷 반응", "줄임말·약어", "감정 표현",
    "비판·부정 반응", "관계·연애", "유머·밈", "게임·커뮤니티",
    "돈·라이프스타일", "주의/거친 표현",
]
CATEGORY_SET = set(CATEGORIES_KO)

# LLM 이 반복적으로 만들어낸 허용 어휘 밖의 값 → 허용 어휘 매핑.
# 프롬프트로만 제약하면 소수가 새어 나오므로 응답 단계에서 흡수한다.
CATEGORY_ALIASES = {
    "성·성적 표현": "주의/거친 표현",
    "감각·신체": "주의/거친 표현",
    "감사·인사": "일상 대화",
    "스포츠·게임": "게임·커뮤니티",
    "격려·응원": "강조 표현",
    "인터넷 반응": "SNS·인터넷 반응",  # 재분류 실행 중 관측된 절단 형태
}

# 검증 과정에서 버려진 값 집계 (실행 종료 시 리포트)
REJECTED_CATEGORIES: Counter = Counter()


def validate_categories(word: str, cats: Any) -> list[str]:
    """LLM 이 반환한 category 를 허용 어휘로 강제한다.

    프롬프트에 목록을 넣어도 모델이 목록 밖의 값을 만들어내므로, 응답을 그대로 신뢰하지 않는다.
    별칭은 매핑하고, 매핑도 안 되는 값은 버린 뒤 REJECTED_CATEGORIES 에 기록한다.
    순서는 유지하고 중복은 제거하며, 최대 3개로 자른다.
    """
    if not isinstance(cats, list):
        REJECTED_CATEGORIES[repr(cats)] += 1
        return []

    out: list[str] = []
    for c in cats:
        if not isinstance(c, str):
            REJECTED_CATEGORIES[repr(c)] += 1
            continue
        c = c.strip()
        mapped = CATEGORY_ALIASES.get(c, c)
        if mapped not in CATEGORY_SET:
            REJECTED_CATEGORIES[c] += 1
            continue
        if mapped not in out:
            out.append(mapped)
    if not out:
        print(f"  [WARN] {word}: 유효한 category 없음 (원본={cats})")
    return out[:3]

SYSTEM_PROMPT = (
    "You are a Korean-English bilingual dictionary editor for a language learning app targeting Korean adults. "
    "For each slang word, do two things:\n"
    "1. Translate the English definition to natural, concise Korean (1–2 sentences max).\n"
    "2. Pick the best category from this list: "
    + ", ".join(f'"{c}"' for c in CATEGORIES_KO)
    + "\n\nRules for translation:\n"
    "- Use Korean only (no English unless the slang term itself is kept).\n"
    "- Capture the SLANG meaning, not the literal dictionary meaning.\n"
    "- Do NOT explain etymology or origin.\n\n"
    "Rules for category:\n"
    "- Choose 1 to 3 categories from the provided list (most relevant first).\n"
    "- Return as a JSON array, e.g. [\"긍정·동의\", \"일상 대화\"].\n"
    "- Base it on how the word is actually used, not its literal meaning."
)

RECATEGORIZE_SYSTEM = (
    "You are a categorization expert for a Korean English slang learning app. "
    "For each slang word, pick the best category from this list: "
    + ", ".join(f'"{c}"' for c in CATEGORIES_KO)
    + "\n\nRules:\n"
    "- Choose 1 to 3 categories (most relevant first).\n"
    "- Return as a JSON array.\n"
    "- '줄임말·약어': the word is formed from initials or shortened letters of a phrase. "
    "Check the definition — if it says 'acronym for', 'stands for', or spells out a full phrase "
    "(like 'Greatest Of All Time', 'Fear Of Missing Out'), include '줄임말·약어'.\n"
    "- Always pair '줄임말·약어' with a meaning-based category.\n"
    "- Regular slang words that are NOT abbreviations (sus, cap, rizz, slay) do NOT get '줄임말·약어'."
)

RECATEGORIZE_TMPL = (
    "Categorize these slang words. "
    "Return JSON: {{\"results\": [{{\"word\": \"...\", \"category\": [\"...\", \"...\"]}}, ...]}}\n\n"
    "{items}"
)

USER_TEMPLATE = (
    "Process these slang words. "
    "Return JSON: {{\"results\": [{{\"word\": \"...\", \"definition_ko\": \"...\", \"category\": [\"...\", \"...\"]}}, ...]}}\n\n"
    "{items}"
)


def build_batch_prompt(batch: list[dict]) -> str:
    lines = []
    for item in batch:
        lines.append(f'- word: "{item["word"]}", definition_en: "{item["definition_en"]}"')
    return USER_TEMPLATE.format(items="\n".join(lines))


def call_llm(client: OpenAI, batch: list[dict]) -> dict[str, dict]:
    prompt = build_batch_prompt(batch)
    max_tok = len(batch) * 110 + 200

    last_err: Exception | None = None
    for attempt in range(1, API_MAX_RETRY + 1):
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tok,
                temperature=0,
            )
            content = resp.choices[0].message.content or ""
            payload = json.loads(content)
            results = payload.get("results", [])
            return {
                r["word"]: {
                    "definition_ko": r.get("definition_ko", ""),
                    "category": validate_categories(r["word"], r.get("category")),
                }
                for r in results if "word" in r and r.get("definition_ko")
            }
        except Exception as exc:
            last_err = exc
            if attempt < API_MAX_RETRY:
                time.sleep(API_RETRY_BASE * (2 ** (attempt - 1)))
    raise RuntimeError(f"LLM call failed after {API_MAX_RETRY} attempts: {last_err}")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def call_llm_recategorize(client: OpenAI, batch: list[dict]) -> dict[str, list]:
    items = "\n".join(f'- word: "{r["word"]}", definition_en: "{r.get("definition_en","")}"' for r in batch)
    prompt = RECATEGORIZE_TMPL.format(items=items)
    max_tok = len(batch) * 40 + 100
    last_err: Exception | None = None
    for attempt in range(1, API_MAX_RETRY + 1):
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": RECATEGORIZE_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tok,
                temperature=0,
            )
            content = resp.choices[0].message.content or ""
            payload = json.loads(content)
            results = payload.get("results", [])
            return {
                r["word"]: validate_categories(r["word"], r.get("category", []))
                for r in results if "word" in r
            }
        except Exception as exc:
            last_err = exc
            if attempt < API_MAX_RETRY:
                time.sleep(API_RETRY_BASE * (2 ** (attempt - 1)))
    raise RuntimeError(f"LLM recategorize failed after {API_MAX_RETRY} attempts: {last_err}")


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--recategorize", action="store_true",
                        help="category만 별도 프롬프트로 재분류 (definition_ko 유지)")
    parser.add_argument("--limit", type=int, default=None,
                        help="처리할 단어 수 상한 (소규모 검증용). --recategorize 와 함께 사용")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    # ── recategorize 모드 ────────────────────────────────────────────────────────
    if args.recategorize:
        final_rows = load_jsonl(FINAL_PATH)
        targets = final_rows if args.limit is None else final_rows[:args.limit]
        if args.limit is not None:
            print(f"[INFO] recategorize 모드 | --limit {args.limit} → 상위 {len(targets)}개만 처리")
        else:
            print(f"[INFO] recategorize 모드 | {len(targets)}개 전체 재분류")
        client = OpenAI(api_key=api_key)
        done = 0
        unchanged = 0
        try:
            for i in range(0, len(targets), BATCH_SIZE):
                batch = targets[i:i + BATCH_SIZE]
                cat_map = call_llm_recategorize(client, batch)
                for r in batch:
                    if r["word"] in cat_map:
                        r["category"] = cat_map[r["word"]]
                    else:
                        unchanged += 1
                done += len(batch)
                # 중간 저장: 165회 호출 중 실패해도 진행분을 잃지 않는다.
                if (i // BATCH_SIZE) % 10 == 9:
                    write_jsonl(final_rows, FINAL_PATH)
                print(f"  [{done}/{len(targets)}] 완료")
        finally:
            write_jsonl(final_rows, FINAL_PATH)

        print(f"\n[DONE] category 재분류 완료 → {FINAL_PATH}")
        if unchanged:
            print(f"[WARN] LLM 응답에 없어 기존 category 를 유지한 단어: {unchanged}개")
        if REJECTED_CATEGORIES:
            total_rejected = sum(REJECTED_CATEGORIES.values())
            print(f"[VALIDATION] 허용 어휘 밖이라 버린 값 {total_rejected}건:")
            for c, n in REJECTED_CATEGORIES.most_common():
                print(f"    {c}: {n}")
        else:
            print("[VALIDATION] 허용 어휘 위반 없음")
        print("\n=== 샘플 ===")
        lookup = {r["word"]: r for r in final_rows}
        for w in ["lmao", "omg", "wtf", "goat", "fomo", "dm", "rizz", "bet", "sus", "cap"]:
            r = lookup.get(w)
            if r:
                print(f"  {w:<10} {r.get('category', [])}")
        return

    rows = load_jsonl(FINAL_PATH)
    print(f"[INFO] Model: {OPENAI_MODEL} | Words: {len(rows)}")

    # 이미 처리된 단어 스킵
    existing: dict[str, dict] = {}
    for r in rows:
        if r.get("definition_ko") and isinstance(r.get("category"), list):
            existing[r["word"]] = {
                "definition_ko": r["definition_ko"],
                "category": r["category"],
            }
    print(f"[INFO] 이미 처리된 단어: {len(existing)}개")

    client = OpenAI(api_key=api_key)

    # 번역 대상 필터
    to_translate = [r for r in rows if r["word"] not in existing]
    print(f"[INFO] 번역 필요: {len(to_translate)}개")

    # 배치 번역
    translations: dict[str, dict] = dict(existing)
    done = 0
    for i in range(0, len(to_translate), BATCH_SIZE):
        batch = to_translate[i:i + BATCH_SIZE]
        result_map = call_llm(client, batch)
        translations.update(result_map)
        done += len(batch)
        print(f"  [{done}/{len(to_translate)}] 완료")

    # final_dataset에 definition_ko + category 삽입
    updated_final = []
    for r in rows:
        info = translations.get(r["word"])
        if info:
            r["definition_ko"] = info["definition_ko"]
            r["category"]      = info["category"]
        updated_final.append(r)

    write_jsonl(updated_final, FINAL_PATH)
    print(f"\n[DONE] final_dataset.jsonl updated — definition_ko + category 추가됨")

    # 샘플 출력
    print("\n=== 샘플 ===")
    samples = ["fire", "vibe", "bet", "rizz", "slay", "cap", "sus", "cringe", "mid", "goat"]
    lookup = {r["word"]: r for r in updated_final}
    for w in samples:
        e = lookup.get(w)
        if e and e.get("definition_ko"):
            print(f"  {w:<10} [{e.get('category','?')}] {e['definition_ko'][:40]}")
        elif e:
            print(f"  {w:<10} (처리 안됨)")


if __name__ == "__main__":
    main()
