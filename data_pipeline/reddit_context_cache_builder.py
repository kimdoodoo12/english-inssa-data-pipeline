from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import random
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import zstandard as zstd
except ImportError:
    zstd = None


# =========================
# Configuration
# =========================
SCORED_CANDIDATES_PATH = Path("data_pipeline/data/scored_candidates.json")

# Reddit 댓글 덤프 경로. 환경변수 REDDIT_DUMP_PATHS 로 지정한다 (os.pathsep 구분).
# parse_slang_raw.py 와 동일한 규약.
DEFAULT_REDDIT_DUMP_PATHS = [
    "data_pipeline/dump/RC_2025-09.zst",
    "data_pipeline/dump/RC_2025-12.zst",
]


def resolve_reddit_dump_paths() -> List[str]:
    raw = os.getenv("REDDIT_DUMP_PATHS", "").strip()
    if not raw:
        return list(DEFAULT_REDDIT_DUMP_PATHS)
    return [p for p in (part.strip() for part in raw.split(os.pathsep)) if p]


OUTPUT_PATH = Path("data_pipeline/candidate_context_cache.jsonl")
SUMMARY_PATH = Path("data_pipeline/candidate_context_summary.jsonl")

RANDOM_SEED = 42
MAX_COMMENT_CHARS = 10_000
LOG_EVERY = 100_000

BODY_KEYS = ["body", "comment", "text"]
SUBREDDIT_KEYS = ["subreddit", "subreddit_name_prefixed"]
TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


# =========================
# Tier-based sample size
# 통계 근거: p=2~15% 가정 하에 모든 tier에서 기대 슬랭 문맥 6~10개 수렴
# =========================
def get_target_n(match_count: int) -> int:
    if match_count < 10_000:    return 100
    if match_count < 50_000:    return 120
    if match_count < 200_000:   return 140
    if match_count < 1_000_000: return 160
    return 200


# =========================
# Text normalization — parse_slang_raw.py와 동일
# =========================
def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("\u2019", "'").replace("`", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(normalize_text(text))


def normalize_phrase(word: str) -> str:
    return " ".join(tokenize(word))


# 1-gram 매칭 시 하이픈 복합어 오매칭 방지용 패턴 캐시
_BOUNDARY_PATTERN_CACHE: Dict[str, re.Pattern] = {}

def _get_boundary_pattern(phrase: str) -> re.Pattern:
    if phrase not in _BOUNDARY_PATTERN_CACHE:
        escaped = re.escape(phrase)
        # "fire-fighter"에서 "fire"가 매칭되는 것을 막음
        # 단어 앞뒤로 알파벳/숫자/하이픈이 붙어있으면 복합어로 간주
        _BOUNDARY_PATTERN_CACHE[phrase] = re.compile(
            r'(?<![a-zA-Z0-9])' + escaped + r'(?!-[a-zA-Z0-9]|[a-zA-Z0-9])',
            re.IGNORECASE
        )
    return _BOUNDARY_PATTERN_CACHE[phrase]


def is_standalone_in_original(phrase: str, original_body: str) -> bool:
    """1-gram 전용: 원본 텍스트에서 단어가 복합어 일부가 아닌지 확인."""
    return bool(_get_boundary_pattern(phrase).search(original_body))


# =========================
# Reservoir sampler
# =========================
class ReservoirSampler:
    def __init__(self, size: int, rng: random.Random):
        self.size = size
        self.rng = rng
        self.items: List[Dict[str, Any]] = []
        self.seen = 0
        self._ids: Set[str] = set()

    def offer(self, item: Dict[str, Any]) -> None:
        if self.size <= 0:
            return
        cid = item["context_id"]
        if cid in self._ids:
            return
        self.seen += 1
        if len(self.items) < self.size:
            self.items.append(item)
            self._ids.add(cid)
            return
        j = self.rng.randint(1, self.seen)
        if j <= self.size:
            old = self.items[j - 1]
            self._ids.discard(old["context_id"])
            self.items[j - 1] = item
            self._ids.add(cid)


# =========================
# Candidate loading
# =========================
def load_keep_candidates(path: Path) -> Tuple[
    Dict[int, Set[str]],
    Dict[str, str],
    Dict[str, int],
    Dict[str, Dict],
]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    ngram_index: Dict[int, Set[str]] = defaultdict(set)
    phrase_to_norm: Dict[str, str] = {}
    target_ns: Dict[str, int] = {}
    word_meta: Dict[str, Dict] = {}

    for item in data:
        if item.get("support_label") != "keep":
            continue

        nw = str(item.get("normalized_word", "")).strip()
        if not nw:
            continue

        match_count = int(item.get("match_count", 0) or 0)
        target_ns[nw] = get_target_n(match_count)
        word_meta[nw] = {
            "match_count": match_count,
            "subreddit_count": int(item.get("subreddit_count", 0) or 0),
            "sense_count": int(item.get("sense_count", 1) or 1),
        }

        # surface_forms 전체 인덱싱 (token 경계 매칭이므로 firefox 등 오매칭 없음)
        phrases: Set[str] = set()
        for sf in (item.get("surface_forms") or []):
            p = normalize_phrase(str(sf))
            if p:
                phrases.add(p)
        p = normalize_phrase(nw)
        if p:
            phrases.add(p)

        for phrase in phrases:
            length = len(phrase.split())
            ngram_index[length].add(phrase)
            phrase_to_norm[phrase] = nw

    print(f"[INFO] KEEP candidates: {len(target_ns)}")
    print(f"[INFO] Indexed phrases: {len(phrase_to_norm)}")
    print(f"[INFO] Phrase lengths: {sorted(ngram_index.keys())}")
    return ngram_index, phrase_to_norm, target_ns, word_meta


# =========================
# Single-file scan (subprocess worker)
# =========================
def scan_file_worker(
    dump_path: str,
    ngram_index: Dict[int, Set[str]],
    phrase_to_norm: Dict[str, str],
    target_ns: Dict[str, int],
    rng_seed: int,
    tmp_output: str,
) -> None:
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

    if zstd is None:
        raise RuntimeError("pip install zstandard")

    rng = random.Random(rng_seed)
    samplers: Dict[str, ReservoirSampler] = {
        nw: ReservoirSampler(n, rng) for nw, n in target_ns.items()
    }
    # 조기 종료용: 아직 target에 도달하지 못한 단어 수 추적
    remaining = len(samplers)
    candidate_lengths = sorted(ngram_index.keys())
    basename = os.path.basename(dump_path)
    total = 0

    print(f"[Worker:{basename}] Starting scan")

    with open(dump_path, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        stream = io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            body: Optional[str] = None
            for k in BODY_KEYS:
                if k in obj and isinstance(obj[k], str):
                    body = obj[k]
                    break
            if not body or body in ("[deleted]", "[removed]"):
                continue

            subreddit: Optional[str] = None
            for k in SUBREDDIT_KEYS:
                if k in obj and isinstance(obj[k], str):
                    subreddit = obj[k]
                    break

            record_id = str(obj.get("id") or obj.get("name") or f"line:{line_no}")
            created_utc = obj.get("created_utc")

            tokens = tokenize(body)
            if not tokens:
                continue

            found: Set[str] = set()
            for n in candidate_lengths:
                if len(tokens) < n:
                    continue
                ts = ngram_index[n]
                for i in range(len(tokens) - n + 1):
                    gram = " ".join(tokens[i:i + n])
                    if gram in ts:
                        found.add(gram)

            total += 1
            for phrase in found:
                # 1-gram은 원본에서 복합어 여부 추가 검증 (fire-fighter -> fire 오매칭 방지)
                if " " not in phrase and not is_standalone_in_original(phrase, body):
                    continue
                nw = phrase_to_norm[phrase]
                sampler = samplers[nw]
                was_full = len(sampler.items) >= sampler.size
                cid = hashlib.sha256(
                    "\x1f".join([nw, dump_path, str(line_no), record_id]).encode()
                ).hexdigest()
                sampler.offer({
                    "context_id": cid,
                    "matched_phrase": phrase,
                    "context_text": body[:MAX_COMMENT_CHARS],
                    "subreddit": subreddit,
                    "created_utc": created_utc,
                    "source_file": basename,
                    "line_no": line_no,
                })
                if not was_full and len(sampler.items) >= sampler.size:
                    remaining -= 1

            if remaining <= 0:
                print(f"[Worker:{basename}] All targets filled at {total:,} comments -- stopping early")
                break

            if total % LOG_EVERY == 0:
                active = sum(1 for s in samplers.values() if s.seen > 0)
                print(f"[Worker:{basename}] comments={total:,} active_words={active:,} remaining={remaining:,}")

    print(f"[Worker:{basename}] Scan done - writing partial results")
    with open(tmp_output, "w", encoding="utf-8") as out:
        for nw, sampler in samplers.items():
            if sampler.seen > 0:
                out.write(json.dumps({
                    "normalized_word": nw,
                    "seen": sampler.seen,
                    "items": sampler.items,
                }, ensure_ascii=False) + "\n")
    print(f"[Worker:{basename}] Written: {tmp_output}")


# =========================
# Merge partial results from both workers
# =========================
def merge_results(
    tmp_files: List[str],
    target_ns: Dict[str, int],
    word_meta: Dict[str, Dict],
) -> None:
    word_items: Dict[str, List[Dict]] = defaultdict(list)
    word_seen: Dict[str, int] = defaultdict(int)
    seen_ids: Dict[str, Set[str]] = defaultdict(set)

    for tmp in tmp_files:
        if not os.path.exists(tmp):
            print(f"[Merge] WARNING: missing tmp file {tmp}")
            continue
        with open(tmp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                nw = obj["normalized_word"]
                word_seen[nw] += obj["seen"]
                for item in obj["items"]:
                    cid = item["context_id"]
                    if cid not in seen_ids[nw]:
                        seen_ids[nw].add(cid)
                        word_items[nw].append(item)

    rng = random.Random(RANDOM_SEED)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)

    total_collected = 0
    words_with_contexts = 0

    with OUTPUT_PATH.open("w", encoding="utf-8") as out, \
         SUMMARY_PATH.open("w", encoding="utf-8") as sout:

        for nw in sorted(target_ns.keys()):
            target_n = target_ns[nw]
            items = word_items.get(nw, [])

            if len(items) > target_n:
                final_sampler = ReservoirSampler(target_n, rng)
                for item in items:
                    final_sampler.offer(item)
                final_items = final_sampler.items
            else:
                final_items = items

            collected = len(final_items)
            total_seen = word_seen.get(nw, 0)
            meta = word_meta.get(nw, {})

            out.write(json.dumps({
                "normalized_word": nw,
                "target_n": target_n,
                "collected": collected,
                "total_seen": total_seen,
                "contexts": final_items,
            }, ensure_ascii=False) + "\n")

            sout.write(json.dumps({
                "normalized_word": nw,
                "target_n": target_n,
                "collected": collected,
                "total_seen": total_seen,
                "match_count": meta.get("match_count", 0),
                "subreddit_count": meta.get("subreddit_count", 0),
                "sense_count": meta.get("sense_count", 1),
            }, ensure_ascii=False) + "\n")

            if collected > 0:
                words_with_contexts += 1
                total_collected += collected

    print(f"[Merge] words_with_contexts={words_with_contexts:,}")
    print(f"[Merge] total_collected={total_collected:,}")
    print(f"[Merge] output -> {OUTPUT_PATH}")
    print(f"[Merge] summary -> {SUMMARY_PATH}")


# =========================
# Main
# =========================
def main() -> None:
    dump_paths = resolve_reddit_dump_paths()
    missing = [p for p in dump_paths if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            "Reddit 덤프 파일을 찾을 수 없습니다: "
            + ", ".join(missing)
            + "\n환경변수 REDDIT_DUMP_PATHS 로 경로를 지정하세요 "
            f"(구분자 '{os.pathsep}')."
        )
    print(f"[INFO] Reddit dumps: {dump_paths}")

    ngram_index, phrase_to_norm, target_ns, word_meta = load_keep_candidates(SCORED_CANDIDATES_PATH)

    tmp_files: List[str] = []
    processes: List[multiprocessing.Process] = []

    for i, dump_path in enumerate(dump_paths):
        tmp = tempfile.mktemp(suffix=f"_ctx_worker{i}.jsonl")
        tmp_files.append(tmp)
        p = multiprocessing.Process(
            target=scan_file_worker,
            args=(
                str(dump_path),
                dict(ngram_index),
                phrase_to_norm,
                target_ns,
                RANDOM_SEED + i,
                tmp,
            ),
        )
        processes.append(p)

    for p in processes:
        p.start()
    for p in processes:
        p.join()

    merge_results(tmp_files, target_ns, word_meta)

    for tmp in tmp_files:
        try:
            os.remove(tmp)
        except OSError:
            pass

    print("[DONE]")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
