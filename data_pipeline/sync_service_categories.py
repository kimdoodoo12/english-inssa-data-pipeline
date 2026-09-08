"""서비스 산출물의 category 필드를 final_dataset.jsonl 기준으로 재동기화한다.

배경
    add_korean_definitions.py 는 final_dataset.jsonl 을 in-place 로 갱신한다.
    service_public_{approved,pending}.json 은 그 시점의 스냅샷에서 갈라져 나온 파생본이라,
    이후 --recategorize 가 final_dataset 에만 적용되면서 두 파일의 category 가 어긋났다.
    (quality_report.py 의 교차 검증에서 384건 중 209건 불일치로 검출됨)

    또한 LLM 응답의 category 를 검증 없이 받아온 탓에, 허용된 14개 밖의 값이 소수 섞여 있다.
    이 스크립트는 그 값들을 OUT_OF_VOCAB_MAP 에 따라 허용 어휘로 정규화한다.

기준
    category 필드의 소유자는 add_korean_definitions.py 이고, 그 출력 대상은 final_dataset.jsonl 이다.
    따라서 final_dataset.jsonl 을 단일 진실 공급원(source of truth)으로 삼는다.

사용법
    python data_pipeline/sync_service_categories.py            # dry-run (기본, 파일 변경 없음)
    python data_pipeline/sync_service_categories.py --apply    # 실제 반영
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

OUTPUT_DIR = Path("data_pipeline/output")

FINAL_PATH = OUTPUT_DIR / "final_dataset.jsonl"
DERIVED_PATHS = [
    OUTPUT_DIR / "service_public_approved.json",
    OUTPUT_DIR / "service_public_pending.json",
]

# add_korean_definitions.py 의 CATEGORIES_KO 와 동일해야 한다.
ALLOWED_CATEGORIES = [
    "칭찬·인정",
    "긍정·동의",
    "감탄·놀람",
    "강조 표현",
    "일상 대화",
    "SNS·인터넷 반응",
    "줄임말·약어",
    "감정 표현",
    "비판·부정 반응",
    "관계·연애",
    "유머·밈",
    "게임·커뮤니티",
    "돈·라이프스타일",
    "주의/거친 표현",
]

# 허용 어휘 밖의 값 → 허용 어휘 매핑.
# 대상 6건: batter(감각·신체), bless(감사·인사), rubber/hump/poppers(성·성적 표현), wicket(스포츠·게임)
OUT_OF_VOCAB_MAP = {
    "성·성적 표현": "주의/거친 표현",
    "감각·신체": "주의/거친 표현",
    "감사·인사": "일상 대화",
    "스포츠·게임": "게임·커뮤니티",
    "격려·응원": "강조 표현",
    "인터넷 반응": "SNS·인터넷 반응",  # 재분류 실행 중 관측된 절단 형태
}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def normalize_categories(cats: Any) -> List[str]:
    """허용 어휘 밖의 값을 매핑하고, 순서를 유지한 채 중복을 제거한다."""
    if not isinstance(cats, list):
        return []
    allowed = set(ALLOWED_CATEGORIES)
    out: List[str] = []
    for c in cats:
        mapped = OUT_OF_VOCAB_MAP.get(c, c)
        if mapped in allowed and mapped not in out:
            out.append(mapped)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 파일에 반영한다 (미지정 시 dry-run)",
    )
    args = parser.parse_args()
    dry = not args.apply

    print("=" * 60)
    print("DRY-RUN — 파일을 변경하지 않습니다" if dry else "APPLY — 파일을 변경합니다")
    print("=" * 60)

    # ---- 1. final_dataset 의 허용 어휘 밖 값 정규화 ----
    final_rows = load_jsonl(FINAL_PATH)
    final_fixed = []
    for r in final_rows:
        before = r.get("category")
        after = normalize_categories(before)
        if before != after:
            final_fixed.append((r["word"], before, after))
            r["category"] = after

    print(f"\n[1] {FINAL_PATH.name} — 허용 어휘 정규화: {len(final_fixed)}건")
    for w, b, a in final_fixed:
        print(f"    {w:<12} {b} -> {a}")

    # ---- 2. 파생본 재동기화 ----
    source = {r["word"]: r.get("category", []) for r in final_rows}
    pending = 0

    for path in DERIVED_PATHS:
        rows = load_json(path)
        changed = []
        orphan = []
        for r in rows:
            word = r.get("word")
            if word not in source:
                orphan.append(word)
                continue
            before = r.get("category")
            after = source[word]
            if before != after:
                changed.append((word, before, after))
                r["category"] = list(after)

        print(f"\n[2] {path.name} — {len(rows):,}건 중 {len(changed):,}건 갱신")
        if orphan:
            print(f"    ⚠️ final_dataset 에 없는 단어 {len(orphan)}건: {orphan[:5]}")
        for w, b, a in changed[:10]:
            print(f"    {w:<12} {b} -> {a}")
        if len(changed) > 10:
            print(f"    ... 외 {len(changed) - 10:,}건")

        pending += len(changed)

        if not dry:
            write_json(path, rows)

    pending += len(final_fixed)

    if not dry:
        write_jsonl(FINAL_PATH, final_rows)
        print(f"\n[DONE] {FINAL_PATH.name} 및 파생본 {len(DERIVED_PATHS)}개 저장 완료")
        return

    if pending:
        print(f"\n[DRY-RUN] 미반영 변경 {pending:,}건 — --apply 를 붙이면 반영됩니다")
        # CI 에서 파생본이 어긋난 채 병합되는 것을 막는다.
        raise SystemExit(1)

    print("\n[OK] 모든 산출물의 category 가 동기화되어 있습니다")


if __name__ == "__main__":
    main()
