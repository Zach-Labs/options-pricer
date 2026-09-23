"""Plain-English explanations of every greek, served to the UI.

Kept out of bsm.py on purpose: bsm.py is the math and should read as math.
This is the teaching layer, and it is the single source for the greek copy so
the model tab and the walkthrough tab can never drift apart on what a greek
means.

Fields per greek:
    symbol   the conventional notation
    partial  which derivative of V it actually is
    formula  the closed form, in the same notation as bsm.py
    units    what one unit of the raw number means
    display  how the UI rescales it, and why
    meaning  what it measures, in one sentence
    desk     why anyone on a desk cares
"""

from __future__ import annotations

GREEKS: list[dict[str, str]] = [
    {
        "key": "delta",
        "name": "Delta",
        "symbol": "Δ",
        "partial": "∂V/∂S",
        "formula": "call: e^(-qT)·Φ(d₁)   put: -e^(-qT)·Φ(-d₁)",
        "units": "change in option value per $1 change in the stock",
        "display": "shown raw",
        "scale": "",
        "meaning": (
            "How much the option's value moves when the stock moves by a dollar. "
            "Runs from 0 to 1 for a call and -1 to 0 for a put."
        ),
        "desk": (
            "It is the hedge ratio, and that is not a second fact about it, it is the "
            "same fact. Holding Δ shares against a short option is exactly what made "
            "the random term cancel in the derivation, which is why mu never reached "
            "the pricing equation. Traders also read delta as roughly the risk-neutral "
            "probability of finishing in the money, and use it as a coordinate: the "
            "\"25-delta put\" names a location on the surface, not a strike."
        ),
    },
    {
        "key": "gamma",
        "name": "Gamma",
        "symbol": "Γ",
        "partial": "∂²V/∂S²",
        "formula": "e^(-qT)·φ(d₁) / (S·σ·√T)",
        "units": "change in delta per $1 change in the stock",
        "display": "shown raw",
        "scale": "",
        "meaning": (
            "How fast delta itself changes. It is the curvature of the value surface, "
            "and it is the term that survived the Ito expansion."
        ),
        "desk": (
            "Curvature is the product. Long options means long gamma: as the stock "
            "rises your delta rises, so a static hedge is suddenly short too many "
            "shares and rebalancing means buying back lower than you sold. You have "
            "mechanically bought low and sold high, in either direction. Gamma is "
            "largest at the money and near expiry, because it is the smoothed version "
            "of the kink in the payoff."
        ),
    },
    {
        "key": "vega",
        "name": "Vega",
        "symbol": "ν",
        "partial": "∂V/∂σ",
        "formula": "S·e^(-qT)·φ(d₁)·√T",
        "units": "change in option value per 1.00 (100 vol points) change in sigma",
        "display": "divided by 100, so the number shown is per 1 vol point",
        "scale": "÷100 per vol pt",
        "meaning": "How much the option is worth for each extra point of implied volatility.",
        "desk": (
            "Once you quote in vol rather than dollars, vega is your actual inventory: "
            "a book is long or short volatility the way a shop is long or short stock. "
            "Vega is strictly positive for a European option, which is what makes "
            "implied volatility well defined, since a strictly increasing function has "
            "a unique inverse. Largest for at-the-money, long-dated options, which have "
            "the most variance left to accumulate. Not actually a Greek letter."
        ),
    },
    {
        "key": "theta",
        "name": "Theta",
        "symbol": "Θ",
        "partial": "∂V/∂t",
        "formula": "-S·e^(-qT)·φ(d₁)·σ/(2√T) ∓ r·K·e^(-rT)·Φ(±d₂) ± q·S·e^(-qT)·Φ(±d₁)",
        "units": "change in option value per year of time passing",
        "display": "divided by 365, so the number shown is per calendar day",
        "scale": "÷365 per day",
        "meaning": "Time decay. Normally negative for a long option: one day less for the kink to pay off.",
        "desk": (
            "Theta is the rent you pay for gamma, and the pricing equation ties the two "
            "together exactly: Θ + ½σ²S²Γ = rV - rSΔ. On a delta-hedged book they are "
            "two sides of one trade. The P&L of a hedged long option over a short "
            "interval is about ½·Γ·S²·[(realised move)² - σ²_implied·dt], so you make "
            "money when realised volatility beats the implied volatility you paid for. "
            "That is the entire gamma-scalping business in one line."
        ),
    },
    {
        "key": "rho",
        "name": "Rho",
        "symbol": "ρ",
        "partial": "∂V/∂r",
        "formula": "call: K·T·e^(-rT)·Φ(d₂)   put: -K·T·e^(-rT)·Φ(-d₂)",
        "units": "change in option value per 1.00 (100 percentage points) change in r",
        "display": "divided by 100, so the number shown is per 1 percentage point",
        "scale": "÷100 per 1% r",
        "meaning": "Sensitivity to the risk-free rate.",
        "desk": (
            "Usually minor for short-dated equity options, and it is the greek people "
            "skip. It matters in two places: on Fed days, and as the reason an American "
            "put gets exercised early. Exercising hands you the strike in cash now, and "
            "the interest on that cash is the entire early-exercise benefit, which is "
            "why the premium vanishes as r goes to zero."
        ),
    },
    {
        "key": "vanna",
        "name": "Vanna",
        "symbol": "",
        "partial": "∂²V/∂S∂σ",
        "formula": "-e^(-qT)·φ(d₁)·d₂/σ",
        "units": "change in delta per 1.00 change in sigma",
        "display": "shown raw",
        "scale": "",
        "meaning": "How your delta changes when implied volatility moves.",
        "desk": (
            "The direct link to the skew. If volatility is correlated with spot, and "
            "since 1987 in index options it emphatically is, then a delta hedge set "
            "without vanna is wrong the moment the market moves, because the vol moved "
            "too. Identical for calls and puts."
        ),
    },
    {
        "key": "volga",
        "name": "Volga",
        "symbol": "",
        "partial": "∂²V/∂σ²",
        "formula": "vega·d₁·d₂/σ",
        "units": "change in vega per 1.00 change in sigma",
        "display": "shown raw",
        "scale": "",
        "meaning": "Curvature in volatility: how fast vega itself changes as vol moves.",
        "desk": (
            "Your exposure to vol-of-vol. Near zero at the money and positive in the "
            "wings, which is why out-of-the-money options are the ones that benefit "
            "when the whole volatility surface gets violent. Identical for calls and "
            "puts, for the same reason as gamma and vega: put-call parity is linear in "
            "S and free of sigma, so every second derivative of it is zero."
        ),
    },
    {
        "key": "charm",
        "name": "Charm",
        "symbol": "",
        "partial": "∂²V/∂S∂t",
        "formula": "±q·e^(-qT)·Φ(±d₁) - e^(-qT)·φ(d₁)·[2(r-q)T - d₂σ√T] / (2Tσ√T)",
        "units": "change in delta per year of time passing",
        "display": "divided by 365, so the number shown is delta drift per calendar day",
        "scale": "÷365 per day",
        "meaning": "How delta decays purely from time passing, with the stock going nowhere.",
        "desk": (
            "Also called delta decay. It matters into expiry and around large open "
            "interest: a hedge that was correct on Friday is wrong on Monday even if "
            "nothing traded, because every option's delta drifted toward 0 or 1 over "
            "the weekend. Desks that rehedge on a schedule rather than continuously "
            "watch this one closely."
        ),
    },
]

GREEKS_BY_KEY = {g["key"]: g for g in GREEKS}
