"""파이프라인 산출물 품질 리포트.

data_pipeline/output/ 에 커밋된 파일만 읽으므로, clone 직후 외부 데이터 없이 실행된다.

    python data_pipeline/quality_report.py
    python data_pipeline/quality_report.py --format md > docs/QUALITY_REPORT.md

집계 항목
  1. 단계별 퍼널 (Stage 6 판정 → Stage 7 랭킹 → 수동 검수)
  2. slang_category / difficulty_tier / is_vulgar 분포
  3. 서비스 카테고리 분포 및 **어휘 검증** (허용된 14개 밖의 값 탐지)
  4. final_dataset ↔ service_public_approved 카테고리 정합성 교차 검증
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

OUTPUT_DIR = Path("data_pipeline/output")

WORD_SUMMARY_PATH = OUTPUT_DIR / "word_summary.jsonl"
RANKED_PATH = OUTPUT_DIR / "ranked_candidates.jsonl"
FINAL_PATH = OUTPUT_DIR / "final_dataset.jsonl"
APPROVED_PATH = OUTPUT_DIR / "service_public_approved.json"
PENDING_PATH = OUTPUT_DIR / "service_public_pending.json"

# add_korean_definitions.py 가 프롬프트로 강제하는 허용 카테고리.
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


# =========================
# Loading
# =========================
def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_json_array(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# =========================
# Rendering
# =========================
def render_counter(
    counter: Counter,
    total: int,
    label: str,
    fmt: str,
    flag: set | None = None,
) -> List[str]:
    lines = []
    if fmt == "md":
        lines.append(f"| {label} | 건수 | 비율 |")
        lines.append("|---|---:|---:|")
    for key, n in counter.most_common():
        mark = " ⚠️" if flag and key in flag else ""
        pct = (n / total * 100) if total else 0.0
        if fmt == "md":
            lines.append(f"| {key}{mark} | {n:,} | {pct:.1f}% |")
        else:
            lines.append(f"  {str(key) + mark:<24} {n:>7,}  {pct:>5.1f}%")
    return lines


def section(title: str, fmt: str) -> List[str]:
    return [f"\n## {title}\n"] if fmt == "md" else [f"\n=== {title} ==="]


# =========================
# Checks
# =========================
def category_pairs(records: List[Dict[str, Any]], key: str = "word") -> Dict[str, Tuple[str, ...]]:
    """word -> category 튜플. category 가 없으면 제외."""
    out: Dict[str, Tuple[str, ...]] = {}
    for r in records:
        cats = r.get("category")
        if isinstance(cats, list):
            out[r[key]] = tuple(cats)
    return out


def build_report(fmt: str) -> str:
    lines: List[str] = []
    lines.append("# 파이프라인 품질 리포트" if fmt == "md" else "파이프라인 품질 리포트")

    # ---- 1. Stage 6 판정 분포 ----
    summary = list(iter_jsonl(WORD_SUMMARY_PATH))
    decisions = Counter(r["decision"] for r in summary)
    lines += section(f"Stage 6 — LLM 판정 ({len(summary):,}개 후보)", fmt)
    lines += render_counter(decisions, len(summary), "decision", fmt)

    # ---- 2. Stage 7 랭킹 ----
    ranked = list(iter_jsonl(RANKED_PATH))
    holdout = Counter(
        "holdout 포함" if r.get("holdout_included") else "enough_evidence"
        for r in ranked
    )
    lines += section(f"Stage 7 — 최종 랭킹 ({len(ranked):,}개)", fmt)
    lines += render_counter(holdout, len(ranked), "출처", fmt)

    holdout_total = decisions.get("holdout", 0)
    holdout_kept = sum(1 for r in ranked if r.get("holdout_included"))
    lines.append(
        f"\nholdout {holdout_total:,}개 중 {holdout_kept:,}개 통과, "
        f"{holdout_total - holdout_kept:,}개 탈락 "
        f"(기준: slang_hits >= 3 AND slang_ratio >= 1.67%)"
    )

    # ---- 3. 최종 데이터셋 분포 ----
    final = list(iter_jsonl(FINAL_PATH))
    lines += section(f"최종 데이터셋 ({len(final):,}개)", fmt)

    lines.append("\n**slang_category**\n" if fmt == "md" else "\n[slang_category]")
    lines += render_counter(
        Counter(r.get("slang_category") for r in final), len(final), "slang_category", fmt
    )

    lines.append("\n**difficulty_tier**\n" if fmt == "md" else "\n[difficulty_tier]")
    lines += render_counter(
        Counter(r.get("difficulty_tier") for r in final), len(final), "difficulty_tier", fmt
    )

    vulgar = sum(1 for r in final if r.get("is_vulgar"))
    lines.append(f"\nis_vulgar: {vulgar:,}개 ({vulgar / len(final) * 100:.1f}%)")

    # ---- 4. 서비스 카테고리 + 어휘 검증 ----
    allowed = set(ALLOWED_CATEGORIES)
    final_cat_counter: Counter = Counter()
    for r in final:
        for c in r.get("category") or []:
            final_cat_counter[c] += 1
    out_of_vocab = {c for c in final_cat_counter if c not in allowed}

    lines += section("서비스 카테고리 분포 (final_dataset, 중복 포함)", fmt)
    lines += render_counter(
        final_cat_counter, sum(final_cat_counter.values()), "category", fmt, flag=out_of_vocab
    )

    lines += section("어휘 검증", fmt)
    if out_of_vocab:
        affected = sum(final_cat_counter[c] for c in out_of_vocab)
        lines.append(
            f"\n❌ 허용된 {len(ALLOWED_CATEGORIES)}개 밖의 카테고리 "
            f"{len(out_of_vocab)}종, {affected}건 발견"
        )
        for c in sorted(out_of_vocab):
            words = [r["word"] for r in final if c in (r.get("category") or [])]
            lines.append(f"  - {c}: {', '.join(words)}")
    else:
        lines.append("\n✅ 모든 카테고리가 허용 목록 내에 있음")

    # ---- 5. 교차 검증: final_dataset ↔ approved ----
    approved = load_json_array(APPROVED_PATH)
    pending = load_json_array(PENDING_PATH)
    lines += section("수동 검수 결과", fmt)
    lines.append(
        f"\n승인 {len(approved):,} / 보류 {len(pending):,} "
        f"= {len(approved) + len(pending):,} (final_dataset {len(final):,})"
    )

    fd_cats = category_pairs(final)
    ap_cats = category_pairs(approved)

    lines += section("교차 검증 — final_dataset ↔ service_public_approved", fmt)
    missing = [w for w in ap_cats if w not in fd_cats]
    common = [w for w in ap_cats if w in fd_cats]
    mismatched = [w for w in common if ap_cats[w] != fd_cats[w]]

    if missing:
        lines.append(f"\n❌ approved 에만 있고 final_dataset 에 없는 단어: {len(missing)}개")
    if mismatched:
        pct = len(mismatched) / len(common) * 100
        lines.append(
            f"\n❌ 두 파일의 category 값이 다른 단어: "
            f"{len(mismatched):,} / {len(common):,} ({pct:.1f}%)"
        )
        lines.append("\n예시 (최대 10건):")
        for w in mismatched[:10]:
            lines.append(
                f"  - {w}: approved={list(ap_cats[w])}  final_dataset={list(fd_cats[w])}"
            )
    if not missing and not mismatched:
        lines.append("\n✅ 두 파일의 category 값이 모두 일치")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["text", "md"],
        default="text",
        help="출력 형식 (기본: text)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="어휘 위반이나 산출물 간 불일치가 있으면 종료 코드 1 (CI 용)",
    )
    args = parser.parse_args()
    report = build_report(args.format)
    print(report)

    if args.strict and "❌" in report:
        print("\n[STRICT] 검증 실패 항목이 있습니다 (위 ❌ 참고)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
