"""Benchmarks, parity, the Merton theorem, and the desk sanity checks.

"Done" is not "it returns a number." Done means it matches a known benchmark,
the greeks have the right shapes and signs, and the edge cases behave. This
file is that standard, written as code.
"""

from __future__ import annotations

import math

import pytest

from pricing.binomial import (
    ArbitrageViolation,
    build_lattice,
    convergence,
    early_exercise_premium,
    price_tree,
    tree_delta,
)
from pricing.bsm import Inputs, greeks, parity_relative_residual, parity_residual, price

# The reference case handed out with the assignment.
BENCH = Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20)


# ---------------------------------------------------------------- benchmarks


def test_benchmark_european_call() -> None:
    """Published value: 10.4506."""
    assert price(BENCH, "call") == pytest.approx(10.4506, abs=5e-5)


def test_benchmark_european_put() -> None:
    """Published value: 5.5735."""
    assert price(BENCH, "put") == pytest.approx(5.5735, abs=5e-5)


def test_benchmark_american_put_on_a_lattice() -> None:
    """Published value: about 6.09, so the early-exercise premium is about 0.52."""
    assert price_tree(BENCH, "put", steps=2000, american=True) == pytest.approx(6.09, abs=0.01)


def test_benchmark_tree_converges_to_closed_form() -> None:
    """A 2000-step tree gives 10.4496 against the closed form's 10.4506."""
    assert price_tree(BENCH, "call", steps=2000) == pytest.approx(10.4496, abs=5e-4)


# -------------------------------------------------------------- put-call parity


@pytest.mark.parametrize(
    "p",
    [
        BENCH,
        Inputs(S=140, K=100, T=0.25, r=0.05, sigma=0.20),
        Inputs(S=60, K=100, T=3.0, r=0.01, sigma=0.55),
        Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20, q=0.03),
        Inputs(S=7.5, K=10.0, T=0.5, r=0.04, sigma=0.80, q=0.01),
    ],
)
def test_put_call_parity_holds_to_machine_precision(p: Inputs) -> None:
    """C - P = S*exp(-qT) - K*exp(-rT), exactly.

    A pure no-arbitrage identity with no model in it. If this ever fails there
    is a sign error in price(), and parity is what tells you so.
    """
    assert abs(parity_relative_residual(p)) < 1e-12


# ------------------------------------------------------- the Merton theorem


@pytest.mark.parametrize("steps", [50, 200, 800])
def test_american_call_equals_european_call_without_dividends(steps: int) -> None:
    """Merton: an American call on a non-dividend stock is never exercised early.

    Exercising gives exactly S - K. Selling gives at least S - K*exp(-rT).
    With r > 0 and T > 0, K*exp(-rT) < K, so selling strictly beats exercising,
    always. Therefore the American call is worth exactly the European call.

    On a lattice this must hold to machine precision, not approximately: the
    early-exercise test simply never fires, so the two routines walk identical
    arithmetic. A non-zero premium here is a bug in the comparison.
    """
    euro = price_tree(BENCH, "call", steps=steps, american=False)
    amer = price_tree(BENCH, "call", steps=steps, american=True)
    assert amer == pytest.approx(euro, abs=1e-12)


@pytest.mark.parametrize("steps", [50, 200, 800])
def test_american_put_premium_is_strictly_positive(steps: int) -> None:
    """The mirror of the theorem above: for puts, early exercise genuinely bites.

    Exercising a put hands you K in cash now, which earns r. Deep in the money
    the remaining optionality is nearly worthless while the interest on K is
    real, so there is a critical spot below which exercising immediately is
    optimal.
    """
    assert early_exercise_premium(BENCH, "put", steps=steps) > 0.0


def test_american_call_premium_appears_once_a_dividend_does() -> None:
    """With a dividend yield the Merton argument breaks and the premium is positive.

    This is the control on the test above: it proves the American routine is
    genuinely capable of finding early exercise on a call, so the zero premium
    in the non-dividend case is a real economic result rather than a broken
    comparison that can only ever return zero.
    """
    div = Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20, q=0.08)
    assert early_exercise_premium(div, "call", steps=800) > 0.0


# --------------------------------------------------------- the sanity checks


@pytest.mark.parametrize("kind", ["call", "put"])
def test_deep_in_the_money_approaches_intrinsic(kind: str) -> None:
    """Deep ITM, the optionality is worthless and only the forward remains."""
    p = Inputs(S=1000 if kind == "call" else 1, K=100, T=1.0, r=0.05, sigma=0.20)
    v = price(p, kind)
    # Discounted intrinsic against the forward, which is what "intrinsic" means
    # once you account for the fact that the strike is paid at T, not today.
    fwd_intrinsic = (
        p.S - p.K * math.exp(-p.r * p.T)
        if kind == "call"
        else p.K * math.exp(-p.r * p.T) - p.S
    )
    assert v == pytest.approx(fwd_intrinsic, rel=1e-6)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_deep_out_of_the_money_approaches_zero(kind: str) -> None:
    p = Inputs(S=1 if kind == "call" else 1000, K=100, T=1.0, r=0.05, sigma=0.20)
    assert price(p, kind) < 1e-8


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("S", [70, 100, 130])
def test_higher_volatility_always_raises_the_price(kind: str, S: float) -> None:
    """Vol up means price up, for calls AND puts, always.

    This is the convexity result: the payoff is a convex function of the
    terminal price, so by Jensen more randomness is strictly worth more to the
    holder, with no view on direction anywhere in the argument. If this ever
    fails, the model is broken, not the market.
    """
    vols = [0.05, 0.10, 0.20, 0.40, 0.80]
    prices = [price(Inputs(S=S, K=100, T=1.0, r=0.05, sigma=v), kind) for v in vols]
    assert all(b > a for a, b in zip(prices, prices[1:]))


def test_zero_rate_collapses_the_put_early_exercise_edge() -> None:
    """As r goes to 0 the put's early-exercise advantage disappears.

    The entire benefit was collecting interest on K sooner. With r = 0 there is
    no interest to collect, so the American put is worth the European put.
    """
    zero_r = Inputs(S=100, K=100, T=1.0, r=0.0, sigma=0.20)
    assert early_exercise_premium(zero_r, "put", steps=800) == pytest.approx(0.0, abs=1e-10)

    with_r = Inputs(S=100, K=100, T=1.0, r=0.08, sigma=0.20)
    assert early_exercise_premium(with_r, "put", steps=800) > 0.01


# ------------------------------------------------------------ lattice mechanics


def test_risk_neutral_probability_reprices_the_stock_at_the_riskless_rate() -> None:
    """qp*u + (1-qp)*d = exp((r-q)*dt), by construction.

    Under qp the stock is expected to grow at exactly the risk-free rate. Not
    because it does, but because that is the measure under which hedged prices
    are consistent. This identity is what makes qp "risk-neutral" rather than
    a forecast.
    """
    lat = build_lattice(BENCH, 250)
    assert lat.qp * lat.u + (1 - lat.qp) * lat.d == pytest.approx(lat.growth, rel=1e-14)
    assert 0.0 < lat.qp < 1.0


def test_arbitrage_condition_is_enforced_not_assumed() -> None:
    """d < exp((r-q)dt) < u must be checked before anything else runs.

    Violate it deliberately: a tiny volatility against a large rate over one
    long step puts the risk-free growth above the up-move, so the bond
    dominates the stock in both states and no qp in (0,1) exists.
    """
    broken = Inputs(S=100, K=100, T=1.0, r=0.50, sigma=0.01)
    with pytest.raises(ArbitrageViolation):
        build_lattice(broken, steps=1)


def test_tree_delta_matches_the_closed_form_delta() -> None:
    """Delta read off the first branching, against the analytic delta.

    The lattice delta is (V_u - V_d)/(S(u-d)), which is the share count in the
    replicating portfolio, not a finite difference. That it lands on Phi(d1) is
    the discrete and continuous arguments agreeing.
    """
    analytic = greeks(BENCH, "call")["delta"]
    assert tree_delta(BENCH, "call", steps=2000) == pytest.approx(analytic, abs=2e-3)


def test_convergence_oscillates_rather_than_marching_smoothly() -> None:
    """The error should change sign repeatedly as N grows, not decay monotonically.

    The tree price straddles the true value depending on where the strike falls
    relative to the terminal nodes, and that alternates with N. A perfectly
    smooth curve would mean the lattice is not doing what it should.
    """
    exact = price(BENCH, "call")
    errs = [v - exact for _, v in convergence(BENCH, "call", max_steps=60)]
    sign_changes = sum(1 for a, b in zip(errs, errs[1:]) if a * b < 0)
    assert sign_changes > 10, f"expected an oscillating error, saw {sign_changes} sign changes"


@pytest.mark.parametrize("steps", [500, 1000, 2000])
def test_tree_converges_toward_the_closed_form_as_steps_grow(steps: int) -> None:
    """Error shrinks roughly like 1/N."""
    exact = price(BENCH, "call")
    err = abs(price_tree(BENCH, "call", steps=steps) - exact)
    assert err < 5.0 / steps


# ------------------------------------------------- the small-tree display path


@pytest.mark.parametrize("american", [False, True])
@pytest.mark.parametrize("kind", ["call", "put"])
def test_lattice_detail_root_matches_the_production_pricer(kind: str, american: bool) -> None:
    """The node-by-node dump must agree with price_tree at the root.

    lattice_detail exists to draw a small tree in the UI, so it re-implements
    the backward induction in a form that keeps every slice. Re-implementation
    is exactly where a display path silently drifts from the pricer it is
    supposed to be showing, so the two are pinned together here.
    """
    from pricing.binomial import lattice_detail

    p = Inputs(S=100, K=105, T=1.0, r=0.05, sigma=0.30)
    detail = lattice_detail(p, kind, steps=6, american=american)
    root = detail["slices"][0]["values"][0]
    assert root == pytest.approx(price_tree(p, kind, steps=6, american=american), abs=1e-12)


def test_lattice_detail_marks_an_exercise_boundary_on_an_american_put() -> None:
    """Deep in the money, an American put holder exercises, and the dump says so.

    Without this, the exercise flags could be all-False forever and every other
    assertion would still pass.
    """
    from pricing.binomial import lattice_detail

    p = Inputs(S=60, K=100, T=1.0, r=0.08, sigma=0.20)

    # INTERIOR slices only. The terminal slice has its flags set by a separate
    # expression (at expiry, "exercise" just means "finished in the money"), so
    # counting it here would let the whole backward-induction boundary be dead
    # while the assertion still passed. That exact mutation survived until this
    # test was narrowed.
    def interior_flags(steps: int, american: bool) -> int:
        d = lattice_detail(p, "put", steps=steps, american=american)
        return sum(sum(s["exercise"]) for s in d["slices"][:-1])

    assert interior_flags(6, american=True) > 0
    assert interior_flags(6, american=False) == 0


# ------------------------------------------- the degenerate branch (T=0, sigma=0)


"""Why this block exists.

_is_degenerate / _degenerate_price / _degenerate_greeks had ZERO coverage: two
mutations planted inside them (swapping the call and put payoff, flipping the
ITM sign logic) left all 183 other tests green. The code was correct, but it
was one silent edit away from breaking with nothing to catch it.

It is also live-reachable rather than theoretical: app.py computes T = 0 the
moment the date picker's expiry equals today, which is a thing a user does on
expiry day.
"""


@pytest.mark.parametrize(
    "S,kind,expected",
    [
        (110, "call", 10.0),   # in the money by 10
        (90, "call", 0.0),     # out of the money
        (100, "call", 0.0),    # exactly at the kink
        (90, "put", 10.0),
        (110, "put", 0.0),
        (100, "put", 0.0),
    ],
)
def test_at_expiry_the_price_is_exactly_the_payoff(S: float, kind: str, expected: float) -> None:
    """T = 0 must return max(S-K, 0) or max(K-S, 0) exactly, with no NaN.

    The formulas divide by sigma*sqrt(T), so this branch has to be handled
    rather than computed. If it were not, the NaN would propagate silently into
    every greek rather than raising anywhere visible.
    """
    p = Inputs(S=S, K=100, T=0.0, r=0.05, sigma=0.20)
    assert price(p, kind) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("S", [80, 100, 120])
def test_zero_volatility_prices_against_the_known_forward(kind: str, S: float) -> None:
    """With sigma = 0 the terminal price is certain, so the value is exact.

    The stock must arrive at S*exp((r-q)T), so the option is worth the
    discounted intrinsic value against that forward, floored at zero. No normal
    distribution is involved at all.
    """
    p = Inputs(S=S, K=100, T=1.0, r=0.05, sigma=0.0, q=0.02)
    fwd = p.S * math.exp(-p.q * p.T)
    disc_k = p.K * math.exp(-p.r * p.T)
    expected = max(fwd - disc_k, 0.0) if kind == "call" else max(disc_k - fwd, 0.0)
    assert price(p, kind) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_degenerate_greeks_are_finite_and_have_the_right_delta(kind: str) -> None:
    """At expiry, delta is 1 or -1 in the money and 0 out of it, everything else 0.

    Asserting finiteness matters as much as the values: the whole point of the
    branch is that nothing here is allowed to be NaN.
    """
    itm = Inputs(S=130 if kind == "call" else 70, K=100, T=0.0, r=0.05, sigma=0.20)
    otm = Inputs(S=70 if kind == "call" else 130, K=100, T=0.0, r=0.05, sigma=0.20)

    g_itm = greeks(itm, kind)
    assert g_itm["delta"] == pytest.approx(1.0 if kind == "call" else -1.0, abs=1e-12)

    g_otm = greeks(otm, kind)
    assert g_otm["delta"] == pytest.approx(0.0, abs=1e-12)

    for g in (g_itm, g_otm):
        for name, value in g.items():
            assert math.isfinite(value), f"{name} is not finite in the degenerate branch"
            if name != "delta":
                assert value == pytest.approx(0.0, abs=1e-12)


def test_parity_still_holds_in_the_degenerate_branch() -> None:
    """Parity is an identity, so it cannot be excused at the edge of the domain."""
    for p in (
        Inputs(S=110, K=100, T=0.0, r=0.05, sigma=0.20),
        Inputs(S=90, K=100, T=0.0, r=0.05, sigma=0.20),
        Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.0, q=0.02),
    ):
        assert abs(parity_relative_residual(p)) < 1e-12


@pytest.mark.parametrize("S", [1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0, 700_000.0])
def test_parity_holds_across_six_orders_of_magnitude(S: float) -> None:
    """The check must not get weaker just because the underlying is expensive.

    An absolute tolerance on the raw residual silently tightens as prices grow,
    because double-precision error scales with magnitude. At S = 700,000, which
    is roughly where BRK-A trades and is therefore reachable by typing a real
    ticker, the raw residual is about 3e-11 against what used to be a 1e-10
    threshold in the UI: passing, with 3x of headroom, which is not a check so
    much as a coin flip.
    """
    p = Inputs(S=S, K=S * 1.05, T=1.0, r=0.05, sigma=0.20, q=0.01)
    assert abs(parity_relative_residual(p)) < 1e-12


def test_the_relative_parity_check_still_catches_a_real_sign_error() -> None:
    """Positive control: scaling must not make the check unfalsifiable.

    A genuine sign error produces a residual of the same order as the price
    itself, so the ratio lands near 1 and fails by orders of magnitude. Built
    here by evaluating a deliberately WRONG parity relation (C + P instead of
    C - P) and asserting the scaled residual is enormous, which proves the
    scaled check can still fail.
    """
    p = Inputs(S=700_000, K=700_000, T=1.0, r=0.05, sigma=0.20)
    wrong = price(p, "call") + price(p, "put") - (p.S - p.K * math.exp(-p.r * p.T))
    assert abs(wrong / p.S) > 1e-3


def test_the_parity_scaling_is_the_stated_one_not_an_arbitrary_constant() -> None:
    """Pin the divisor itself, not just that the scaled value is small.

    The positive control above computes its own ratio by hand, so it never
    calls parity_relative_residual and a mutation replacing the scale with a
    huge constant survived it: everything divided by 1e18 looks tiny and
    passes. That makes the check unfalsifiable, which is worse than no check.

    So assert the identity the function actually claims:
        relative = residual / max(1, S, K)
    computed independently here.
    """
    for p in (
        Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20),
        Inputs(S=700_000, K=650_000, T=2.0, r=0.04, sigma=0.35, q=0.01),
        Inputs(S=0.5, K=0.75, T=0.5, r=0.03, sigma=0.60),
    ):
        expected_scale = max(1.0, abs(p.S), abs(p.K))
        assert parity_relative_residual(p) == pytest.approx(
            parity_residual(p) / expected_scale, rel=1e-12, abs=1e-18
        )
