"""Inverting Black-Scholes for the volatility that reproduces a traded price.

The property that makes this well posed is that vega is strictly positive, so
price is strictly increasing in sigma and the inverse is unique. Most of these
tests are really tests of that property, approached from different directions.
"""

from __future__ import annotations

import math

import pytest

from pricing.bsm import Inputs, NoImpliedVol, implied_vol, price

KINDS = ["call", "put"]
CASES = [
    # S, K, T, r, q
    (100, 100, 1.00, 0.05, 0.00),
    (100, 100, 0.08, 0.05, 0.00),
    (140, 100, 1.00, 0.05, 0.00),
    (60, 100, 1.00, 0.05, 0.00),
    (100, 120, 2.00, 0.03, 0.00),
    (339.75, 340, 0.31, 0.05, 0.0031),
    (85, 100, 1.50, 0.06, 0.02),
    (7.5, 10, 0.50, 0.04, 0.01),
]


@pytest.mark.parametrize("S,K,T,r,q", CASES)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("sigma", [0.05, 0.12, 0.20, 0.35, 0.60, 1.20])
def test_round_trip_recovers_the_volatility_it_was_priced_with(
    S, K, T, r, q, kind, sigma
) -> None:
    """Price at a known sigma, invert, get the same sigma back.

    The strongest test available, since it needs no external benchmark: the
    pricer is its own oracle in both directions.

    With one honest exception, which is a real property of the problem rather
    than a concession to the solver. For a far-from-the-money option at low
    volatility, the ENTIRE time value can be smaller than one unit in the last
    place of the price. Every sigma across a wide band then reproduces the
    quote exactly, so there is no implied volatility to recover and the right
    answer is a refusal, not a number.

    So this asserts the dichotomy: either the inversion recovers sigma, or the
    option genuinely had no time value to carry the information. It does NOT
    let a refusal pass unexamined, because that would let a broken solver claim
    every case was degenerate.
    """
    target = price(Inputs(S=S, K=K, T=T, r=r, sigma=sigma, q=q), kind)
    floor = price(Inputs(S=S, K=K, T=T, r=r, sigma=1e-9, q=q), kind)
    time_value = target - floor

    try:
        recovered = implied_vol(target, S, K, T, r, kind, q=q)
    except NoImpliedVol:
        assert time_value < 1e-12, (
            f"refused to invert an option with real time value {time_value:.3e}; "
            f"that is a solver failure, not a degenerate contract"
        )
        return

    assert recovered == pytest.approx(sigma, abs=1e-6)


def test_the_degenerate_cases_really_are_degenerate() -> None:
    """Positive control on the refusal branch above.

    The dichotomy test would be satisfied by a solver that refused everything
    with low time value, so this pins the actual numbers: these six contracts
    have a total volatility value at or below machine noise, meaning they are
    worth their intrinsic value to the last representable bit.
    """
    degenerate = [
        (140, 100, 1.0, 0.05, 0.0),
        (60, 100, 1.0, 0.05, 0.0),
        (7.5, 10, 0.5, 0.04, 0.01),
    ]
    for S, K, T, r, q in degenerate:
        for kind in KINDS:
            target = price(Inputs(S=S, K=K, T=T, r=r, sigma=0.05, q=q), kind)
            floor = price(Inputs(S=S, K=K, T=T, r=r, sigma=1e-9, q=q), kind)
            assert target - floor < 2e-15
            with pytest.raises(NoImpliedVol, match="no volatility information"):
                implied_vol(target, S, K, T, r, kind, q=q)


def test_the_same_contracts_invert_fine_once_they_have_real_time_value() -> None:
    """The other half of the control: degeneracy is about the price, not the strike.

    Same strikes and expiries, higher volatility, so there is now real time
    value. If these also refused, the guard would be rejecting on moneyness
    rather than on information content, which would be wrong.
    """
    for S, K, T, r, q in [(140, 100, 1.0, 0.05, 0.0), (60, 100, 1.0, 0.05, 0.0)]:
        for kind in KINDS:
            target = price(Inputs(S=S, K=K, T=T, r=r, sigma=0.45, q=q), kind)
            assert implied_vol(target, S, K, T, r, kind, q=q) == pytest.approx(0.45, abs=1e-6)


@pytest.mark.parametrize("kind", KINDS)
def test_price_is_strictly_increasing_in_sigma(kind: str) -> None:
    """The property the whole inversion rests on, asserted directly.

    If this were ever false the inverse would not be unique and the solver
    would be returning one of several answers with no way to know which.
    """
    prices = [
        price(Inputs(S=100, K=110, T=0.5, r=0.05, sigma=s, q=0.01), kind)
        for s in [0.01, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.0]
    ]
    assert all(b > a for a, b in zip(prices, prices[1:]))


@pytest.mark.parametrize("kind", KINDS)
def test_a_price_below_the_no_arbitrage_floor_is_refused_not_approximated(kind: str) -> None:
    """Below intrinsic there is no volatility that works, so say so.

    Returning a tiny sigma here would be inventing a number. A price below the
    floor means the quote is stale or the inputs do not describe the contract
    that traded, and that is the useful thing to report.
    """
    S, K, T, r = 140.0, 100.0, 1.0, 0.05
    floor = price(Inputs(S=S, K=K, T=T, r=r, sigma=1e-9), kind)
    with pytest.raises(NoImpliedVol, match="no-arbitrage floor"):
        implied_vol(floor - 1.0, S, K, T, r, kind)


@pytest.mark.parametrize("kind", KINDS)
def test_an_impossibly_high_price_is_refused(kind: str) -> None:
    with pytest.raises(NoImpliedVol, match="exceeds"):
        implied_vol(10_000.0, 100, 100, 1.0, 0.05, kind)


def test_expired_or_nonsense_input_is_refused() -> None:
    with pytest.raises(NoImpliedVol):
        implied_vol(5.0, 100, 100, 0.0, 0.05, "call")
    with pytest.raises(NoImpliedVol):
        implied_vol(float("nan"), 100, 100, 1.0, 0.05, "call")


@pytest.mark.parametrize("kind", KINDS)
def test_the_solution_actually_reprices_to_the_target(kind: str) -> None:
    """Independent of the round trip: feed an arbitrary price, check the answer.

    The round-trip test could in principle pass with a solver that inverted
    some other monotone function. This one takes a price that was never
    generated by the pricer and asserts the returned sigma really does
    reproduce it.
    """
    S, K, T, r, q = 339.75, 340.0, 0.31, 0.05, 0.0031
    for target in (5.0, 12.5, 21.30, 40.0):
        sigma = implied_vol(target, S, K, T, r, kind, q=q)
        assert price(Inputs(S=S, K=K, T=T, r=r, sigma=sigma, q=q), kind) == pytest.approx(
            target, abs=1e-6
        )


def test_call_and_put_on_the_same_contract_imply_the_same_volatility() -> None:
    """Put-call parity again, seen from the volatility side.

    Since C - P is fixed by parity with no sigma in it, a call and a put at the
    same strike and expiry, priced consistently, must imply the same number.
    This is the check a desk uses to spot a bad quote on one leg.
    """
    S, K, T, r, q = 100.0, 105.0, 0.75, 0.04, 0.02
    true_sigma = 0.28
    c = price(Inputs(S=S, K=K, T=T, r=r, sigma=true_sigma, q=q), "call")
    p = price(Inputs(S=S, K=K, T=T, r=r, sigma=true_sigma, q=q), "put")

    iv_c = implied_vol(c, S, K, T, r, "call", q=q)
    iv_p = implied_vol(p, S, K, T, r, "put", q=q)
    assert iv_c == pytest.approx(iv_p, abs=1e-6)
    assert iv_c == pytest.approx(true_sigma, abs=1e-6)


def test_deep_out_of_the_money_still_inverts_where_newton_would_struggle() -> None:
    """The reason the solver is bisection rather than Newton.

    Vega collapses towards zero far from the money, and Newton divides by it,
    so the iterate gets thrown somewhere useless exactly where real quotes are
    most likely to be odd. Bisection cannot diverge. This case has a vega small
    enough to make that a live concern.
    """
    S, K, T, r = 100.0, 250.0, 0.05, 0.05
    sigma = 0.9
    target = price(Inputs(S=S, K=K, T=T, r=r, sigma=sigma), "call")
    vega = (
        price(Inputs(S=S, K=K, T=T, r=r, sigma=sigma + 1e-5), "call")
        - price(Inputs(S=S, K=K, T=T, r=r, sigma=sigma - 1e-5), "call")
    ) / 2e-5
    assert vega < 12.0, "this case is meant to have a small vega"
    assert implied_vol(target, S, K, T, r, "call") == pytest.approx(sigma, abs=1e-6)


def test_implied_vol_is_finite_and_positive_everywhere_it_succeeds() -> None:
    for S, K, T, r, q in CASES:
        for kind in KINDS:
            target = price(Inputs(S=S, K=K, T=T, r=r, sigma=0.3, q=q), kind)
            iv = implied_vol(target, S, K, T, r, kind, q=q)
            assert math.isfinite(iv) and iv > 0
