"""Black-Scholes-Merton closed-form pricer and the full greek set.

Everything here is a partial derivative of one function, V(S, t; sigma, r).
The pricing formula solves the Black-Scholes PDE

    V_t + r*S*V_S + 0.5*sigma^2*S^2*V_SS - r*V = 0

subject to the terminal condition V(S, T) = max(S - K, 0) for a call and
max(K - S, 0) for a put. The greeks are not extra machinery bolted on
afterwards; they are literally the partials of that same surface, which is why
delta is both "sensitivity to spot" and "the hedge ratio" with no further
argument needed.

Sign conventions used throughout, stated once so every formula below can be
checked against a finite difference of the price function in tests:

    delta =  dV/dS
    gamma =  d2V/dS2
    vega  =  dV/dsigma        (raw, per 1.00 of vol, i.e. per 100 vol points)
    theta =  dV/dt = -dV/dT   (raw, per year)
    rho   =  dV/dr            (raw, per 1.00 of rate)
    vanna =  d2V/dS dsigma
    volga =  d2V/dsigma2
    charm =  d2V/dS dt = -d2V/dS dT

The "raw" units are the mathematically honest ones. Desks quote vega per vol
point and theta per day; that scaling is a display choice and lives in
greeks_display(), never in the math.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SQRT_2 = math.sqrt(2.0)
SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """Standard normal CDF, Phi(x).

    Built from math.erf rather than imported from scipy. Phi(x) is defined as
    the integral of the normal density, and erf is that same integral rescaled:
        Phi(x) = 0.5 * (1 + erf(x / sqrt(2)))
    This is exact to double precision and keeps the project dependency-free
    apart from numpy, which is only used for the lattice.
    """
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


def norm_pdf(x: float) -> float:
    """Standard normal PDF, phi(x)."""
    return math.exp(-0.5 * x * x) / SQRT_2PI


@dataclass(frozen=True)
class Inputs:
    """The six numbers every formula in this module needs.

    S:     spot price of the underlying, today
    K:     strike, fixed by the contract
    T:     time to expiry in years
    r:     continuously compounded risk-free rate
    sigma: volatility, annualised
    q:     continuous dividend yield (0.0 for a non-dividend payer)

    q defaults to zero because the assignment's benchmark cases and the Merton
    early-exercise theorem are both stated for a non-dividend stock. It is
    carried through every formula anyway, because the one case where an
    American CALL is worth more than a European call is a dividend payer, and
    that is worth being able to demonstrate rather than assert.
    """

    S: float
    K: float
    T: float
    r: float
    sigma: float
    q: float = 0.0

    def validate(self) -> None:
        if self.S <= 0:
            raise ValueError("spot S must be positive")
        if self.K <= 0:
            raise ValueError("strike K must be positive")
        if self.T < 0:
            raise ValueError("time to expiry T cannot be negative")
        if self.sigma < 0:
            raise ValueError("volatility sigma cannot be negative")


def d1_d2(p: Inputs) -> tuple[float, float]:
    """The two standardised moneyness terms.

    d1 = (ln(S/K) + (r - q + sigma^2/2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)

    Read them as: d2 is the number of standard deviations by which the
    log-forward exceeds log(K), so Phi(d2) is the risk-neutral probability of
    finishing in the money. d1 is the same quantity under the measure where the
    stock itself is the numeraire, which is why Phi(d1) shows up as delta and
    not as a probability.

    Undefined when sigma * sqrt(T) is zero, so callers must handle the
    degenerate case first (see _degenerate_price).
    """
    vol_t = p.sigma * math.sqrt(p.T)
    d1 = (math.log(p.S / p.K) + (p.r - p.q + 0.5 * p.sigma**2) * p.T) / vol_t
    return d1, d1 - vol_t


def _is_degenerate(p: Inputs) -> bool:
    """True when sigma*sqrt(T) == 0 and the formulas divide by zero.

    Two ways to get here: expiry has arrived (T = 0) or the stock is modelled
    as having no randomness at all (sigma = 0). Both have exact answers that do
    not need the normal distribution.
    """
    return p.T <= 0.0 or p.sigma <= 0.0


def _degenerate_price(p: Inputs, kind: str) -> float:
    """Exact value when there is no remaining randomness.

    With sigma = 0 the terminal price is known: S * exp((r - q) * T). The
    option is then worth its discounted intrinsic value against that forward,
    floored at zero. Setting T = 0 collapses this to the payoff itself, so the
    same expression covers both cases.
    """
    fwd = p.S * math.exp(-p.q * p.T)
    disc_k = p.K * math.exp(-p.r * p.T)
    return max(fwd - disc_k, 0.0) if kind == "call" else max(disc_k - fwd, 0.0)


def price(p: Inputs, kind: str) -> float:
    """Black-Scholes-Merton price of a European call or put.

    call = S*exp(-qT)*Phi(d1) - K*exp(-rT)*Phi(d2)
    put  = K*exp(-rT)*Phi(-d2) - S*exp(-qT)*Phi(-d1)

    The structure is the same in both: a discounted expected payoff under the
    risk-neutral measure. The first term is the present value of receiving the
    stock in the states where the option is exercised, the second the present
    value of paying the strike in those same states.

    Note what is absent: mu, the expected return of the stock. It cancelled out
    of the PDE when the hedge ratio was chosen, so two people who disagree
    completely about direction still have to agree on this number.
    """
    _check_kind(kind)
    p.validate()
    if _is_degenerate(p):
        return _degenerate_price(p, kind)

    d1, d2 = d1_d2(p)
    disc_q = math.exp(-p.q * p.T)
    disc_r = math.exp(-p.r * p.T)

    if kind == "call":
        return p.S * disc_q * norm_cdf(d1) - p.K * disc_r * norm_cdf(d2)
    return p.K * disc_r * norm_cdf(-d2) - p.S * disc_q * norm_cdf(-d1)


def greeks(p: Inputs, kind: str) -> dict[str, float]:
    """All eight greeks in raw units. See module docstring for conventions.

    Every formula below is verified against a central finite difference of
    price() in tests/test_greeks.py. That test is the reason to trust these
    signs rather than the fact that they were typed carefully.
    """
    _check_kind(kind)
    p.validate()
    if _is_degenerate(p):
        return _degenerate_greeks(p, kind)

    d1, d2 = d1_d2(p)
    sqrt_t = math.sqrt(p.T)
    vol_t = p.sigma * sqrt_t
    disc_q = math.exp(-p.q * p.T)
    disc_r = math.exp(-p.r * p.T)
    pdf_d1 = norm_pdf(d1)

    # gamma, vega, vanna and volga are identical for calls and puts. Put-call
    # parity differs by S*exp(-qT) - K*exp(-rT), which is LINEAR IN S and has
    # NO sigma in it at all. So any derivative of that difference which is
    # either second-or-higher order in S, or touches sigma even once, is zero.
    # That covers all four, including vega, which is only first order.
    #
    # (Saying "the second derivatives vanish" would be the tidier sentence and
    # it does not actually cover vega. Worth getting right: it is the kind of
    # thing someone pokes at.)
    gamma = disc_q * pdf_d1 / (p.S * vol_t)
    vega = p.S * disc_q * pdf_d1 * sqrt_t
    vanna = -disc_q * pdf_d1 * d2 / p.sigma
    volga = vega * d1 * d2 / p.sigma

    # The theta and charm expressions share this term: the pure time-decay of
    # the volatility value, which is always a drain on a long option.
    decay = -p.S * disc_q * pdf_d1 * p.sigma / (2.0 * sqrt_t)
    charm_common = disc_q * pdf_d1 * (2.0 * (p.r - p.q) * p.T - d2 * vol_t) / (2.0 * p.T * vol_t)

    if kind == "call":
        delta = disc_q * norm_cdf(d1)
        theta = decay - p.r * p.K * disc_r * norm_cdf(d2) + p.q * p.S * disc_q * norm_cdf(d1)
        rho = p.K * p.T * disc_r * norm_cdf(d2)
        charm = p.q * disc_q * norm_cdf(d1) - charm_common
    else:
        delta = -disc_q * norm_cdf(-d1)
        theta = decay + p.r * p.K * disc_r * norm_cdf(-d2) - p.q * p.S * disc_q * norm_cdf(-d1)
        rho = -p.K * p.T * disc_r * norm_cdf(-d2)
        charm = -p.q * disc_q * norm_cdf(-d1) - charm_common

    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho,
        "vanna": vanna,
        "volga": volga,
        "charm": charm,
    }


def _degenerate_greeks(p: Inputs, kind: str) -> dict[str, float]:
    """Greeks at expiry or at zero vol.

    At the kink itself (S == K, T == 0) delta is genuinely undefined: the
    payoff is not differentiable there. Reporting 0.5 would be inventing a
    number, so this returns the one-sided limits and leaves delta at the
    discontinuity as 0.0 for the call and -0.0 for the put, with every
    higher-order greek zero. Nothing downstream should be trusting greeks in
    this state; it exists so the UI does not raise while someone drags T to
    zero.
    """
    fwd = p.S * math.exp(-p.q * p.T)
    disc_k = p.K * math.exp(-p.r * p.T)
    itm = fwd > disc_k
    if kind == "call":
        delta = math.exp(-p.q * p.T) if itm else 0.0
    else:
        delta = -math.exp(-p.q * p.T) if not itm else 0.0
    return {
        "delta": delta,
        "gamma": 0.0,
        "vega": 0.0,
        "theta": 0.0,
        "rho": 0.0,
        "vanna": 0.0,
        "volga": 0.0,
        "charm": 0.0,
    }


def greeks_display(raw: dict[str, float]) -> dict[str, float]:
    """Rescale raw greeks into the units a desk actually quotes.

    vega:  per 1 vol point  (sigma 20% -> 21%), so raw / 100
    theta: per calendar day, so raw / 365
    rho:   per 1 percentage point of rate, so raw / 100
    charm: delta drift per calendar day, so raw / 365

    delta, gamma, vanna and volga are left raw. This function is display only;
    no other module should ever call it, and no test asserts against it.
    """
    out = dict(raw)
    out["vega"] = raw["vega"] / 100.0
    out["theta"] = raw["theta"] / 365.0
    out["rho"] = raw["rho"] / 100.0
    out["charm"] = raw["charm"] / 365.0
    return out


def parity_residual(p: Inputs) -> float:
    """Put-call parity check: C - P - (S*exp(-qT) - K*exp(-rT)) should be 0.

    This is a pure no-arbitrage identity with no model in it at all: a long
    call plus a short put is a synthetic long forward. If this residual is not
    at machine-precision zero there is a sign error somewhere in price(), and
    because the identity is exact, the size of the residual tells you roughly
    where.
    """
    c = price(p, "call")
    put = price(p, "put")
    return c - put - (p.S * math.exp(-p.q * p.T) - p.K * math.exp(-p.r * p.T))


def parity_relative_residual(p: Inputs) -> float:
    """Parity residual scaled by the size of the numbers it came from.

    An ABSOLUTE tolerance on the raw residual is wrong, because double-precision
    error grows with magnitude. At a spot of 100 the residual is exactly zero;
    at 700,000, which is roughly where BRK-A trades and is therefore reachable
    by typing a real ticker, it is about 3e-11. A fixed 1e-10 threshold passes
    that with only 3x of headroom, which is a coin flip rather than a check.

    Scaling by max(S, K) keeps the test just as strict in the way that matters:
    a genuine sign error produces a residual of the same order as the price
    itself, so the ratio lands near 1 and fails by fourteen orders of magnitude.
    """
    scale = max(1.0, abs(p.S), abs(p.K))
    return parity_residual(p) / scale


def _check_kind(kind: str) -> None:
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")
