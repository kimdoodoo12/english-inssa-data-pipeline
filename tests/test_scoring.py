"""점수 공식과 카테고리 검증 로직에 대한 단위 테스트.

    python -m pytest tests/ -q

파이프라인 본체는 수백 GB 외부 데이터를 요구하므로 통합 테스트가 어렵다.
대신 결과 랭킹을 좌우하는 순수 함수 — 점수 공식, 임계값 분류, tier 경계,
LLM 응답 검증 — 만 떼어내 검증한다.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_pipeline"))

from add_korean_definitions import CATEGORIES_KO, validate_categories  # noqa: E402
from rank_final_candidates import (  # noqa: E402
    CATEGORY_CONF,
    EXPECTED_SLANG_CAP,
    compute_priority,
    is_holdout_included,
)
from rank_slang_candidates import (  # noqa: E402
    KEEP_THRESHOLD,
    PRUNE_THRESHOLD,
    classify_support,
    compute_support_score,
)
from reddit_context_cache_builder import get_target_n  # noqa: E402
from reddit_slang_llm_judger import get_n_max  # noqa: E402
from sync_service_categories import normalize_categories  # noqa: E402


# =========================
# Stage 4 — support_score
# =========================
class TestSupportScore:
    def test_matches_documented_formula(self):
        # support_score = 0.6*log10(1+match) + 0.4*log10(1+subreddit)
        got = compute_support_score(999, 99)
        expected = 0.6 * math.log10(1000) + 0.4 * math.log10(100)
        assert got == pytest.approx(expected)
        assert got == pytest.approx(0.6 * 3 + 0.4 * 2)

    def test_zero_counts_give_zero(self):
        assert compute_support_score(0, 0) == 0.0

    def test_negative_counts_are_clamped(self):
        """음수 입력이 log10 도메인 에러를 내지 않아야 한다."""
        assert compute_support_score(-5, -5) == 0.0

    def test_monotonic_in_match_count(self):
        scores = [compute_support_score(n, 10) for n in (10, 100, 1_000, 10_000)]
        assert scores == sorted(scores)

    def test_match_count_weighs_more_than_subreddit_count(self):
        """같은 크기 변화라면 match_count 쪽이 점수를 더 올린다 (0.6 vs 0.4)."""
        assert compute_support_score(10_000, 10) > compute_support_score(10, 10_000)


class TestClassifySupport:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (KEEP_THRESHOLD, "keep"),          # 경계 포함
            (KEEP_THRESHOLD + 0.01, "keep"),
            (KEEP_THRESHOLD - 0.01, "gray_zone"),
            (PRUNE_THRESHOLD, "gray_zone"),    # 경계 포함
            (PRUNE_THRESHOLD - 0.01, "prune"),
            (0.0, "prune"),
        ],
    )
    def test_threshold_boundaries(self, score, expected):
        assert classify_support(score) == expected


# =========================
# Stage 7 — priority_score
# =========================
class TestComputePriority:
    def test_perfect_slang_has_no_penalty(self):
        """cat_conf=1.0 이면 penalty=0 이라 log10(1+expected) 그대로다."""
        priority, ratio, raw = compute_priority(
            match_count=1000, slang_hits=60, sampled=60, slang_category="internet_slang"
        )
        assert ratio == 1.0
        assert raw == 1000
        assert priority == pytest.approx(math.log10(1001), abs=1e-6)

    def test_expected_is_capped(self):
        """expected_capped 는 3,000,000 을 넘지 않는다 (raw 는 캡 전 값)."""
        priority, _, raw = compute_priority(
            match_count=10_000_000, slang_hits=60, sampled=60, slang_category="internet_slang"
        )
        assert raw == 10_000_000
        assert priority == pytest.approx(math.log10(1 + EXPECTED_SLANG_CAP), abs=1e-6)

    def test_unknown_category_uses_default_confidence(self):
        """None 과 미등록 문자열은 같은 기본 신뢰도(0.5)를 쓴다."""
        a, _, _ = compute_priority(1000, 30, 60, None)
        b, _, _ = compute_priority(1000, 30, 60, "not_a_real_category")
        assert a == b

    def test_lower_confidence_category_scores_lower(self):
        """같은 근거라면 colloquial 이 internet_slang 보다 낮아야 한다."""
        assert CATEGORY_CONF["internet_slang"] > CATEGORY_CONF["colloquial"]
        high, _, _ = compute_priority(1000, 30, 60, "internet_slang")
        low, _, _ = compute_priority(1000, 30, 60, "colloquial")
        assert high > low

    def test_zero_sampled_does_not_divide_by_zero(self):
        priority, ratio, raw = compute_priority(1000, 0, 0, "general_slang")
        assert ratio == 0.0
        assert raw == 0
        assert priority == 0.0


class TestHoldoutFilter:
    @pytest.mark.parametrize(
        "hits,ratio,expected",
        [
            (3, 0.0167, True),    # 두 조건 모두 충족
            (2, 0.9, False),      # hits 부족
            (10, 0.01, False),    # ratio 부족
            (3, 0.0166, False),   # ratio 경계 바로 아래
        ],
    )
    def test_requires_both_conditions(self, hits, ratio, expected):
        assert is_holdout_included(hits, ratio) is expected


# =========================
# Stage 5/6 — tier 경계
# =========================
class TestTierBoundaries:
    @pytest.mark.parametrize(
        "match_count,expected",
        [
            (0, 100),
            (9_999, 100),
            (10_000, 120),
            (49_999, 120),
            (50_000, 140),
            (199_999, 140),
            (200_000, 160),
            (999_999, 160),
            (1_000_000, 200),
            (10_000_000, 200),
        ],
    )
    def test_target_n_tiers(self, match_count, expected):
        assert get_target_n(match_count) == expected

    def test_stage5_and_stage6_tiers_agree(self):
        """Stage 5 수집량과 Stage 6 소진 상한이 어긋나면 판정이 조기 종료된다."""
        for mc in (0, 9_999, 10_000, 50_000, 200_000, 1_000_000, 5_000_000):
            assert get_target_n(mc) == get_n_max(mc)


# =========================
# LLM 응답 검증
# =========================
class TestValidateCategories:
    def test_allowed_categories_pass_through(self):
        assert validate_categories("test", ["일상 대화", "감탄·놀람"]) == ["일상 대화", "감탄·놀람"]

    def test_out_of_vocab_is_dropped(self):
        assert validate_categories("test", ["일상 대화", "존재하지않는카테고리"]) == ["일상 대화"]

    def test_known_alias_is_mapped(self):
        """실제 데이터에서 관측된 규정 외 값은 허용 어휘로 흡수한다."""
        assert validate_categories("rubber", ["성·성적 표현"]) == ["주의/거친 표현"]
        assert validate_categories("wicket", ["스포츠·게임"]) == ["게임·커뮤니티"]

    def test_duplicates_removed_after_mapping(self):
        """별칭 매핑 결과가 기존 항목과 겹치면 중복을 남기지 않는다."""
        assert validate_categories("x", ["주의/거친 표현", "성·성적 표현"]) == ["주의/거친 표현"]

    def test_capped_at_three(self):
        assert len(validate_categories("x", CATEGORIES_KO)) == 3

    @pytest.mark.parametrize("bad", [None, "일상 대화", 42, {"a": 1}])
    def test_non_list_input_returns_empty(self, bad):
        assert validate_categories("x", bad) == []

    def test_non_string_elements_are_skipped(self):
        assert validate_categories("x", ["일상 대화", None, 7]) == ["일상 대화"]


class TestNormalizeCategories:
    def test_matches_validate_categories_for_known_aliases(self):
        """재동기화 스크립트와 생성 스크립트의 정규화 결과가 일치해야 한다."""
        for cats in (["성·성적 표현"], ["감각·신체"], ["감사·인사"], ["스포츠·게임"], ["격려·응원"]):
            assert normalize_categories(cats) == validate_categories("x", cats)

    def test_order_preserved(self):
        assert normalize_categories(["감탄·놀람", "일상 대화"]) == ["감탄·놀람", "일상 대화"]
