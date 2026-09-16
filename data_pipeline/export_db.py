"""
export_db.py

final_dataset.jsonl → output/db_insert.json
DB slangs 테이블 INSERT용 데이터 변환.

실행:
  python data_pipeline/export_db.py [--subset final|auto|curated]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

INPUT_FILES = {
    "final":   Path("data_pipeline/output/final_dataset.jsonl"),
    "auto":    Path("data_pipeline/output/auto_approved.jsonl"),
    "curated": Path("data_pipeline/output/curated_dataset.jsonl"),
}
OUTPUT_PATH = Path("data_pipeline/output/db_insert.json")


def to_db_row(r: dict) -> dict:
    examples = r.get("reddit_examples") or []
    return {
        "word":          r["word"],
        "definition_en": r.get("definition_en") or None,
        "definition_ko": r.get("definition_ko") or None,
        "example_en":    examples[0] if examples else None,
        "example_ko":    None,
        "category":      r.get("category") or "Etc",
        "emoji":         None,
        "shorts_url":    None,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", choices=["final", "auto", "curated"], default="final")
    args = parser.parse_args()

    input_path = INPUT_FILES[args.subset]
    rows = []
    with input_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    print(f"[INFO] {len(rows)} words from {input_path.name}")

    db_rows = [to_db_row(r) for r in rows]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(db_rows, f, ensure_ascii=False, indent=2)

    no_ko  = sum(1 for r in db_rows if not r["definition_ko"])
    no_cat = sum(1 for r in db_rows if r["category"] == "Etc")
    print(f"[DONE] {len(db_rows)} rows → {OUTPUT_PATH}")
    print(f"  definition_ko 없음: {no_ko}개")
    print(f"  category='Etc'    : {no_cat}개")

    print("\n=== 샘플 ===")
    lookup = {r["word"]: r for r in db_rows}
    for w in ["lmao", "bro", "bet", "fire", "cap"]:
        e = lookup.get(w)
        if e:
            print(f"  {w:<10} category={e['category']}")
            print(f"             ko={str(e['definition_ko'])[:50]}")


if __name__ == "__main__":
    main()
