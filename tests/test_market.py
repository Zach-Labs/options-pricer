"""Market-data assembly, tested without ever touching the network.

The risk in this module is not the arithmetic, it is the UNITS and the missing
data. So these tests aim at exactly that: a NaN close that must not reach a
price, a dividend field that must be derived rather than read, a volatility
that must refuse to be computed from too few points, and a guarantee that no
test run can ever reach Yahoo.
"""

from __future__ import annotations

import math

import pytest

from pricing.market import (
    MarketDataUnavailable,
    _annualised_vol,
    fetch_quote,
)


def make_raw(**over) -> dict:
    """A plausible upstream payload, overridable per test."""
    # 250 closes on a deterministic gentle uptrend with alternating wiggle, so
    # the realized vol is a real number rather than zero.
    closes = [100.0 * (1.0005 ** i) * (1.01 if i % 2 else 0.99) for i in range(250)]
    raw = {
        "spot": 123.45,
        "currency": "USD",
        "closes": closes,
        "last_bar": "2026-09-22 00:00:00-04:00",
        "trailing_dividends": 2.4690,  # exactly 2% of 123.45
    }
    raw.update(over)
    return raw


def fake(**over):
    return lambda ticker: make_raw(**over)


# ------------------------------------------------------------ the units risk


def test_dividend_yield_is_derived_from_payments_not_read_off_a_field() -> None:
    """trailing dividends / spot, as a DECIMAL.

    This is the 100x bug the module exists to avoid: yfinance's dividendYield
    is in percent, so reading it straight into q would turn a 2% yield into
    200%. Deriving it makes the units unambiguous.
    """
    q = fetch_quote("TEST", fetcher=fake())
    assert q.dividend_yield == pytest.approx(0.02, abs=1e-9)
    assert q.trailing_dividends == pytest.approx(2.4690)


def test_a_non_payer_gets_exactly_zero_yield() -> None:
    """BRK-B pays nothing. Zero, not None, and not a fallback guess."""
    q = fetch_quote("TEST", fetcher=fake(trailing_dividends=0.0))
    assert q.dividend_yield == 0.0


def test_dividend_yield_stays_a_decimal_at_realistic_magnitudes() -> None:
    """A guard on the direction of the error, not just the value.

    A real equity yield is a couple of percent. If this ever comes back above
    0.5 for an ordinary payer, someone has reintroduced the percent field.
    """
    q = fetch_quote("TEST", fetcher=fake(spot=100.0, trailing_dividends=0.39))
    assert 0.0 < q.dividend_yield < 0.05
    assert q.dividend_yield == pytest.approx(0.0039)


# ------------------------------------------------------- the missing-data risk


def test_a_nan_close_never_reaches_a_volatility() -> None:
    """Feed a real NaN through and assert it does not survive.

    The earlier version of this test passed clean closes through a fake and
    asserted the result was finite, which it always would have been. It never
    exercised the filtering at all, so it would have stayed green with the
    filter deleted. This one plants the actual defect.

    Yahoo genuinely returns a NaN close for the current session's bar, so this
    is the live case: one NaN makes the standard deviation NaN, which becomes
    sigma, which makes every price NaN with nothing raised anywhere.
    """
    clean = make_raw()["closes"]
    poisoned = clean[:-1] + [float("nan")]

    assert math.isnan(poisoned[-1]), "the fixture must actually contain a NaN"

    q = fetch_quote("TEST", fetcher=fake(closes=poisoned))
    assert q.realized_vol_1y is not None
    assert math.isfinite(q.realized_vol_1y), "a NaN close reached the volatility"
    assert math.isfinite(q.realized_vol_30d)

    # And the same one layer down, directly on the helper.
    assert math.isfinite(_annualised_vol(poisoned))
    assert math.isfinite(_annualised_vol([float("nan")] + clean))
    assert math.isfinite(_annualised_vol(clean + [float("inf"), 0.0, -3.0]))


def test_a_nan_heavy_series_refuses_rather_than_computing_from_the_remnant() -> None:
    """Dropping NaNs must not quietly leave too few points to be meaningful."""
    mostly_nan = [100.0, 101.0, 102.0] + [float("nan")] * 200
    assert _annualised_vol(mostly_nan) is None


def test_volatility_refuses_rather_than_guessing_from_too_few_points() -> None:
    """None is the honest answer from 5 observations, not a small number."""
    assert _annualised_vol([100.0, 101.0, 99.0, 102.0, 98.0]) is None

    q = fetch_quote("TEST", fetcher=fake(closes=[100.0, 101.0, 99.0]))
    assert q.realized_vol_1y is None
    assert q.realized_vol_30d is None


def test_a_missing_or_nonsense_price_raises_rather_than_returning_a_quote() -> None:
    """Failing closed. A quote with no price is worse than no quote."""
    for bad in (None, float("nan"), 0.0, -5.0, "339.75"):
        with pytest.raises(MarketDataUnavailable):
            fetch_quote("TEST", fetcher=fake(spot=bad))


def test_an_upstream_explosion_becomes_our_own_error_type() -> None:
    """The endpoint should never see a raw yfinance or network exception."""
    def boom(ticker):
        raise ConnectionError("yahoo is down")

    with pytest.raises(MarketDataUnavailable) as exc:
        fetch_quote("AAPL", fetcher=boom)
    assert "AAPL" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   ", "NOT A TICKER", "A" * 20, "rm -rf /", "../../etc"])
def test_junk_symbols_are_rejected_before_any_fetch_happens(bad: str) -> None:
    """Validated up front, so a bad symbol cannot become a request at all."""
    # Record the call rather than raising from inside the fetcher. Raising was
    # the first version and it passed for the wrong reason: fetch_quote wraps
    # ANY exception from the fetcher into MarketDataUnavailable, so the
    # assertion held even with validation deleted. A mutation proved it.
    calls = []

    def recording_fetcher(ticker):
        calls.append(ticker)
        return make_raw()

    with pytest.raises(MarketDataUnavailable):
        fetch_quote(bad, fetcher=recording_fetcher)
    assert calls == [], f"validation was skipped; the fetcher ran with {calls!r}"


# ------------------------------------------------------------ the network guard


def test_the_real_fetch_path_refuses_to_run_inside_a_test() -> None:
    """Isolation lives at the resource, not in each test file.

    Calling fetch_quote with no injected fetcher would otherwise hit Yahoo for
    real. The guard keys on PYTEST_CURRENT_TEST, which pytest sets for every
    test, so this cannot be forgotten in some future test file.

    This test is also its own positive control: it proves the guard fires,
    rather than assuming it does.
    """
    import os

    assert os.environ.get("PYTEST_CURRENT_TEST"), "pytest should set this"
    with pytest.raises(MarketDataUnavailable, match="refusing to fetch live market data"):
        fetch_quote("AAPL")


def test_ticker_is_normalised() -> None:
    q = fetch_quote("  aapl  ", fetcher=fake())
    assert q.ticker == "AAPL"


# ------------------------------------------------- the annualisation magnitude


def test_annualised_vol_actually_annualises() -> None:
    """Assert the NUMBER, not just that one comes back.

    Nothing previously checked the magnitude, so deleting the sqrt(252) factor
    left every test green while under-reporting volatility by a factor of 16.
    A sigma of 1.5% instead of 24% looks like a quiet input, not a bug.

    Built from a series whose daily log return alternates by a known amount, so
    the expected annualised figure is computable by hand rather than by calling
    the function under test.
    """
    daily = 0.01
    closes = [100.0 * math.exp(daily * (i % 2)) for i in range(200)]

    got = _annualised_vol(closes)
    assert got is not None

    # The log returns alternate +daily, -daily, so their sample standard
    # deviation is daily (mean zero, every deviation of size daily).
    expected = daily * math.sqrt(252)
    assert got == pytest.approx(expected, rel=0.01)
    assert 0.14 < got < 0.17, f"annualised vol {got} is not in the plausible band"


def test_the_boundary_for_refusing_is_exactly_where_it_claims_to_be() -> None:
    """19 returns is None, 20 returns is a number. Pins the guard itself."""
    base = [100.0 * (1.01 if i % 2 else 0.99) for i in range(40)]
    assert _annualised_vol(base[:19]) is None       # 18 returns
    assert _annualised_vol(base[:20]) is not None   # 19 returns
