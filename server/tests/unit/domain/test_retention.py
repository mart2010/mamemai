import pytest

from mamemai_server.domain.model import retention


def test_freshly_practiced_item_is_fully_retained():
    """Spec: FR-506, TR-802"""
    assert retention(0, 5.0) == pytest.approx(1.0)


def test_one_half_life_halves_retention():
    """Spec: FR-506, TR-802 — the curve's defining property."""
    assert retention(5, 5.0) == pytest.approx(0.5)
    assert retention(10, 5.0) == pytest.approx(0.25)


def test_longer_half_life_decays_more_slowly():
    """Spec: FR-506, TR-802 — what growth-on-success buys: the same elapsed time costs
    a well-known item less retention than a shaky one."""
    assert retention(7, 14.0) > retention(7, 2.0)


def test_retention_is_monotonically_decreasing_in_elapsed_days():
    """Spec: FR-506, TR-802 — the ranking property selection actually relies on."""
    values = [retention(days, 3.0) for days in range(0, 30)]
    assert values == sorted(values, reverse=True)


def test_retention_stays_positive_across_realistic_staleness():
    """Spec: TR-802 — over any plausible gap the curve stays strictly positive, so stale
    items still order among themselves rather than collapsing into one tie."""
    assert 0.0 < retention(365, 1.0) < 1e-100
    assert retention(365, 1.0) < retention(300, 1.0)


def test_extreme_staleness_underflows_to_zero_harmlessly():
    """Spec: TR-802 — documents a real float64 limit rather than claiming it away: past
    ~1074 half-lives the result underflows to exactly 0.0 and such items tie. Harmless
    for ranking, because a persona's "no usable state" sentinel is negative (the tutor
    uses -1.0), so a never-practiced item still sorts ahead of a long-abandoned one."""
    assert retention(2000, 1.0) == 0.0


def test_negative_elapsed_days_reads_as_fully_retained():
    """Spec: TR-802 — clock skew (practiced "in the future") must not produce a
    retention above 1.0, which would sort a skewed item as better-known than a
    just-practiced one."""
    assert retention(-5, 2.0) == pytest.approx(1.0)


@pytest.mark.parametrize("half_life_days", [0.0, -1.0])
def test_non_positive_half_life_is_rejected(half_life_days):
    """Spec: TR-802, INV-6 — there is no curve for a non-positive half-life, and what an
    unusable SRS state should rank as is persona policy. The domain formula refuses to
    invent an answer rather than baking one persona's sentinel into generic code."""
    with pytest.raises(ValueError, match="half_life_days"):
        retention(1, half_life_days)
