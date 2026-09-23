"""Market-data assembly, tested without ever touching the network.

The risk in this module is not the arithmetic, it is the UNITS and the missing
data. So these tests aim at exactly that: a NaN close that must not reach a
price, a dividend field that must be derived rather than read, a volatility
that must refuse to be computed from too few points, and a guarantee that no
test run can ever reach Yahoo.
"""

from __future__ import annotations

import json
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


# ----------------------------------------- the trailing-dividend window itself

import datetime  # noqa: E402

from pricing.market import _cache, trailing_dividend_total  # noqa: E402

TODAY = datetime.date(2026, 9, 23)


def quarterly(last: datetime.date, n: int, amount: float = 0.26):
    """n quarterly payments ending at `last`, oldest first."""
    return [(last - datetime.timedelta(days=91 * i), amount) for i in range(n)][::-1]


def test_the_window_is_anchored_to_today_not_to_the_last_payment() -> None:
    """The HIGH finding from review, pinned with the real numbers.

    AAPL's last ex-date was 2026-08-10, 44 days before 2026-09-23. Anchoring
    the 365-day window to that payment instead of to today slides the window
    back 44 days and pulls a FIFTH quarterly payment in: 1.32 against a correct
    1.06, a 24.5% overstatement, flowing silently into q and from there into
    d1, the discounting, delta, theta and charm.
    """
    payments = [
        (datetime.date(2025, 2, 10), 0.25),
        (datetime.date(2025, 5, 12), 0.26),
        (datetime.date(2025, 8, 11), 0.26),
        (datetime.date(2025, 11, 10), 0.26),
        (datetime.date(2026, 2, 9), 0.26),
        (datetime.date(2026, 5, 11), 0.27),
        (datetime.date(2026, 8, 10), 0.27),
    ]
    assert trailing_dividend_total(payments, TODAY) == pytest.approx(1.06)

    # And explicitly NOT the old answer, so a revert cannot pass quietly.
    assert trailing_dividend_total(payments, TODAY) != pytest.approx(1.32)


def test_a_suspended_dividend_reports_zero_rather_than_a_stale_year() -> None:
    """The worse direction of the same bug: a non-payer reported as a payer.

    A company that stopped paying two years ago has a last-payment date two
    years back, so a window anchored to it still sits over a full year of
    historical payments and returns a healthy yield for a stock that now pays
    nothing at all, with no error raised anywhere.
    """
    stopped = quarterly(datetime.date(2024, 9, 1), 4)
    assert trailing_dividend_total(stopped, TODAY) == 0.0


def test_a_declared_but_unpaid_future_dividend_is_excluded() -> None:
    """A trailing figure must not include something that has not happened."""
    payments = quarterly(datetime.date(2026, 8, 10), 4) + [
        (datetime.date(2026, 11, 9), 0.27),  # declared, ex-date still ahead
    ]
    assert trailing_dividend_total(payments, TODAY) == pytest.approx(0.26 * 4)


def test_exactly_on_the_boundary_is_excluded_and_one_day_inside_is_not() -> None:
    """Pin the boundary rather than leaving it to chance."""
    on_it = TODAY - datetime.timedelta(days=365)
    assert trailing_dividend_total([(on_it, 1.0)], TODAY) == 0.0
    assert trailing_dividend_total([(on_it + datetime.timedelta(days=1), 1.0)], TODAY) == 1.0
    assert trailing_dividend_total([(TODAY, 1.0)], TODAY) == 1.0


def test_a_non_payer_has_no_payments_at_all() -> None:
    assert trailing_dividend_total([], TODAY) == 0.0


# ---------------------------------------------------------------- the cache


@pytest.fixture(autouse=True)
def clear_quote_cache():
    """The cache is module state, so it has to be reset between tests."""
    _cache.clear()
    yield
    _cache.clear()


def test_a_repeat_fetch_inside_the_ttl_is_served_from_cache() -> None:
    calls = []

    def counting(ticker):
        calls.append(ticker)
        return make_raw()

    t = [1000.0]
    fetch_quote("AAA", fetcher=counting, now=lambda: t[0])
    fetch_quote("AAA", fetcher=counting, now=lambda: t[0] + 30)
    assert len(calls) == 1, "second call inside the TTL should not have refetched"


def test_the_cache_expires_and_does_not_serve_a_stale_quote() -> None:
    calls = []

    def counting(ticker):
        calls.append(ticker)
        return make_raw()

    t = [1000.0]
    fetch_quote("AAA", fetcher=counting, now=lambda: t[0])
    fetch_quote("AAA", fetcher=counting, now=lambda: t[0] + 61)
    assert len(calls) == 2, "a quote older than the TTL should have been refetched"


def test_the_cache_does_not_collide_across_tickers() -> None:
    def per_ticker(ticker):
        return make_raw(spot=100.0 if ticker == "AAA" else 200.0)

    t = [1000.0]
    a = fetch_quote("AAA", fetcher=per_ticker, now=lambda: t[0])
    b = fetch_quote("BBB", fetcher=per_ticker, now=lambda: t[0])
    assert (a.spot, b.spot) == (100.0, 200.0)


def test_the_leaf_fetcher_refuses_too_not_just_the_entry_point() -> None:
    """Defence in depth on the network guard, asserted rather than claimed.

    fetch_quote checks PYTEST_CURRENT_TEST, and _default_fetcher checks it
    again. The second check is redundant for every current call site, which
    means it is exactly the kind of line that rots untested: a mutation
    deleting it survived the whole suite, the same shape as the dead guard
    already removed from _annualised_vol.

    A guard nobody tests is not defence in depth, it is decoration. So this
    calls the leaf directly. If someone later adds a caller that reaches
    _default_fetcher without going through fetch_quote, this is what stops the
    suite quietly starting to depend on Yahoo being up.
    """
    from pricing.market import _default_fetcher

    with pytest.raises(MarketDataUnavailable, match="refusing to fetch live market data"):
        _default_fetcher("AAPL")


# ------------------------------------------------------- the option chain


from pricing.market import build_chain, fetch_chain  # noqa: E402


def chain_rows(spot=100.0, sigma=0.30, T=0.5, r=0.05, q=0.0, kind="call", strikes=None):
    """Rows priced at a KNOWN sigma, so the inversion has a right answer."""
    from pricing.bsm import Inputs, price

    strikes = strikes or [80, 90, 100, 110, 120]
    return [
        {
            "strike": float(k),
            "lastPrice": price(Inputs(S=spot, K=float(k), T=T, r=r, sigma=sigma, q=q), kind),
            "volume": 100.0,
            "openInterest": 50.0,
            "lastTradeDate": "2026-09-22 20:00:00",
            "impliedVolatility": 0.00001,  # the feed's broken value
            "inTheMoney": spot > k,
        }
        for k in strikes
    ]


def test_every_row_recovers_the_volatility_it_was_priced_with() -> None:
    """The chain inverts each contract's own price, not a feed field."""
    rows = build_chain(chain_rows(), spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")
    assert len(rows) == 5
    for row in rows:
        assert row.implied_vol == pytest.approx(0.30, abs=1e-6), row.strike
        assert row.implied_vol_error is None


def test_the_feeds_own_broken_iv_is_carried_but_never_used() -> None:
    """Both numbers are surfaced so they can be compared, not conflated.

    The feed's field really is broken: measured against live AAPL it returned
    0.025% to 0.099% across every expiry while the traded prices imply 23% to
    27%. Carrying it labelled is useful; trusting it would not be.
    """
    rows = build_chain(chain_rows(), spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")
    for row in rows:
        assert row.yahoo_implied_vol == pytest.approx(0.00001)
        assert row.implied_vol == pytest.approx(0.30, abs=1e-6)
        assert row.implied_vol != pytest.approx(row.yahoo_implied_vol)


def test_an_uninvertible_row_keeps_its_reason_instead_of_vanishing() -> None:
    """A dropped row makes the chain look cleaner than it is.

    A price below intrinsic is a stale or wrong quote, and that is worth seeing
    rather than quietly removing.
    """
    rows = chain_rows()
    rows[0]["lastPrice"] = 0.01  # far below intrinsic for the 80 strike
    built = build_chain(rows, spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")

    assert len(built) == 5, "the row must still be present"
    assert built[0].implied_vol is None
    assert built[0].implied_vol_error is not None
    assert "no-arbitrage floor" in built[0].implied_vol_error
    # and the rest are unaffected
    assert all(r.implied_vol == pytest.approx(0.30, abs=1e-6) for r in built[1:])


def test_puts_invert_to_the_same_volatility_as_calls() -> None:
    c = build_chain(chain_rows(kind="call"), 100.0, 0.5, 0.05, 0.0, "call")
    p = build_chain(chain_rows(kind="put"), 100.0, 0.5, 0.05, 0.0, "put")
    for a, b in zip(c, p):
        assert a.implied_vol == pytest.approx(b.implied_vol, abs=1e-6)


def test_an_expiry_in_the_past_is_refused() -> None:
    with pytest.raises(MarketDataUnavailable, match="not in the future"):
        fetch_chain("AAPL", "2020-01-17", 100.0, 0.05, 0.0, "call",
                    as_of=datetime.date(2026, 9, 23), fetcher=lambda *a: [])


def test_todays_expiry_is_refused_because_nothing_is_left_to_price() -> None:
    with pytest.raises(MarketDataUnavailable, match="not in the future"):
        fetch_chain("AAPL", "2026-09-23", 100.0, 0.05, 0.0, "call",
                    as_of=datetime.date(2026, 9, 23), fetcher=lambda *a: [])


def test_a_malformed_expiry_is_refused() -> None:
    with pytest.raises(MarketDataUnavailable, match="not a date"):
        fetch_chain("AAPL", "next friday", 100.0, 0.05, 0.0, "call",
                    as_of=datetime.date(2026, 9, 23), fetcher=lambda *a: [])


def test_time_to_expiry_is_computed_from_today_not_assumed() -> None:
    out = fetch_chain(
        "AAPL", "2027-09-23", 100.0, 0.05, 0.0, "call",
        as_of=datetime.date(2026, 9, 23), fetcher=lambda *a: chain_rows(T=1.0),
    )
    assert out["days_to_expiry"] == 365
    assert out["T"] == pytest.approx(1.0)


# ------------------------------------------- non-finite values from the feed


def test_a_nan_volume_never_reaches_the_response() -> None:
    """Feeds send NaN in numeric columns, and it breaks the browser, not Python.

    An illiquid strike comes back with volume NaN rather than 0 or null. The
    obvious idiom `float(row.get("volume") or 0.0)` passes it straight through,
    because NaN is TRUTHY, so `NaN or 0.0` is NaN.

    Nothing then fails on the Python side: json.dumps emits a bare NaN token as
    a non-standard extension and json.loads reads it back. The browser's
    JSON.parse is strict and rejects it, so the entire chain response fails to
    parse and the page shows a raw parser error. One bad strike takes down the
    whole view.
    """
    rows = chain_rows()
    rows[2]["volume"] = float("nan")
    rows[3]["openInterest"] = float("nan")

    assert math.isnan(rows[2]["volume"]), "the fixture must actually contain a NaN"

    built = build_chain(rows, spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")
    for row in built:
        assert math.isfinite(row.volume), f"NaN volume survived at {row.strike}"
        assert math.isfinite(row.open_interest)


def test_the_serialised_chain_is_strict_json() -> None:
    """The real assertion: what ships must parse under STRICT json.

    Python's own loads accepts bare NaN, so round-tripping through this module
    would pass while the browser still failed. parse_constant is what makes
    this test speak for the browser rather than for Python.
    """
    rows = chain_rows()
    rows[1]["volume"] = float("nan")
    rows[2]["impliedVolatility"] = float("nan")
    rows[4]["openInterest"] = float("inf")

    built = build_chain(rows, spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")
    payload = json.dumps([r.to_dict() for r in built])

    assert "NaN" not in payload
    assert "Infinity" not in payload

    def reject(token):
        raise AssertionError(f"non-standard JSON token {token!r} would break JSON.parse")

    json.loads(payload, parse_constant=reject)


def test_a_missing_feed_volatility_is_null_not_a_fabricated_zero() -> None:
    """Absence and zero are different statements, and only one is true.

    This value is displayed beside our own implied vol for comparison. A
    substituted 0.0 would read as "the feed says zero volatility" rather than
    "the feed gave no value".
    """
    rows = chain_rows()
    rows[0]["impliedVolatility"] = float("nan")
    rows[1]["impliedVolatility"] = None

    built = build_chain(rows, spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")
    assert built[0].yahoo_implied_vol is None
    assert built[1].yahoo_implied_vol is None
    assert built[2].yahoo_implied_vol == pytest.approx(0.00001)


def test_a_garbage_numeric_field_does_not_raise() -> None:
    """A string where a number belongs should degrade, not take the page down."""
    rows = chain_rows()
    rows[0]["volume"] = "n/a"
    built = build_chain(rows, spot=100.0, T=0.5, r=0.05, q=0.0, kind="call")
    assert built[0].volume == 0.0
