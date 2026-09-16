"""
build_final_dataset.py

Assembles the final learning dataset by combining:
- ranked_candidates.jsonl   → word priority ranking
- word_summary.jsonl        → LLM-generated definition_en + slang_category
- context_judgments.jsonl   → is_slang=True contexts for reddit_examples

Output: data_pipeline/output/final_dataset.jsonl
        data_pipeline/output/curated_dataset.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

INPUT_RANKED = Path("data_pipeline/output/ranked_candidates.jsonl")
INPUT_JUDGMENTS = Path("data_pipeline/output/context_judgments.jsonl")
INPUT_WORD_SUMMARY = Path("data_pipeline/output/word_summary.jsonl")
OUTPUT_PATH = Path("data_pipeline/output/final_dataset.jsonl")
OUTPUT_CURATED_PATH = Path("data_pipeline/output/curated_dataset.jsonl")

# slang_category → slang_tier 매핑
PURE_SLANG_CATEGORIES = {"internet_slang", "aave", "general_slang"}
INFORMAL_CATEGORIES = {"colloquial"}

CURATE_PURE_SLANG_MIN_RATIO = 0.4
CURATE_INFORMAL_MIN_RATIO = 0.7

MIN_EXAMPLE_LEN = 15
MAX_EXAMPLE_LEN = 350
TARGET_EXAMPLES = 5


def load_ranked(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_word_summary(path: Path) -> dict[str, dict]:
    """Return {word: {definition_en, slang_category}} from word_summary.jsonl."""
    result: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            nw = str(row.get("normalized_word", "")).strip()
            if nw:
                result[nw] = {
                    "definition_en": row.get("definition_en") or "",
                    "slang_category": row.get("slang_category"),
                }
    return result


def load_judgments(path: Path) -> dict[str, list[str]]:
    """Return {word: [context_text, ...]} for is_slang=True contexts only."""
    result: dict[str, list[str]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("is_slang"):
                nw = str(row.get("normalized_word", "")).strip()
                text = str(row.get("context_text", "")).strip()
                if nw and text:
                    result[nw].append(text)
    return result


def get_slang_tier(slang_category: str | None) -> str:
    if slang_category in PURE_SLANG_CATEGORIES:
        return "pure_slang"
    if slang_category in INFORMAL_CATEGORIES:
        return "informal_slang"
    return "unlabeled"


def select_examples(
    slang_texts: list[str],
    n: int = TARGET_EXAMPLES,
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for text in slang_texts:
        if MIN_EXAMPLE_LEN <= len(text) <= MAX_EXAMPLE_LEN:
            key = text[:60].lower()
            if key not in seen:
                seen.add(key)
                result.append(text)
        if len(result) >= n:
            break
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[INFO] Loading ranked_candidates...")
    ranked = load_ranked(INPUT_RANKED)
    print(f"[INFO] {len(ranked)} words")

    print("[INFO] Loading word_summary (definitions)...")
    word_summary = load_word_summary(INPUT_WORD_SUMMARY)
    print(f"[INFO] {len(word_summary)} words with definitions")

    print("[INFO] Loading context_judgments (is_slang=True)...")
    judgments = load_judgments(INPUT_JUDGMENTS)
    print(f"[INFO] {len(judgments)} words with slang contexts")

    rows = []
    tier_counts: dict[str, int] = {"pure_slang": 0, "informal_slang": 0, "unlabeled": 0}
    no_examples_count = 0
    no_definition_count = 0

    for entry in ranked:
        word = entry["normalized_word"]
        ws = word_summary.get(word, {})
        definition_en = ws.get("definition_en") or ""
        slang_category = ws.get("slang_category") or entry.get("slang_category")

        if not definition_en:
            no_definition_count += 1

        slang_tier = get_slang_tier(slang_category)
        tier_counts[slang_tier] += 1

        slang_texts = judgments.get(word, [])
        reddit_examples = select_examples(slang_texts)
        if not reddit_examples:
            no_examples_count += 1

        rows.append({
            "word": word,
            "rank": entry["rank"],
            "priority_score": entry["priority_score"],
            "slang_tier": slang_tier,
            "slang_category": slang_category,
            "definition_en": definition_en,
            "reddit_examples": reddit_examples,
            "slang_hits": entry["slang_hits"],
            "slang_ratio": entry["slang_ratio"],
            "match_count": entry["match_count"],
            "is_vulgar": entry.get("is_vulgar", False),
        })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n[DONE] {len(rows)} words -> {OUTPUT_PATH}")
    print(f"  pure_slang    : {tier_counts['pure_slang']}")
    print(f"  informal_slang: {tier_counts['informal_slang']}")
    print(f"  unlabeled     : {tier_counts['unlabeled']}")
    print(f"  no reddit examples  : {no_examples_count}")
    print(f"  no definition_en    : {no_definition_count}")

    lookup = {r["word"]: r for r in rows}
    check = ["fire", "vibe", "lmao", "bet", "rizz", "ngl", "dude", "cringe", "sus", "cap"]
    print("\n=== SPOT CHECK ===")
    for w in check:
        e = lookup.get(w)
        if not e:
            print(f"  {w:<12} N/A")
            continue
        ex = e["reddit_examples"][0][:70] + "..." if e["reddit_examples"] else "(no examples)"
        print(f"  {w:<12} rank={e['rank']:<5} tier={e['slang_tier']:<16} cat={e['slang_category']}")
        print(f"               def=\"{e['definition_en'][:55]}\"")
        print(f"               reddit: {ex}")

    def _is_curated(r: dict) -> bool:
        if r["slang_tier"] == "pure_slang":
            return r["slang_ratio"] >= CURATE_PURE_SLANG_MIN_RATIO
        if r["slang_tier"] == "informal_slang":
            return r["slang_ratio"] >= CURATE_INFORMAL_MIN_RATIO
        return False

    curated = [r for r in rows if _is_curated(r)]
    with OUTPUT_CURATED_PATH.open("w", encoding="utf-8") as f:
        for row in curated:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n[CURATED] {len(curated)} words -> {OUTPUT_CURATED_PATH}")
    print(f"  pure_slang (ratio>={CURATE_PURE_SLANG_MIN_RATIO})  : "
          f"{sum(1 for r in curated if r['slang_tier'] == 'pure_slang')}개")
    print(f"  informal_slang (ratio>={CURATE_INFORMAL_MIN_RATIO}): "
          f"{sum(1 for r in curated if r['slang_tier'] == 'informal_slang')}개")


if __name__ == "__main__":
    main()
