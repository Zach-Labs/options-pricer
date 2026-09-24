"""Localhost front end for the pricer.

Two tabs:
  1. Model       - price an option, read every greek, see the curves and the tree
  2. Walkthrough - the derivation next to the source file that implements it

One design rule worth stating, because it is what keeps this honest: the
walkthrough tab does NOT contain a copy of the code. It reads the real files off
disk and renders them. A walkthrough that quotes a pasted snapshot drifts away
from the thing it claims to explain the first time anyone edits the module, and
then it is actively misleading rather than merely stale.

Run:
    python3 app.py
Then open http://127.0.0.1:8899
"""

from __future__ import annotations

import datetime as dt
import inspect
import math
import pathlib
import traceback

from flask import Flask, jsonify, render_template, request

from pricing import binomial, bsm, market, surface
from pricing.glossary import GREEKS

app = Flask(__name__)
ROOT = pathlib.Path(__file__).parent

# Port 8899 because every other local service on this machine has its own port
# and two things sharing one is how you spend an hour debugging the wrong
# process.
PORT = 8899

DAYS_PER_YEAR = 365.0

# Source files the walkthrough tab is allowed to read. An explicit allowlist
# rather than "any path under ROOT", so a crafted path cannot walk the disk.
SOURCE_FILES = {
    "bsm": "pricing/bsm.py",
    "binomial": "pricing/binomial.py",
    "glossary": "pricing/glossary.py",
    "app": "app.py",
    "test_greeks": "tests/test_greeks.py",
    "test_pricing": "tests/test_pricing.py",
}

# The individual functions the walkthrough is allowed to render. Same reasoning
# as SOURCE_FILES above: name what is exposed rather than exposing a namespace.
SOURCE_SYMBOLS = {
    "bsm": ("norm_cdf", "norm_pdf", "d1_d2", "price", "greeks", "parity_residual",
            "greeks_display"),
    "binomial": ("build_lattice", "price_tree", "tree_delta", "early_exercise_premium",
                 "convergence", "lattice_detail"),
}


def parse_inputs(payload: dict) -> bsm.Inputs:
    """Build Inputs from the form, resolving an expiry date into years if given.

    The assignment asks for an expiry DATE as an input, so the UI offers both:
    pick a date and T is computed on an ACT/365 basis, or type T directly. The
    date is the more realistic interface and T is the more convenient one for
    reproducing a textbook benchmark, so neither is worth dropping.
    """
    expiry = payload.get("expiry")
    if payload.get("use_expiry_date") and expiry:
        today = dt.date.today()
        exp = dt.date.fromisoformat(expiry)
        T = max((exp - today).days, 0) / DAYS_PER_YEAR
    else:
        T = float(payload.get("T", 1.0))

    return bsm.Inputs(
        S=float(payload.get("S", 100.0)),
        K=float(payload.get("K", 100.0)),
        T=T,
        r=float(payload.get("r", 0.05)),
        sigma=float(payload.get("sigma", 0.20)),
        q=float(payload.get("q", 0.0)),
    )


@app.errorhandler(Exception)
def handle_error(exc: Exception):
    """Return the real error rather than a generic 500.

    A pricer that fails silently or with a blank message is worse than one that
    crashes loudly, because the arbitrage assertion in build_lattice is
    SUPPOSED to fire sometimes and its message says exactly what to change.
    Swallowing it would throw away the most useful output in the project.
    """
    app.logger.error("%s\n%s", exc, traceback.format_exc())
    return jsonify({"error": str(exc), "type": type(exc).__name__}), 400


@app.route("/")
def index():
    return render_template("index.html", greeks=GREEKS)


@app.post("/api/price")
def api_price():
    """Everything the model tab shows for one set of inputs."""
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    kind = payload.get("kind", "call")
    steps = int(payload.get("steps", 500))

    raw = bsm.greeks(p, kind)
    closed_form = bsm.price(p, kind)

    result = {
        "inputs": {
            "S": p.S, "K": p.K, "T": p.T, "r": p.r, "sigma": p.sigma, "q": p.q,
            "kind": kind, "steps": steps,
        },
        "closed_form": closed_form,
        "greeks_raw": raw,
        "greeks_display": bsm.greeks_display(raw),
        "parity_residual": bsm.parity_residual(p),
        "parity_relative_residual": bsm.parity_relative_residual(p),
        "call_price": bsm.price(p, "call"),
        "put_price": bsm.price(p, "put"),
        "intrinsic": max(p.S - p.K, 0.0) if kind == "call" else max(p.K - p.S, 0.0),
    }

    # The lattice can legitimately refuse. Report that as a first-class result
    # rather than letting it take down the whole response, so the closed-form
    # numbers above still render.
    try:
        lat = binomial.build_lattice(p, steps)
        euro = binomial.price_tree(p, kind, steps, american=False)
        amer = binomial.price_tree(p, kind, steps, american=True)
        result["tree"] = {
            "european": euro,
            "american": amer,
            "early_exercise_premium": amer - euro,
            "tree_vs_closed_form": euro - closed_form,
            "params": {
                "dt": lat.dt, "u": lat.u, "d": lat.d,
                "growth": lat.growth, "qp": lat.qp, "disc": lat.disc,
            },
            "no_arbitrage_holds": lat.d < lat.growth < lat.u,
        }
    except binomial.ArbitrageViolation as exc:
        result["tree"] = {"error": str(exc)}

    return jsonify(result)


@app.post("/api/curves")
def api_curves():
    """Greek curves against spot, at several times to expiry.

    This is the plot where the intuition actually lands: gamma spiking at the
    money as expiry approaches is something you have to see once to remember.
    """
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    kind = payload.get("kind", "call")

    expiries = [t for t in (2.0, 1.0, 0.5, 0.25, 1 / 12, 1 / 52) if t <= max(p.T, 2.0)]
    if p.T > 0 and p.T not in expiries:
        expiries.append(p.T)
    expiries = sorted(set(expiries), reverse=True)

    lo, hi = p.K * 0.4, p.K * 1.8
    n = 181
    spots = [lo + (hi - lo) * i / (n - 1) for i in range(n)]

    series = []
    for T in expiries:
        rows = {g["key"]: [] for g in GREEKS}
        rows["price"] = []
        for S in spots:
            q = bsm.Inputs(S=S, K=p.K, T=T, r=p.r, sigma=p.sigma, q=p.q)
            rows["price"].append(bsm.price(q, kind))
            g = bsm.greeks(q, kind)
            for key in g:
                rows[key].append(g[key])
        series.append({"T": T, "label": _label_expiry(T), "rows": rows})

    return jsonify({"spots": spots, "strike": p.K, "series": series})


def _label_expiry(T: float) -> str:
    days = T * DAYS_PER_YEAR
    if days < 10:
        return f"{days:.0f}d"
    if days < 60:
        return f"{days / 7:.0f}w"
    if days < 400:
        return f"{days / 30.4:.0f}m"
    return f"{T:.1f}y"


@app.post("/api/convergence")
def api_convergence():
    """Tree price against step count, plus the closed-form value to compare to.

    Expect a damped zigzag. The error is roughly O(1/N) but its SIGN depends on
    where the strike falls relative to the terminal nodes, and that alternates
    as N changes. A smooth curve here means something is wrong.
    """
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    kind = payload.get("kind", "call")
    american = bool(payload.get("american", False))
    max_steps = int(payload.get("max_steps", 150))

    pts = binomial.convergence(p, kind, max_steps=max_steps, american=american)
    return jsonify({
        "points": [{"n": n, "price": v} for n, v in pts],
        "closed_form": bsm.price(p, kind),
        "american": american,
    })


@app.post("/api/implied")
def api_implied():
    """Solve for the volatility that reproduces an observed market price.

    This is the replacement for the option-chain browser, and it is a better
    tool for the job. The chain pulled last-traded prices off a free feed,
    which are stale on any thinly traded strike, so the volatilities it
    produced in the wings were noise that had to be caveated on screen. Typing
    in a mark from a real terminal removes the weakest link: the price comes
    from wherever the price is good, and this end does the part that is
    actually ours.
    """
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    kind = payload.get("kind", "call")
    try:
        target = float(payload.get("market_price"))
    except (TypeError, ValueError):
        return jsonify({"error": "no market price given"}), 400

    try:
        sigma, uncertainty = bsm.implied_vol_with_uncertainty(
            target, p.S, p.K, p.T, p.r, kind, q=p.q
        )
    except bsm.NoImpliedVol as exc:
        return jsonify({"error": str(exc)}), 422

    return jsonify({
        "implied_vol": sigma,
        "uncertainty": uncertainty,
        "reprices_to": bsm.price(
            bsm.Inputs(S=p.S, K=p.K, T=p.T, r=p.r, sigma=sigma, q=p.q), kind
        ),
        "market_price": target,
    })


@app.post("/api/lattice")
def api_lattice():
    """A small tree, node by node, with the exercise boundary marked."""
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    kind = payload.get("kind", "call")
    steps = min(int(payload.get("steps", 5)), 12)
    american = bool(payload.get("american", True))
    return jsonify(binomial.lattice_detail(p, kind, steps, american))


@app.get("/api/source/<name>")
def api_source(name: str):
    """Serve a source file, read from disk at request time.

    Deliberately not a stored copy. See the module docstring.
    """
    rel = SOURCE_FILES.get(name)
    if rel is None:
        return jsonify({"error": f"unknown source {name!r}"}), 404
    path = ROOT / rel
    return jsonify({
        "name": name,
        "path": rel,
        "code": path.read_text(),
        "lines": len(path.read_text().splitlines()),
    })


@app.get("/api/quote/<ticker>")
def api_quote(ticker: str):
    """Live spot, realized volatility and dividend yield for a ticker.

    The ONLY endpoint that touches the network, and the only one allowed to
    fail without taking anything else with it. A failure here returns 503 with
    the reason, and the UI keeps whatever is already in the form, because the
    pricer never needed this to work in the first place.
    """
    try:
        return jsonify(market.fetch_quote(ticker).to_dict())
    except market.MarketDataUnavailable as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/sample-grid")
def api_sample_grid():
    """A worked example grid, priced off the contract currently loaded.

    Hand-written sample numbers do not survive a change of spot: quotes that
    look reasonable against a 100 underlying fall below intrinsic against a 770
    one, and half the grid then refuses to invert. Generating it from the model
    means the sample always parses, and it carries a deliberate skew so the
    picture it draws is the one the section is about.
    """
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    kind = payload.get("kind", "call")

    expiries = [0.08, 0.25, 0.5, 1.0]
    strikes = [round(p.S * m / 5) * 5 for m in (0.88, 0.94, 1.0, 1.06, 1.12)]

    lines = ["\t" + "\t".join(f"{t:g}" for t in expiries)]
    for K in strikes:
        cells = []
        for T in expiries:
            # A downward skew in strike, decaying with maturity, which is the
            # shape equity surfaces actually have.
            moneyness = math.log(K / p.S)
            sigma = 0.20 - 0.35 * moneyness / (1.0 + 2.0 * T) + 0.02 * T
            sigma = max(0.05, sigma)
            cells.append(f"{bsm.price(bsm.Inputs(S=p.S, K=K, T=T, r=p.r, sigma=sigma, q=p.q), kind):.4f}")
        lines.append(f"{K:g}\t" + "\t".join(cells))

    return jsonify({"grid": "\n".join(lines)})


@app.post("/api/surface")
def api_surface():
    """Build a volatility surface from a pasted grid of quotes.

    Deliberately not fed from a data feed. Marks come from wherever the marks
    are good, a terminal or a spreadsheet, and this end does the inversion.
    That split is the whole design: the weak link in the old chain browser was
    the free feed's stale last-traded prices, not the maths.
    """
    payload = request.get_json(force=True)
    p = parse_inputs(payload)
    try:
        return jsonify(surface.build_surface(
            payload.get("grid", ""),
            S=p.S, r=p.r, q=p.q,
            kind=payload.get("kind", "call"),
            cells_are=payload.get("cells_are", "price"),
        ))
    except surface.SurfaceError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/symbol/<module>/<symbol>")
def api_symbol(module: str, symbol: str):
    """Serve ONE function's source, pulled live with inspect.getsource.

    This is the strongest form of the no-copies rule: the walkthrough asks the
    running interpreter for the source of the exact object it just called, so
    what is rendered on the page is by construction the code that produced the
    numbers on the other tab. There is no path by which the two can disagree.
    """
    mods = {"bsm": bsm, "binomial": binomial}
    mod = mods.get(module)
    if mod is None:
        return jsonify({"error": f"unknown module {module!r}"}), 404

    # Allowlist, not a bare getattr. A module's namespace contains everything it
    # imported, so getattr(bsm, "dataclass") happily returns inspect.getsource
    # of the stdlib file that defines it, which is neither in this repo nor
    # something the walkthrough ever asks for. Localhost-only binding makes the
    # real-world risk minimal, but the docstring above claims this serves "the
    # source of the exact object it just called", and that claim should be true
    # rather than nearly true.
    if symbol not in SOURCE_SYMBOLS.get(module, ()):
        return jsonify({"error": f"{module}.{symbol} is not an exposed symbol"}), 404

    obj = getattr(mod, symbol, None)
    if obj is None:
        return jsonify({"error": f"{module}.{symbol} does not exist"}), 404

    src = inspect.getsource(obj)
    _, start = inspect.getsourcelines(obj)
    return jsonify({
        "module": module,
        "symbol": symbol,
        "code": src,
        "path": f"pricing/{module}.py",
        "start_line": start,
    })


@app.get("/api/verify")
def api_verify():
    """Run the headline checks live and report pass or fail with the numbers.

    This is the panel that answers "how do you know it is right." Every row is
    computed on the spot from the same functions the model tab uses, so it
    cannot drift from what the pricer actually does. The targets are the values
    published with the assignment.
    """
    b = bsm.Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20)
    rows = []

    def check(label: str, got: float, target: float, tol: float, note: str) -> None:
        rows.append({
            "label": label, "got": got, "target": target,
            "tolerance": tol, "passed": abs(got - target) <= tol, "note": note,
        })

    check("European call, closed form", bsm.price(b, "call"), 10.4506, 5e-5,
          "Published reference value.")
    check("European put, closed form", bsm.price(b, "put"), 5.5735, 5e-5,
          "Published reference value.")
    check("European call, 2000-step tree", binomial.price_tree(b, "call", 2000), 10.4496, 5e-4,
          "The lattice converging on the closed form.")
    check("American put, 2000-step tree", binomial.price_tree(b, "put", 2000, True), 6.09, 0.01,
          "Published reference value.")
    check("Early-exercise premium, put", binomial.early_exercise_premium(b, "put", 2000), 0.52, 0.01,
          "The value of being allowed to exercise early. Differenced on one lattice so the "
          "discretisation error cancels.")
    check("Put-call parity residual", bsm.parity_residual(b), 0.0, 1e-12,
          "C - P - (S·e^(-qT) - K·e^(-rT)). A pure no-arbitrage identity with no model in it. "
          "A non-zero residual means a sign error.")
    check("American call minus European call", binomial.early_exercise_premium(b, "call", 2000), 0.0, 1e-12,
          "Merton: an American call on a non-dividend stock is never exercised early, so this "
          "must be exactly zero, not approximately.")

    div = bsm.Inputs(S=100, K=100, T=1.0, r=0.05, sigma=0.20, q=0.08)
    amer_call_div = binomial.early_exercise_premium(div, "call", 800)
    rows.append({
        "label": "Control: American call premium with an 8% dividend",
        "got": amer_call_div, "target": None, "tolerance": None,
        "passed": amer_call_div > 0.0,
        "note": "The positive control on the row above. A zero there only means something if "
                "the same routine CAN find early exercise when it exists. It can.",
    })

    # The four sanity checks, as booleans.
    deep_itm = bsm.price(bsm.Inputs(S=1000, K=100, T=1.0, r=0.05, sigma=0.20), "call")
    fwd_intrinsic = 1000 - 100 * pow(2.718281828459045, -0.05)
    rows.append({
        "label": "Sanity: deep in the money goes to intrinsic",
        "got": deep_itm, "target": fwd_intrinsic, "tolerance": 1e-3,
        "passed": abs(deep_itm - fwd_intrinsic) < 1e-3,
        "note": "All optionality worthless, only the discounted forward remains.",
    })
    deep_otm = bsm.price(bsm.Inputs(S=1, K=100, T=1.0, r=0.05, sigma=0.20), "call")
    rows.append({
        "label": "Sanity: deep out of the money goes to zero",
        "got": deep_otm, "target": 0.0, "tolerance": 1e-8,
        "passed": deep_otm < 1e-8, "note": "Nothing left to be worth anything.",
    })

    vols = [0.05, 0.1, 0.2, 0.4, 0.8]
    monotone = True
    for kind in ("call", "put"):
        vals = [bsm.price(bsm.Inputs(S=100, K=100, T=1.0, r=0.05, sigma=v), kind) for v in vols]
        monotone = monotone and all(b2 > a for a, b2 in zip(vals, vals[1:]))
    rows.append({
        "label": "Sanity: higher volatility always raises the price",
        "got": None, "target": None, "tolerance": None, "passed": monotone,
        "note": "Calls AND puts. The payoff is convex, so by Jensen more randomness is strictly "
                "worth more to the holder, with no view on direction anywhere in the argument.",
    })

    zero_r = binomial.early_exercise_premium(
        bsm.Inputs(S=100, K=100, T=1.0, r=0.0, sigma=0.20), "put", 800)
    rows.append({
        "label": "Sanity: r = 0 collapses the put's early-exercise edge",
        "got": zero_r, "target": 0.0, "tolerance": 1e-10, "passed": abs(zero_r) < 1e-10,
        "note": "The whole benefit was interest on the strike collected early. No rate, no benefit.",
    })

    return jsonify({"rows": rows, "all_passed": all(r["passed"] for r in rows)})


if __name__ == "__main__":
    print(f"\n  Options pricer running at http://127.0.0.1:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, debug=True)
