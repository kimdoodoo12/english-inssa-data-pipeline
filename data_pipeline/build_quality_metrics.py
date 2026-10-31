"""데이터 품질 지표 리포트(QUALITY_METRICS.md) 생성기.

data_pipeline/output/ 에 커밋된 파일만 읽는다. 새 라벨링/LLM 호출/외부 데이터 없음, 표준 라이브러리만 사용.
quality_report.py 가 '건수 분포/어휘 검증'을 다룬다면, 이 스크립트는
퍼널 정합성, slang_ratio 통계, 승인 vs 비승인 비교, 노이즈 의심, 필드 완결성을 다룬다.

    python data_pipeline/build_quality_metrics.py
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

OUTPUT_DIR = Path("data_pipeline/output")
REPORT_PATH = Path("QUALITY_METRICS.md")

WORD_SUMMARY_PATH = OUTPUT_DIR / "word_summary.jsonl"
FINAL_PATH = OUTPUT_DIR / "final_dataset.jsonl"
RANKED_PATH = OUTPUT_DIR / "ranked_candidates.jsonl"
APPROVED_PATH = OUTPUT_DIR / "service_public_approved.json"
PENDING_PATH = OUTPUT_DIR / "service_public_pending.json"

# 조기 종료 기준(K): enough_evidence 는 slang_hits 가 이 값에 도달하면 종료된다고 가정하고 검증한다.
EVIDENCE_K = 15
LOW_RATIO_THRESHOLD = 0.05
# rank_final_candidates.py 의 holdout 포함 기준(HOLDOUT_MIN_HITS, HOLDOUT_MIN_RATIO)과 동일한 값 (CLAUDE.md: hits>=3 AND ratio>=1/60).
HOLDOUT_MIN_HITS = 3
HOLDOUT_MIN_RATIO = 0.05 / 3
N_DISPUTABLE = 3  # 9절: approved 중 priority rank 상위 N개를 '슬랭 여부 검토 필요' 예시 후보로 표시
BAR_WIDTH = 30

KNOWN_SLANG = [
    "vibe", "fire", "cringe", "rizz", "slay", "sus", "cap", "bet",
    "low key", "woke", "based", "mid", "salty", "drip", "lmao", "ngl",
]
GENERIC_WORDS = ["never", "people", "go", "good", "way", "got", "one"]


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
# Stats helpers
# =========================
def key(word: str) -> str:
    """단어 키 비교용 정규화(소문자). approved/pending/final/ranked 비교에 일관되게 사용."""
    return word.lower()


def pct(n: float, total: float) -> str:
    return f"{(n / total * 100) if total else 0.0:.1f}%"


def quantiles(values: Sequence[float]) -> Dict[str, float]:
    """평균/중앙값/사분위(Q1,Q3)/최소/최대."""
    if not values:
        return {}
    s = sorted(values)
    if len(s) >= 2:
        q1, _, q3 = statistics.quantiles(s, n=4, method="inclusive")
    else:
        q1 = q3 = s[0]
    return {
        "n": len(s), "mean": statistics.mean(s), "median": statistics.median(s),
        "q1": q1, "q3": q3, "min": s[0], "max": s[-1],
    }


def bar(n: int, max_n: int) -> str:
    return "#" * (round(n / max_n * BAR_WIDTH) if max_n else 0)


def table(headers: List[str], rows: Iterable[Sequence[Any]]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out + [""]


def ascii_hist(values: Sequence[float], edges: List[float]) -> List[str]:
    """구간 [edges[i], edges[i+1]) 별 막대 (마지막 구간은 우측 포함). 코드블록 줄 목록 반환."""
    counts = [0] * (len(edges) - 1)
    for v in values:
        for i in range(len(counts)):
            last = i == len(counts) - 1
            if edges[i] <= v < edges[i + 1] or (last and v == edges[i + 1]):
                counts[i] += 1
                break
    mx = max(counts) if counts else 0
    lines = ["```"]
    for i, c in enumerate(counts):
        lab = f"{edges[i] * 100:>3.0f}-{edges[i + 1] * 100:<3.0f}%"
        lines.append(f"{lab} {c:>5,} {pct(c, len(values)):>6} {bar(c, mx)}")
    return lines + ["```", ""]


def stat_row(label: str, values: Sequence[float], scale: float = 100.0, unit: str = "%") -> List[str]:
    q = quantiles(values)
    if not q:
        return [label, "0", "-", "-", "-", "-"]
    f = lambda x: f"{x * scale:.1f}{unit}"  # noqa: E731
    return [label, f"{q['n']:,}", f(q["mean"]), f(q["median"]), f(q["q1"]), f(q["q3"])]


def counter_table(label: str, counters: Dict[str, Counter]) -> List[str]:
    """여러 Counter 를 열로 비교하는 표 (건수 + 비율)."""
    keys: List[Any] = []
    for c in counters.values():
        for k, _ in c.most_common():
            if k not in keys:
                keys.append(k)
    first = next(iter(counters.values()))
    keys.sort(key=lambda k: -first.get(k, 0))
    totals = {name: sum(c.values()) for name, c in counters.items()}
    headers = [label] + [f"{name} (n={totals[name]:,})" for name in counters]
    rows = [[k] + [f"{c.get(k, 0):,} ({pct(c.get(k, 0), totals[n])})" for n, c in counters.items()] for k in keys]
    return table(headers, rows)


# =========================
# Sections
# =========================
def sec_funnel(ws, ranked, final, approved, pending) -> List[str]:
    dec = Counter(r["decision"] for r in ws)
    n = len(ws)
    rk_dec = Counter(r["decision"] for r in ranked)
    holdout_kept = sum(1 for r in ranked if r.get("holdout_included"))
    out = ["## 1. 퍼널", ""]
    rows = [["판정 대상 (word_summary)", f"{n:,}", "100.0%"]]
    for d in ("drop", "enough_evidence", "holdout"):
        rows.append([f"  {d}", f"{dec.get(d, 0):,}", pct(dec.get(d, 0), n)])
    rows.append(["ranked_candidates", f"{len(ranked):,}", pct(len(ranked), n)])
    rows.append(["  enough_evidence 유래", f"{rk_dec.get('enough_evidence', 0):,}", pct(rk_dec.get('enough_evidence', 0), n)])
    rows.append(["  holdout 필터 통과", f"{holdout_kept:,}", f"holdout 중 {pct(holdout_kept, dec.get('holdout', 0))}"])
    rows.append(["final_dataset", f"{len(final):,}", pct(len(final), n)])
    tot = len(approved) + len(pending)
    rows.append(["  approved (검수 통과)", f"{len(approved):,}", f"final 중 {pct(len(approved), len(final))}"])
    rows.append(["  pending (미검수)", f"{len(pending):,}", f"final 중 {pct(len(pending), len(final))}"])
    out += table(["단계", "건수", "비율"], rows)

    # 단계 간 정합성
    checks = []
    exp_rank = dec.get("enough_evidence", 0) + holdout_kept
    checks.append(f"- ranked = enough_evidence + holdout 통과: {dec.get('enough_evidence', 0):,} + {holdout_kept:,} = {exp_rank:,} "
                  f"(실제 {len(ranked):,}) -> {'일치' if exp_rank == len(ranked) else '불일치'}")
    checks.append(f"- final = ranked: {len(final):,} vs {len(ranked):,} -> {'일치' if len(final) == len(ranked) else '불일치'}")
    checks.append(f"- approved + pending = final: {tot:,} vs {len(final):,} -> {'일치' if tot == len(final) else '불일치'}")
    fw = {key(r["word"]) for r in final}
    aw = {key(r["word"]) for r in approved}
    pw = {key(r["word"]) for r in pending}
    checks.append(f"- approved ∩ pending = {len(aw & pw):,}, approved - final = {len(aw - fw):,}, pending - final = {len(pw - fw):,}")
    rw = {key(r["normalized_word"]) for r in ranked}
    checks.append(f"- final 단어 집합 == ranked 단어 집합: {'예' if fw == rw else '아니오'}")
    out += checks + [""]
    out.append(f"검수 통과율(approved / final) = **{pct(len(approved), len(final))}**, "
               f"미검수 비율(pending / final) = **{pct(len(pending), len(final))}**.")
    out.append("")
    # holdout 필터 재현: rank_final_candidates.py 의 기준을 word_summary 에 직접 적용한다(ratio 는 코드와 같이 소수 6자리 반올림).
    hold = [r for r in ws if r["decision"] == "holdout"]

    def ratio6(r: Dict[str, Any]) -> float:
        return round(r["slang_hits"] / r["sampled"], 6) if r["sampled"] else 0.0

    def passes(r: Dict[str, Any]) -> bool:
        return r["slang_hits"] >= HOLDOUT_MIN_HITS and ratio6(r) >= HOLDOUT_MIN_RATIO

    repro = {key(r["normalized_word"]) for r in hold if passes(r)}
    ranked_hold = {key(r["normalized_word"]) for r in ranked if r.get("decision") == "holdout"}
    excl = [r for r in hold if not passes(r)]
    low_hits = sum(1 for r in excl if r["slang_hits"] < HOLDOUT_MIN_HITS)
    low_ratio = sum(1 for r in excl if ratio6(r) < HOLDOUT_MIN_RATIO)
    out.append(f"**holdout 필터 재현**: word_summary 의 holdout {len(hold):,}개에 "
               f"`slang_hits >= {HOLDOUT_MIN_HITS}` AND `slang_ratio >= {HOLDOUT_MIN_RATIO:.5f}`(=0.05/3=1/60)를 적용하면 "
               f"**{len(repro):,}개**가 통과하고 {len(excl):,}개가 탈락한다. "
               f"ranked 의 holdout 유래 {len(ranked_hold):,}개와 {'단어 집합까지 정확히 일치한다' if repro == ranked_hold else '단어 집합이 일치하지 않는다'} "
               f"(통과 건수: 재현 {len(repro):,} vs ranked 기록 {holdout_kept:,} -> {'일치' if len(repro) == holdout_kept else '불일치'}).")
    out.append(f"- 탈락 {len(excl):,}개 중 slang_hits < {HOLDOUT_MIN_HITS}: {low_hits:,}개, slang_ratio 기준 미달: {low_ratio:,}개 (중복 가능).")
    out.append("")
    return out


def sec_ratio(ws, ranked) -> List[str]:
    out = ["## 2. slang_ratio 분포 (decision 별)", ""]
    out.append(f"> 주의: enough_evidence 는 slang_hits 가 K(={EVIDENCE_K})에 도달하면 조기 종료되므로, "
               "표본 수가 작을 때 비율이 상향 편향될 수 있다(고비율 단어가 먼저 종료). 분포 해석 시 유의.")
    out.append("")
    out.append("> 순환성 주의: decision 은 slang_hits(와 그로부터 나오는 slang_ratio)로 결정된다. "
               "따라서 drop 의 비율이 0%이고 holdout 의 비율이 낮은 것은 구조적 결과이며 품질의 증거가 아니다. "
               "이 절은 분포를 기술할 뿐 판정의 정확성을 검증하지 않는다.")
    out.append("")
    skipped = [r for r in ws if not r["sampled"]]
    if skipped:
        out.append(f"> sampled == 0 인 레코드 {len(skipped):,}개는 비율을 계산할 수 없어 이 절의 분포에서 제외했다.")
        out.append("")
    by_dec: Dict[str, List[float]] = {}
    for r in ws:
        if r["sampled"]:
            by_dec.setdefault(r["decision"], []).append(r["slang_hits"] / r["sampled"])
    out.append("**word_summary 기준 (slang_ratio = slang_hits / sampled, 직접 계산)**")
    out.append("")
    rows = [stat_row(d, by_dec[d]) for d in ("drop", "enough_evidence", "holdout") if d in by_dec]
    out += table(["decision", "n", "평균", "중앙값", "Q1", "Q3"], rows)
    edges = [0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0]
    for d in ("drop", "enough_evidence", "holdout"):
        if d in by_dec:
            out.append(f"`{d}` 구간별 분포")
            out += ascii_hist(by_dec[d], edges)
    rk = [r["slang_ratio"] for r in ranked]
    out.append("**ranked_candidates 기준 (저장된 slang_ratio 필드)**")
    out.append("")
    out += table(["대상", "n", "평균", "중앙값", "Q1", "Q3"], [stat_row("ranked 전체", rk)])
    out += ascii_hist(rk, edges)
    return out


def sec_sampled(ws) -> List[str]:
    out = [f"## 3. sampled 분포 / {EVIDENCE_K}회 도달까지 문맥 수", ""]
    dec_groups: Dict[str, List[int]] = {}
    for r in ws:
        dec_groups.setdefault(r["decision"], []).append(r["sampled"])
    rows = []
    for d in ("drop", "enough_evidence", "holdout"):
        v = dec_groups.get(d, [])
        if v:
            rows.append([d, f"{len(v):,}", f"{statistics.mean(v):.1f}", f"{statistics.median(v):.0f}", min(v), max(v)])
    out += table(["decision", "n", "sampled 평균", "중앙값", "최소", "최대"], rows)
    c = Counter(r["sampled"] for r in ws)
    mx = max(c.values())
    out.append("sampled 값별 단어 수")
    out.append("```")
    for k in sorted(c):
        out.append(f"{k:>4} {c[k]:>6,} {pct(c[k], len(ws)):>6} {bar(c[k], mx)}")
    out += ["```", ""]
    ev = [r for r in ws if r["decision"] == "enough_evidence"]
    if ev:
        s = [r["sampled"] for r in ev]
        out.append(f"enough_evidence {len(ev):,}개가 판정에 사용한 문맥(sampled) 수: "
                   f"평균 **{statistics.mean(s):.1f}**, 중앙값 **{statistics.median(s):.0f}**, 최소 {min(s)}, 최대 {max(s)}.")
        out.append("- sampled 는 배치 단위(예: 20 단위)로 증가하는 것으로 보이며, 실제 K 도달 시점은 배치 경계로 올림된 값일 수 있다(추정).")
        out.append("")
    return out


def sec_categories(final, approved, pending) -> List[str]:
    out = ["## 4. 카테고리 분포", ""]
    aw = {key(r["word"]) for r in approved}
    pw = {key(r["word"]) for r in pending}
    sc = {
        "전체": Counter(r.get("slang_category") for r in final),
        "approved": Counter(r.get("slang_category") for r in final if key(r["word"]) in aw),
        "pending": Counter(r.get("slang_category") for r in final if key(r["word"]) in pw),
    }
    out += ["**slang_category (final_dataset 기준)**", ""] + counter_table("slang_category", sc)
    cat_all: Dict[str, Counter] = {
        "approved": Counter(c for r in approved for c in (r.get("category") or [])),
        "pending": Counter(c for r in pending for c in (r.get("category") or [])),
    }
    cat_all = {"전체(approved+pending)": cat_all["approved"] + cat_all["pending"], **cat_all}
    out += ["**서비스 category (한국어, 다중 라벨 -> 태그 단위 집계)**", ""] + counter_table("category", cat_all)
    out.append(f"- 고유 category 값 개수: {len(cat_all['전체(approved+pending)'])}")
    nc = {
        "final": Counter(len(r.get("category") or []) for r in final),
        "approved": Counter(len(r.get("category") or []) for r in approved),
        "pending": Counter(len(r.get("category") or []) for r in pending),
    }
    out += ["", "**단어당 category 개수**", ""] + counter_table("개수", nc)
    return out


def sec_approved_vs_rest(final, approved) -> List[str]:
    out = ["## 5. 승인 vs 비승인(pending) 비교", ""]
    aw = {key(r["word"]) for r in approved}
    a = [r["slang_ratio"] for r in final if key(r["word"]) in aw]
    p = [r["slang_ratio"] for r in final if key(r["word"]) not in aw]
    allv = [r["slang_ratio"] for r in final]
    out += table(["대상", "n", "평균", "중앙값", "Q1", "Q3"],
                 [stat_row("전체 final", allv), stat_row("approved", a), stat_row("비승인", p)])
    edges = [0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0]
    out.append("approved 의 slang_ratio 구간 분포")
    out += ascii_hist(a, edges)
    out.append("비승인의 slang_ratio 구간 분포")
    out += ascii_hist(p, edges)
    fin_a = [r for r in final if key(r["word"]) in aw]
    fin_p = [r for r in final if key(r["word"]) not in aw]
    out += ["**slang_category 비교** (4절 표 참고) 및 is_vulgar / difficulty_tier 비교", ""]
    out += counter_table("difficulty_tier", {
        "전체": Counter(r.get("difficulty_tier") for r in final),
        "approved": Counter(r.get("difficulty_tier") for r in fin_a),
        "비승인": Counter(r.get("difficulty_tier") for r in fin_p),
    })
    ranks_a = [r["rank"] for r in fin_a]
    ranks_p = [r["rank"] for r in fin_p]
    out.append(f"- is_vulgar 비율: 전체 {pct(sum(1 for r in final if r.get('is_vulgar')), len(final))}, "
               f"approved {pct(sum(1 for r in fin_a if r.get('is_vulgar')), len(fin_a))}, "
               f"비승인 {pct(sum(1 for r in fin_p if r.get('is_vulgar')), len(fin_p))}")
    if ranks_a and ranks_p:
        out.append(f"- rank 중앙값: approved {statistics.median(ranks_a):.0f}, 비승인 {statistics.median(ranks_p):.0f}")
    out.append("")
    return out


def sec_known(final, approved) -> List[str]:
    out = ["## 6. 알려진 슬랭 포함률", ""]
    out.append("> 이 지표는 recall 의 매우 약한 대용치일 뿐이다(소수의 사전 선정 단어, 정답 코퍼스 아님).")
    out.append("")
    fr = {key(r["word"]): r for r in final}
    aw = {key(r["word"]) for r in approved}
    rows = []
    for w in KNOWN_SLANG:
        r = fr.get(w)
        rows.append([w, "O" if r else "X", r["rank"] if r else "-",
                     f"{r['slang_ratio'] * 100:.1f}%" if r else "-", "O" if w in aw else "X"])
    out += table(["단어", "final 포함", "rank", "slang_ratio", "approved 포함"], rows)
    inf = sum(1 for w in KNOWN_SLANG if w in fr)
    ina = sum(1 for w in KNOWN_SLANG if w in aw)
    out.append(f"- final 포함: {inf}/{len(KNOWN_SLANG)} ({pct(inf, len(KNOWN_SLANG))}, 사전 선정 단어라 약한 지표), "
               f"approved 포함: {ina}/{len(KNOWN_SLANG)} ({pct(ina, len(KNOWN_SLANG))}, 사전 선정 단어라 약한 지표)")
    out.append("")
    return out


def sec_noise(final, approved) -> List[str]:
    out = ["## 7. 노이즈 의심 점검", ""]
    fr = {key(r["word"]): r for r in final}
    aw = {key(r["word"]) for r in approved}
    rows = []
    for w in GENERIC_WORDS:
        r = fr.get(w)
        rows.append([w, "O" if r else "X", r["rank"] if r else "-",
                     f"{r['slang_ratio'] * 100:.1f}%" if r else "-", "O" if w in aw else "X"])
    out += ["**일반어 포함 여부**", ""] + table(["단어", "final 포함", "rank", "slang_ratio", "approved 포함"], rows)
    present = sorted((fr[w] for w in GENERIC_WORDS if w in fr), key=lambda r: r["rank"])
    if present:
        out.append(f"- 해석: final {len(final):,}개 중 일반어 목록 {len(GENERIC_WORDS)}개 가운데 {len(present)}개가 포함되어 있으며, "
                   f"가장 높은 것은 {present[0]['word']}(rank {present[0]['rank']})이다. 즉 상위권에 일반어가 섞여 있다.")
        out.append("")
    low = sorted((r for r in final if r["slang_ratio"] < LOW_RATIO_THRESHOLD), key=lambda r: r["slang_ratio"])
    out.append(f"slang_ratio < {LOW_RATIO_THRESHOLD * 100:.0f}% 단어: **{len(low):,}개 ({pct(len(low), len(final))})**")
    out.append("")
    # 동률이 많아 상위 10개는 (ratio 오름차순, match_count 내림차순) 으로 고정
    low_top = sorted(low, key=lambda r: (r["slang_ratio"], -r["match_count"]))[:10]
    out.append("해당 단어 중 slang_ratio 낮은 순 상위 10개 (동률은 match_count 큰 순)")
    out.append("")
    out += table(["word", "rank", "slang_ratio", "slang_hits", "match_count", "approved"],
                 [[r["word"], r["rank"], f"{r['slang_ratio'] * 100:.2f}%", r["slang_hits"],
                   f"{r['match_count']:,}", "O" if key(r["word"]) in aw else "X"] for r in low_top])
    return out


def sec_completeness(final, approved, pending) -> List[str]:
    out = ["## 8. 필드 완결성", ""]
    fields = ["definition_en", "definition_ko", "example_en", "example_ko", "category"]

    def missing(r: Dict[str, Any], k: str) -> bool:
        v = r.get(k)
        return v is None or (isinstance(v, (str, list)) and len(v) == 0) or (isinstance(v, str) and not v.strip())

    groups = {"approved": approved, "pending": pending, "approved+pending": approved + pending}
    rows = []
    for f in fields:
        row = [f]
        for recs in groups.values():
            m = sum(1 for r in recs if missing(r, f))
            row.append(f"{m:,} ({pct(m, len(recs))})")
        rows.append(row)
    out += ["누락(null/빈 값) 건수 (비율)", ""]
    out += table(["필드"] + [f"{k} (n={len(v):,})" for k, v in groups.items()], rows)
    # final_dataset 은 example 필드가 없으므로 별도 표기
    fm = [[f, f"{sum(1 for r in final if f not in r or missing(r, f)):,}"] for f in ("definition_en", "definition_ko", "category")]
    out += ["final_dataset 누락 (example_* 필드는 final_dataset 에 존재하지 않음)", ""] + table(["필드", "누락 건수"], fm)
    lens = [len(r["example_en"]) for r in approved + pending if not missing(r, "example_en")]
    q = quantiles(lens)
    if q:
        out.append(f"example_en 길이(문자 수, n={q['n']:,}): 평균 {q['mean']:.1f}, 중앙값 {q['median']:.0f}, "
                   f"Q1 {q['q1']:.0f}, Q3 {q['q3']:.0f}, 최소 {q['min']:.0f}, 최대 {q['max']:.0f}")
        out.append("")
        edges = [0, 20, 40, 60, 80, 100, 150, 200, 10**9]
        counts = [sum(1 for x in lens if edges[i] <= x < edges[i + 1]) for i in range(len(edges) - 1)]
        mx = max(counts)
        out.append("```")
        for i, c in enumerate(counts):
            hi = f"{edges[i + 1] - 1}" if edges[i + 1] < 10**9 else "+"
            out.append(f"{edges[i]:>3}-{hi:<4} {c:>6,} {pct(c, len(lens)):>6} {bar(c, mx)}")
        out += ["```", ""]
    return out


def sec_limitations(final, approved, pending) -> List[str]:
    aw = {key(r["word"]) for r in approved}
    cand = sorted((r for r in final if key(r["word"]) in aw), key=lambda r: r["rank"])[:N_DISPUTABLE]
    out = ["## 9. Limitations", ""]
    out.append("이 리포트는 커밋된 산출물만으로 계산한 **내부 일관성/분포 지표**이며, 다음은 측정하지 못한다.")
    out.append("")
    out.append("- Precision / Recall / Cohen's κ 는 사람이 붙인 정답 라벨이 없으면 계산할 수 없다. 여기에는 사람 라벨이 없다.")
    out.append("- slang_ratio, slang_hits 는 LLM 판정 결과이며 정답 라벨이 아니다. LLM 판정 자체의 오류율은 알 수 없다.")
    out.append("- 'approved' 는 서비스 노출용 검수 통과이지 슬랭 정답 라벨이 아니다. "
               f"approved 중 priority rank 상위 {len(cand)}개를 자동 선정한 **슬랭 여부가 논쟁 가능한 예시 후보**(검토 필요): "
               + ", ".join(f"{r['word']}(rank {r['rank']}, slang_ratio {r['slang_ratio'] * 100:.1f}%)" for r in cand) + ".")
    out.append(f"- 6절의 '알려진 슬랭 포함률'은 {len(KNOWN_SLANG)}개 단어에 대한 약한 recall 대용치이며 모집단 recall 이 아니다.")
    out.append(f"- word_summary 의 K≥{EVIDENCE_K} 조기 종료는 slang_ratio 를 상향 편향시킬 수 있다(2절 참고). decision 간 비율 비교는 표본 수가 다르다.")
    out.append("- 단계 간 수치의 정합성과 holdout 필터(hits>=3 AND ratio>=1/60) 재현은 확인했다(1절). 다만 이는 내부 일관성 확인이며 품질 검증이 아니다.")
    out.append("- 문맥 출처(Reddit 등) 편향, 시점 편향, 중복/표기 변형 단어는 점검하지 않았다.")
    out.append(f"- pending {len(pending):,}개는 미검수이므로 example_ko 누락 등 완결성 지표는 승인분과 직접 비교할 수 없다(8절).")
    out.append("")
    return out


# =========================
# Main
# =========================
def build_report() -> str:
    ws = list(iter_jsonl(WORD_SUMMARY_PATH))
    final = list(iter_jsonl(FINAL_PATH))
    ranked = list(iter_jsonl(RANKED_PATH))
    approved = load_json_array(APPROVED_PATH)
    pending = load_json_array(PENDING_PATH)

    out: List[str] = ["# 데이터 품질 지표 리포트", "",
                      "`python data_pipeline/build_quality_metrics.py` 로 자동 생성됨. 모든 수치는 "
                      "`data_pipeline/output/` 의 커밋된 파일에서 계산했다.", ""]
    out += sec_funnel(ws, ranked, final, approved, pending)
    out += sec_ratio(ws, ranked)
    out += sec_sampled(ws)
    out += sec_categories(final, approved, pending)
    out += sec_approved_vs_rest(final, approved)
    out += sec_known(final, approved)
    out += sec_noise(final, approved)
    out += sec_completeness(final, approved, pending)
    out += sec_limitations(final, approved, pending)
    return "\n".join(out)


def main() -> None:
    with REPORT_PATH.open("w", encoding="utf-8", newline="\n") as f:
        f.write(build_report() + "\n")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
