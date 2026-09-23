"""Every analytic greek, checked against a numerical derivative of price().

The point of this file: a greek formula that is typed from memory with one
wrong sign still returns plausible-looking numbers, and no amount of staring at
it reliably catches that. Differentiating price() numerically does catch it,
because the price function is independently verified against the published
benchmark in test_pricing.py.

So the chain of trust is:
    published benchmark  ->  price()  ->  finite differences  ->  greeks()

Central differences are used throughout. A central difference has error
O(h^2), so with h chosen near the sixth root of machine epsilon for second
derivatives the agreement should be around 1e-6 relative, and it is.
"""

from __future__ import annotations

import math

import pytest

from pricing.bsm import Inputs, greeks, price

# A spread of cases: at the money, deep in, deep out, short dated, long dated,
# low vol, high vol, with and without a dividend yield. A greek sign error that
# survives all of these is not a sign error.
CASES = [
    Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20),
    Inputs(S=100, K=100, T=0.08, r=0.05, sigma=0.20),
    Inputs(S=140, K=100, T=1.0, r=0.05, sigma=0.20),
    Inputs(S=60, K=100, T=1.0, r=0.05, sigma=0.20),
    Inputs(S=100, K=120, T=2.0, r=0.03, sigma=0.45),
    Inputs(S=100, K=90, T=0.5, r=0.00, sigma=0.10),
    Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20, q=0.03),
    Inputs(S=85, K=100, T=1.5, r=0.06, sigma=0.35, q=0.02),
]
KINDS = ["call", "put"]


def _bump(p: Inputs, **kw) -> Inputs:
    """Return a copy of p with one or more fields changed."""
    fields = dict(S=p.S, K=p.K, T=p.T, r=p.r, sigma=p.sigma, q=p.q)
    fields.update(kw)
    return Inputs(**fields)


def fd1(p: Inputs, kind: str, field: str, h: float) -> float:
    """Central first derivative of price with respect to one input."""
    up = price(_bump(p, **{field: getattr(p, field) + h}), kind)
    dn = price(_bump(p, **{field: getattr(p, field) - h}), kind)
    return (up - dn) / (2.0 * h)


def fd2(p: Inputs, kind: str, field: str, h: float) -> float:
    """Central second derivative of price with respect to one input."""
    up = price(_bump(p, **{field: getattr(p, field) + h}), kind)
    mid = price(p, kind)
    dn = price(_bump(p, **{field: getattr(p, field) - h}), kind)
    return (up - 2.0 * mid + dn) / (h * h)


def fd_cross(p: Inputs, kind: str, f1: str, f2: str, h1: float, h2: float) -> float:
    """Central mixed second derivative, d2 price / d f1 d f2."""
    pp = price(_bump(p, **{f1: getattr(p, f1) + h1, f2: getattr(p, f2) + h2}), kind)
    pm = price(_bump(p, **{f1: getattr(p, f1) + h1, f2: getattr(p, f2) - h2}), kind)
    mp = price(_bump(p, **{f1: getattr(p, f1) - h1, f2: getattr(p, f2) + h2}), kind)
    mm = price(_bump(p, **{f1: getattr(p, f1) - h1, f2: getattr(p, f2) - h2}), kind)
    return (pp - pm - mp + mm) / (4.0 * h1 * h2)


def close(actual: float, expected: float, tol: float = 2e-5) -> bool:
    """Relative comparison, falling back to absolute when expected is tiny."""
    scale = max(1.0, abs(expected))
    return abs(actual - expected) / scale < tol


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_delta_matches_numerical(p: Inputs, kind: str) -> None:
    g = greeks(p, kind)
    assert close(g["delta"], fd1(p, kind, "S", 1e-4 * p.S))


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_gamma_matches_numerical(p: Inputs, kind: str) -> None:
    g = greeks(p, kind)
    assert close(g["gamma"], fd2(p, kind, "S", 1e-3 * p.S), tol=1e-4)


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_vega_matches_numerical(p: Inputs, kind: str) -> None:
    g = greeks(p, kind)
    assert close(g["vega"], fd1(p, kind, "sigma", 1e-5))


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_rho_matches_numerical(p: Inputs, kind: str) -> None:
    g = greeks(p, kind)
    assert close(g["rho"], fd1(p, kind, "r", 1e-6))


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_theta_matches_numerical(p: Inputs, kind: str) -> None:
    """theta is dV/dt, and t runs the opposite way to T, hence the minus sign.

    This is the single most commonly botched sign in the whole greek set, which
    is exactly why it gets its own explicit note.
    """
    g = greeks(p, kind)
    assert close(g["theta"], -fd1(p, kind, "T", 1e-6))


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_vanna_matches_numerical(p: Inputs, kind: str) -> None:
    g = greeks(p, kind)
    expected = fd_cross(p, kind, "S", "sigma", 1e-3 * p.S, 1e-4)
    assert close(g["vanna"], expected, tol=1e-4)


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_volga_matches_numerical(p: Inputs, kind: str) -> None:
    g = greeks(p, kind)
    assert close(g["volga"], fd2(p, kind, "sigma", 1e-3), tol=1e-4)


@pytest.mark.parametrize("p", CASES)
@pytest.mark.parametrize("kind", KINDS)
def test_charm_matches_numerical(p: Inputs, kind: str) -> None:
    """charm = d(delta)/dt = -d2V/dS dT. Same time-direction flip as theta."""
    g = greeks(p, kind)
    expected = -fd_cross(p, kind, "S", "T", 1e-3 * p.S, 1e-5)
    assert close(g["charm"], expected, tol=1e-4)


@pytest.mark.parametrize("p", CASES)
def test_parity_invariant_greeks_agree_across_call_and_put(p: Inputs) -> None:
    """gamma, vega, vanna and volga must be identical for a call and a put.

    Put-call parity says C - P = S*exp(-qT) - K*exp(-rT). That difference is
    linear in S and free of sigma, so any derivative of it that is second order
    or higher in S, or touches sigma at all, is zero. Gamma qualifies on the
    first count, vega and volga on the second, vanna on both.

    Deliberately NOT called "second order greeks": vega is first order and the
    tidier name would have made the docstring's own justification wrong.
    """
    c = greeks(p, "call")
    put = greeks(p, "put")
    for name in ("gamma", "vega", "vanna", "volga"):
        assert math.isclose(c[name], put[name], rel_tol=1e-12, abs_tol=1e-12)


@pytest.mark.parametrize("p", CASES)
def test_delta_difference_is_the_parity_slope(p: Inputs) -> None:
    """delta_call - delta_put = exp(-qT), differentiating parity in S."""
    c = greeks(p, "call")["delta"]
    put = greeks(p, "put")["delta"]
    assert math.isclose(c - put, math.exp(-p.q * p.T), rel_tol=1e-12)
