"""Cox-Ross-Rubinstein binomial lattice, European and American exercise.

Same economics as Black-Scholes, in finite steps you can actually look at.

The one-period argument, which is where everything here comes from:

Over a step of length dt the stock does exactly one of two things, up to u*S or
down to d*S. Build a portfolio of Delta shares plus B dollars of bonds and
demand it match the option in BOTH states:

    Delta*u*S + B*exp(r*dt) = V_u
    Delta*d*S + B*exp(r*dt) = V_d

Two linear equations, two unknowns. Subtracting gives

    Delta = (V_u - V_d) / (S*(u - d))

which is the discrete derivative of value with respect to price, i.e. delta,
exactly as on the continuous side. Back-substituting for B and insisting that
today's option price equals the cost of that replicating portfolio collapses
to

    V = exp(-r*dt) * [ qp*V_u + (1 - qp)*V_d ],  qp = (exp((r-q)*dt) - d)/(u - d)

The critical reading: qp was never assumed and nobody estimated it. It fell out
of solving two equations. It behaves like a probability and it is the
risk-neutral measure in its simplest form, but the real-world probability of an
up move appears nowhere, exactly as mu appears nowhere in the PDE.

qp lies strictly in (0, 1) if and only if d < exp((r-q)*dt) < u. That is not a
technical nicety, it is no-arbitrage written in three symbols: outside it the
stock either dominates the bond in both states or is dominated in both. It is
asserted in code before anything else runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from pricing.bsm import Inputs, _check_kind


class ArbitrageViolation(ValueError):
    """Raised when d < exp((r-q)*dt) < u fails, so no risk-neutral qp exists."""


@dataclass(frozen=True)
class Lattice:
    """The CRR parameters for one choice of step count."""

    steps: int
    dt: float
    u: float
    d: float
    growth: float  # exp((r - q) * dt), the per-step risk-neutral growth factor
    qp: float  # risk-neutral up-probability
    disc: float  # exp(-r * dt), the per-step discount factor


def build_lattice(p: Inputs, steps: int) -> Lattice:
    """CRR parameters, with the no-arbitrage condition checked up front.

    u = exp(sigma*sqrt(dt)) and d = 1/u.

    The sqrt(dt) is the same square-root-of-time scaling as the Brownian
    increment dW, and it is what makes the lattice's variance per step match
    sigma^2*dt to leading order. Choosing d = 1/u makes the tree RECOMBINE, so
    n steps give n+1 terminal nodes instead of 2^n. That single choice is the
    difference between a program that runs and one that does not.
    """
    if steps < 1:
        raise ValueError("steps must be at least 1")
    p.validate()

    dt = p.T / steps
    u = math.exp(p.sigma * math.sqrt(dt))
    d = 1.0 / u
    growth = math.exp((p.r - p.q) * dt)

    if not (d < growth < u):
        raise ArbitrageViolation(
            f"no-arbitrage condition d < exp((r-q)dt) < u violated: "
            f"d={d:.6f}, growth={growth:.6f}, u={u:.6f}. "
            f"With sigma={p.sigma} and dt={dt:.6g} the tree cannot straddle the "
            f"risk-free growth rate, so no risk-neutral probability exists. "
            f"Raise sigma, or raise the step count so dt shrinks."
        )

    qp = (growth - d) / (u - d)
    return Lattice(steps=steps, dt=dt, u=u, d=d, growth=growth, qp=qp, disc=math.exp(-p.r * dt))


def _intrinsic(spots: np.ndarray, K: float, kind: str) -> np.ndarray:
    """Payoff if exercised right now at these spot levels."""
    return np.maximum(spots - K, 0.0) if kind == "call" else np.maximum(K - spots, 0.0)


def _terminal_spots(p: Inputs, lat: Lattice) -> np.ndarray:
    """Spot at each terminal node, from all-down to all-up.

    Node j has had j up-moves and (N - j) down-moves, so S_j = S * u^j * d^(N-j).
    Because d = 1/u this is just S * u^(2j - N), which is why the tree
    recombines: the ORDER of the moves does not matter, only the net count.
    """
    j = np.arange(lat.steps + 1)
    return p.S * lat.u ** (2 * j - lat.steps)


def price_tree(p: Inputs, kind: str, steps: int = 500, american: bool = False) -> float:
    """Price a European or American option by backward induction.

    Start at expiry where the value is known exactly (it is the payoff), then
    step backwards. At each node the CONTINUATION value is the discounted
    risk-neutral expectation of the two children. For an American option, take
    max(intrinsic, continuation) at every node.

    That single max is the optimal stopping problem solved by dynamic
    programming. There is nothing more sophisticated to it: the exercise
    boundary is not specified anywhere, it EMERGES from a few thousand
    comparisons.
    """
    _check_kind(kind)
    lat = build_lattice(p, steps)

    spots = _terminal_spots(p, lat)
    values = _intrinsic(spots, p.K, kind)

    for step in range(lat.steps - 1, -1, -1):
        # Discounted risk-neutral expectation over the two children.
        values = lat.disc * (lat.qp * values[1:] + (1.0 - lat.qp) * values[:-1])
        if american:
            # Spot levels one step back. Same recombining formula, shorter row.
            spots = p.S * lat.u ** (2 * np.arange(step + 1) - step)
            values = np.maximum(values, _intrinsic(spots, p.K, kind))

    return float(values[0])


def tree_delta(p: Inputs, kind: str, steps: int = 500, american: bool = False) -> float:
    """Delta read straight off the first branching, the replication way.

    Delta = (V_u - V_d) / (S*(u - d)).

    This is not a finite-difference approximation bolted on afterwards. It is
    the number of shares the replicating portfolio holds, which is where the
    continuous-time delta came from in the first place.
    """
    _check_kind(kind)
    if steps < 2:
        raise ValueError("need at least 2 steps to read a delta off the first branching")
    lat = build_lattice(p, steps)

    up = Inputs(S=p.S * lat.u, K=p.K, T=p.T - lat.dt, r=p.r, sigma=p.sigma, q=p.q)
    dn = Inputs(S=p.S * lat.d, K=p.K, T=p.T - lat.dt, r=p.r, sigma=p.sigma, q=p.q)
    v_u = price_tree(up, kind, steps - 1, american)
    v_d = price_tree(dn, kind, steps - 1, american)
    return (v_u - v_d) / (p.S * (lat.u - lat.d))


def early_exercise_premium(p: Inputs, kind: str, steps: int = 500) -> float:
    """American value minus European value, priced on the SAME lattice.

    Differencing on one lattice rather than against the closed form is
    deliberate: the discretisation error is common to both legs and cancels,
    so what is left is the early-exercise value itself rather than the value
    plus a few basis points of lattice noise.
    """
    return price_tree(p, kind, steps, american=True) - price_tree(p, kind, steps, american=False)


def convergence(
    p: Inputs, kind: str, max_steps: int = 200, american: bool = False
) -> list[tuple[int, float]]:
    """Price at every step count from 1 to max_steps.

    Plotting this should show a DAMPED OSCILLATION, not a smooth curve. The
    error is roughly O(1/N) but its sign depends on where the strike falls
    relative to the terminal nodes, and that alternates as N changes. A
    perfectly smooth convergence curve means something is wrong.
    """
    out = []
    for n in range(1, max_steps + 1):
        try:
            out.append((n, price_tree(p, kind, n, american)))
        except ArbitrageViolation:
            # Very small N with low vol can violate d < growth < u. Skip rather
            # than fabricate a point; the gap in the plot is the honest signal.
            continue
    return out


def lattice_detail(p: Inputs, kind: str, steps: int, american: bool = False) -> dict:
    """Full node-by-node lattice, for displaying a small tree.

    Only sensible for a handful of steps. Returns, for each time slice, the
    spot at every node, the option value there, the intrinsic value, and
    whether an American holder would exercise at that node. That last flag is
    the exercise boundary, discovered rather than assumed.
    """
    _check_kind(kind)
    if steps > 12:
        raise ValueError("lattice_detail is for small trees only (steps <= 12)")
    lat = build_lattice(p, steps)

    spots_by_step = [p.S * lat.u ** (2 * np.arange(n + 1) - n) for n in range(lat.steps + 1)]
    values = _intrinsic(spots_by_step[-1], p.K, kind)
    values_by_step: list[np.ndarray] = [values]
    exercise_by_step: list[np.ndarray] = [
        _intrinsic(spots_by_step[-1], p.K, kind) > 0 if american else np.zeros(steps + 1, bool)
    ]

    for step in range(lat.steps - 1, -1, -1):
        cont = lat.disc * (lat.qp * values[1:] + (1.0 - lat.qp) * values[:-1])
        intr = _intrinsic(spots_by_step[step], p.K, kind)
        if american:
            exercised = intr > cont
            values = np.maximum(cont, intr)
        else:
            exercised = np.zeros(step + 1, bool)
            values = cont
        values_by_step.insert(0, values)
        exercise_by_step.insert(0, exercised)

    return {
        "params": {
            "steps": lat.steps,
            "dt": lat.dt,
            "u": lat.u,
            "d": lat.d,
            "growth": lat.growth,
            "qp": lat.qp,
            "disc": lat.disc,
        },
        "slices": [
            {
                "step": n,
                "spots": spots_by_step[n].tolist(),
                "values": values_by_step[n].tolist(),
                "intrinsic": _intrinsic(spots_by_step[n], p.K, kind).tolist(),
                "exercise": exercise_by_step[n].tolist(),
            }
            for n in range(lat.steps + 1)
        ],
    }
