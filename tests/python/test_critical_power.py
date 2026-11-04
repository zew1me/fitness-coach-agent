"""Critical power fit: recovers CP and W' from maximal efforts (issue #250)."""

import pytest

from backend.engine.critical_power import MaxEffort, fit_critical_power

CP_WATTS = 280
W_PRIME_JOULES = 20_000


def _effort(duration_seconds: int) -> MaxEffort:
    """A perfectly maximal effort for the known athlete: P(t) = CP + W'/t."""
    return MaxEffort(duration_seconds, CP_WATTS + W_PRIME_JOULES / duration_seconds)


def test_recovers_known_cp_and_w_prime_from_three_efforts() -> None:
    fit = fit_critical_power([_effort(180), _effort(480), _effort(1200)])

    assert fit.cp_watts == CP_WATTS
    assert fit.w_prime_joules == W_PRIME_JOULES
    assert fit.cp_standard_error_watts == 0
    assert fit.w_prime_standard_error_joules == 0
    assert fit.confidence == "high"


def test_realistically_noisy_maximal_efforts_stay_high_confidence() -> None:
    fit = fit_critical_power(
        [
            MaxEffort(180, 395),
            MaxEffort(300, 345),
            MaxEffort(720, 312),
            MaxEffort(1200, 297),
        ]
    )

    assert fit.cp_watts == pytest.approx(CP_WATTS, abs=2)
    assert fit.w_prime_joules == pytest.approx(W_PRIME_JOULES, abs=1000)
    assert fit.confidence == "high"


def test_derives_ftp_and_lt1_from_cp() -> None:
    fit = fit_critical_power([_effort(180), _effort(480), _effort(1200)])

    assert fit.ftp_watts == CP_WATTS
    assert fit.lt1_watts == 210


def test_effort_order_does_not_change_the_fit() -> None:
    efforts = [_effort(180), _effort(480), _effort(1200)]

    assert fit_critical_power(efforts) == fit_critical_power(efforts[::-1])


def test_two_efforts_fit_but_cannot_be_cross_checked() -> None:
    fit = fit_critical_power([_effort(180), _effort(720)])

    assert fit.cp_watts == CP_WATTS
    assert fit.cp_standard_error_watts is None
    assert fit.w_prime_standard_error_joules is None
    assert fit.confidence == "low"


def test_efforts_without_a_long_anchor_are_medium_confidence() -> None:
    fit = fit_critical_power([_effort(180), _effort(300), _effort(480)])

    assert fit.confidence == "medium"


def test_efforts_that_disagree_with_the_model_are_low_confidence() -> None:
    fit = fit_critical_power(
        [
            MaxEffort(180, 400),
            MaxEffort(420, 300),
            MaxEffort(720, 340),
            MaxEffort(1200, 290),
        ]
    )

    assert fit.cp_standard_error_watts is not None
    assert fit.confidence == "low"


@pytest.mark.parametrize(
    ("efforts", "message"),
    [
        ([], "at least 2"),
        ([MaxEffort(300, 350)], "at least 2"),
        ([MaxEffort(179, 400), MaxEffort(1200, 300)], "between 3 and 20 minutes"),
        ([MaxEffort(180, 400), MaxEffort(1201, 300)], "between 3 and 20 minutes"),
        ([MaxEffort(300, 0), MaxEffort(1200, 300)], "positive"),
        ([MaxEffort(300, 350), MaxEffort(300, 345)], "at least 2 minutes apart"),
        ([MaxEffort(300, 350), MaxEffort(360, 340)], "at least 2 minutes apart"),
    ],
)
def test_rejects_efforts_that_cannot_support_a_fit(efforts: list[MaxEffort], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        fit_critical_power(efforts)


@pytest.mark.parametrize(
    "efforts",
    [
        # Longer effort held more power: the short effort was not maximal, W' < 0.
        [MaxEffort(300, 280), MaxEffort(1200, 300)],
        # Work fell as duration grew, so CP < 0.
        [MaxEffort(180, 1000), MaxEffort(1200, 50)],
        # Casual "best 20 min" against a real 5 min test: W' far beyond human range.
        [MaxEffort(300, 400), MaxEffort(1200, 150)],
    ],
)
def test_rejects_physiologically_impossible_fits(efforts: list[MaxEffort]) -> None:
    with pytest.raises(ValueError, match="maximal"):
        fit_critical_power(efforts)
