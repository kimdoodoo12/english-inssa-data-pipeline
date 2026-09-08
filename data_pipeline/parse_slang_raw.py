import os
import re
import json
import bz2
import gzip
import lzma
import time
from collections import defaultdict
from typing import Dict, List, Set, Iterable, Tuple, Optional

# zst 지원 (설치되어 있으면 사용)
try:
    import zstandard as zstd
except ImportError:
    zstd = None


# =========================
# 설정
# =========================
CANDIDATE_JSON_PATH = "data_pipeline/output/slang_raw.json"

# Reddit 댓글 덤프 파일들 경로.
# 환경변수 REDDIT_DUMP_PATHS 로 지정한다 (os.pathsep 구분, Windows는 ';' / POSIX는 ':').
#   set REDDIT_DUMP_PATHS=D:\reddit\RC_2025-09.zst;D:\reddit\RC_2025-12.zst
DEFAULT_REDDIT_DUMP_PATHS = [
    "data_pipeline/dump/RC_2025-09.zst",
    "data_pipeline/dump/RC_2025-12.zst",
]

def resolve_reddit_dump_paths() -> List[str]:
    raw = os.getenv("REDDIT_DUMP_PATHS", "").strip()
    if not raw:
        return list(DEFAULT_REDDIT_DUMP_PATHS)
    return [p for p in (part.strip() for part in raw.split(os.pathsep)) if p]

OUTPUT_MATCHED_PATH = "matched_candidates.json"
OUTPUT_UNMATCHED_PATH = "unmatched_candidates.json"
OUTPUT_STATS_PATH = "candidate_usage_stats.json"

# 스캔 규모 지표 (처리 건수·소요 시간). 파이프라인 리포트용으로 남긴다.
OUTPUT_SCAN_STATS_PATH = "data_pipeline/output/scan_stats.json"

# body 필드명 후보
BODY_KEYS = ["body", "comment", "text"]
SUBREDDIT_KEYS = ["subreddit", "subreddit_name_prefixed"]

# 너무 큰 파일에서 중간 진행상황 출력 주기
LOG_EVERY = 100_000


# =========================
# 텍스트 정규화
# =========================
TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")

def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'").replace("`", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def tokenize(text: str) -> List[str]:
    text = normalize_text(text)
    return TOKEN_RE.findall(text)

def normalize_candidate_word(word: str) -> str:
    return " ".join(tokenize(word))


# =========================
# 파일 열기
# =========================
def open_text_file(path: str):
    lower = path.lower()

    if lower.endswith(".bz2"):
        return bz2.open(path, "rt", encoding="utf-8", errors="ignore")
    if lower.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    if lower.endswith(".xz") or lower.endswith(".lzma"):
        return lzma.open(path, "rt", encoding="utf-8", errors="ignore")
    if lower.endswith(".zst"):
        if zstd is None:
            raise RuntimeError(
                "'.zst' 파일을 읽으려면 zstandard 패키지가 필요함.\n"
                "설치: pip install zstandard"
            )
        fh = open(path, "rb")
        dctx = zstd.ZstdDecompressor()
        stream = dctx.stream_reader(fh)
        import io
        return io.TextIOWrapper(stream, encoding="utf-8", errors="ignore")

    return open(path, "rt", encoding="utf-8", errors="ignore")


# =========================
# Reddit JSONL 한 줄 파싱
# =========================
def extract_body_and_subreddit(obj: dict) -> Tuple[Optional[str], Optional[str]]:
    body = None
    subreddit = None

    for key in BODY_KEYS:
        if key in obj and isinstance(obj[key], str):
            body = obj[key]
            break

    for key in SUBREDDIT_KEYS:
        if key in obj and isinstance(obj[key], str):
            subreddit = obj[key]
            break

    return body, subreddit


def iter_reddit_comments(paths: List[str]) -> Iterable[Tuple[str, Optional[str], str]]:
    """
    yield: (body, subreddit, source_file)
    """
    for path in paths:
        print(f"[INFO] Reading dump: {path}")
        with open_text_file(path) as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                body, subreddit = extract_body_and_subreddit(obj)
                if not body or body in ("[deleted]", "[removed]"):
                    continue

                yield body, subreddit, path


# =========================
# 후보 로딩 및 인덱스 구성
# =========================
def load_candidates(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("후보 JSON은 list 형태여야 함.")

    return data


def build_ngram_index(candidates: List[dict]):
    """
    candidate phrase를 토큰 길이별 set으로 저장.
    ex)
      index[1] = {"cat", "mid", ...}
      index[2] = {"no cap", "low key", ...}
    """
    ngram_index: Dict[int, Set[str]] = defaultdict(set)
    normalized_to_items: Dict[str, List[dict]] = defaultdict(list)

    skipped = 0

    for item in candidates:
        raw_word = item.get("word", "")
        if not isinstance(raw_word, str):
            skipped += 1
            continue

        norm = normalize_candidate_word(raw_word)
        if not norm:
            skipped += 1
            continue

        length = len(norm.split())
        ngram_index[length].add(norm)
        normalized_to_items[norm].append(item)

    print(f"[INFO] Valid normalized candidates: {sum(len(v) for v in ngram_index.values())}")
    print(f"[INFO] Skipped invalid candidates: {skipped}")
    print(f"[INFO] Phrase lengths used: {sorted(ngram_index.keys())}")

    return ngram_index, normalized_to_items


# =========================
# 댓글 1개에서 후보 찾기
# =========================
def find_candidates_in_comment(
    body: str,
    ngram_index: Dict[int, Set[str]]
) -> Set[str]:
    """
    댓글 body를 토큰화한 뒤,
    후보 길이에 맞춰 n-gram을 생성해서 후보 set과 교집합 검사.
    """
    tokens = tokenize(body)
    if not tokens:
        return set()

    found = set()
    candidate_lengths = sorted(ngram_index.keys())

    for n in candidate_lengths:
        if len(tokens) < n:
            continue

        target_set = ngram_index[n]

        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i:i+n])
            if gram in target_set:
                found.add(gram)

    return found


# =========================
# 메인
# =========================
def main():
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

    started_at = time.time()
    candidates = load_candidates(CANDIDATE_JSON_PATH)
    ngram_index, normalized_to_items = build_ngram_index(candidates)

    usage_stats = {}
    for norm_word in normalized_to_items.keys():
        usage_stats[norm_word] = {
            "matched": False,
            "match_count": 0,          # 댓글에서 몇 번 발견됐는지
            "source_files": set(),     # 어느 분기 파일에서 나왔는지
            "subreddits": set(),       # 어떤 subreddit에서 나왔는지
        }

    total_comments = 0

    for body, subreddit, source_file in iter_reddit_comments(dump_paths):
        total_comments += 1

        found = find_candidates_in_comment(body, ngram_index)
        if found:
            for norm_word in found:
                usage_stats[norm_word]["matched"] = True
                usage_stats[norm_word]["match_count"] += 1
                usage_stats[norm_word]["source_files"].add(os.path.basename(source_file))
                if subreddit:
                    usage_stats[norm_word]["subreddits"].add(subreddit)

        if total_comments % LOG_EVERY == 0:
            matched_so_far = sum(1 for v in usage_stats.values() if v["matched"])
            print(
                f"[INFO] processed_comments={total_comments:,} "
                f"matched_candidates={matched_so_far:,}"
            )

    matched_candidates = []
    unmatched_candidates = []
    candidate_usage_stats = []

    for norm_word, items in normalized_to_items.items():
        stat = usage_stats[norm_word]

        serializable_stat = {
            "normalized_word": norm_word,
            "matched": stat["matched"],
            "match_count": stat["match_count"],
            "source_files": sorted(stat["source_files"]),
            "subreddit_count": len(stat["subreddits"]),
            "subreddits_sample": sorted(list(stat["subreddits"]))[:20],
            "candidate_count": len(items),  # 같은 단어에 여러 sense가 있으면 개수 표시
        }
        candidate_usage_stats.append(serializable_stat)

        if stat["matched"]:
            for item in items:
                out = dict(item)
                out["normalized_word"] = norm_word
                out["matched"] = True
                out["match_count"] = stat["match_count"]
                out["source_files"] = sorted(stat["source_files"])
                out["subreddit_count"] = len(stat["subreddits"])
                matched_candidates.append(out)
        else:
            for item in items:
                out = dict(item)
                out["normalized_word"] = norm_word
                out["matched"] = False
                unmatched_candidates.append(out)

    # 저장
    with open(OUTPUT_MATCHED_PATH, "w", encoding="utf-8") as f:
        json.dump(matched_candidates, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_UNMATCHED_PATH, "w", encoding="utf-8") as f:
        json.dump(unmatched_candidates, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(candidate_usage_stats, f, ensure_ascii=False, indent=2)

    elapsed_sec = time.time() - started_at
    matched_words = sum(1 for v in usage_stats.values() if v["matched"])

    scan_stats = {
        "stage": "2_parse_slang_raw",
        "total_comments_processed": total_comments,
        "elapsed_sec": round(elapsed_sec, 1),
        "comments_per_sec": round(total_comments / elapsed_sec, 1) if elapsed_sec > 0 else None,
        "candidate_words": len(normalized_to_items),
        "matched_words": matched_words,
        "dumps": [
            {
                "file": os.path.basename(p),
                "compressed_bytes": os.path.getsize(p) if os.path.exists(p) else None,
            }
            for p in dump_paths
        ],
    }
    os.makedirs(os.path.dirname(OUTPUT_SCAN_STATS_PATH), exist_ok=True)
    with open(OUTPUT_SCAN_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(scan_stats, f, ensure_ascii=False, indent=2)

    print("\n[DONE]")
    print(f"Total comments processed: {total_comments:,}")
    print(f"Elapsed: {elapsed_sec/3600:.2f}h  ({scan_stats['comments_per_sec']:,} comments/sec)")
    print(f"Matched candidate words: {matched_words:,}")
    print(f"Saved: {OUTPUT_MATCHED_PATH}")
    print(f"Saved: {OUTPUT_UNMATCHED_PATH}")
    print(f"Saved: {OUTPUT_STATS_PATH}")
    print(f"Saved: {OUTPUT_SCAN_STATS_PATH}")


if __name__ == "__main__":
    main()