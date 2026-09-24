"""Build a volatility surface from a grid of market quotes.

Nothing here touches the network, and that is the point. Bloomberg (or any
terminal worth quoting) supplies the marks; this module does the part that is
ours, which is inverting each one into the volatility that reproduces it and
arranging the results so the shape is visible.

A surface is implied volatility as a function of two things, strike and time to
expiry. Two readings matter and they are different questions:

    ACROSS STRIKE, at one expiry: the SMILE, or for equities the SKEW. Implied
    vol falls as strike rises, because the market charges more for downside
    protection than for upside. Black-Scholes cannot express this at all: it
    has one sigma per underlying, not one per strike. The skew is the market
    pricing the model's own wrongness, and it has been there continuously since
    October 1987.

    ACROSS EXPIRY, at one strike: the TERM STRUCTURE. Usually upward sloping in
    calm markets and inverted when something is about to happen, because a
    known event sits inside the near expiry and not the far one.

The grid is parsed from text on purpose. A terminal or a spreadsheet copies as
tab-separated text, so pasting is the natural motion and there is no bespoke
grid editor to build or maintain.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, asdict

CALENDAR_DAYS = 365.0


class SurfaceError(ValueError):
    """The grid could not be read. Always carries what was wrong with it."""


@dataclass(frozen=True)
class SurfacePoint:
    strike: float
    expiry: str          # as the user wrote it
    T: float             # years
    quote: float         # the number in the cell
    implied_vol: float | None
    uncertainty: float | None
    error: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_expiry(token: str, as_of: datetime.date) -> tuple[str, float]:
    """Read an expiry as either a year fraction or a calendar date.

    Both appear in practice: a textbook gives 0.25, a terminal gives
    2026-12-18. Accepting only one of them would force hand-conversion, which
    is exactly the sort of step where a decimal goes missing.
    """
    token = token.strip()
    if not token:
        raise SurfaceError("blank expiry in the header row")

    try:
        exp = datetime.date.fromisoformat(token)
    except ValueError:
        pass
    else:
        days = (exp - as_of).days
        if days <= 0:
            raise SurfaceError(f"expiry {token} is not in the future")
        return token, days / CALENDAR_DAYS

    try:
        years = float(token)
    except ValueError as exc:
        raise SurfaceError(
            f"cannot read {token!r} as an expiry; use a year fraction like 0.25 "
            f"or a date like 2026-12-18"
        ) from exc
    if years <= 0:
        raise SurfaceError(f"expiry {token} must be positive")
    return token, years


def parse_grid(text: str, as_of: datetime.date | None = None):
    """Parse a pasted grid into (expiry labels, years, rows of (strike, quotes)).

    Expected shape, with the top-left cell ignored so a pasted block with a
    corner label still works:

        .        0.08    0.25    0.5
        950      12.40   21.00   30.10
        1000     24.20   35.50   48.00

    Separators are tabs, commas or runs of spaces, so a paste from a terminal,
    a spreadsheet or a plain text file all land the same way.

    A blank cell is allowed and means "no quote at this strike and expiry",
    which is normal: real grids are ragged because not every strike trades at
    every expiry. It is recorded as a hole rather than guessed at.
    """
    as_of = as_of or datetime.date.today()

    lines = [ln for ln in (l.rstrip() for l in text.strip().splitlines()) if ln.strip()]
    if len(lines) < 2:
        raise SurfaceError("need a header row of expiries and at least one strike row")

    def split(line: str) -> list[str]:
        if "\t" in line:
            return [c.strip() for c in line.split("\t")]
        if "," in line:
            return [c.strip() for c in line.split(",")]
        return line.split()

    header = split(lines[0])
    if len(header) < 2:
        raise SurfaceError("header row needs at least one expiry")

    # Drop a top-left corner label if there is one. A header with the same cell
    # count as the data rows has a corner; one cell shorter does not.
    body = [split(ln) for ln in lines[1:]]
    widest = max(len(r) for r in body)
    header_cells = header[1:] if len(header) == widest else header

    labels, years = [], []
    for token in header_cells:
        label, T = parse_expiry(token, as_of)
        labels.append(label)
        years.append(T)

    rows = []
    for raw in body:
        try:
            strike = float(raw[0])
        except (ValueError, IndexError) as exc:
            raise SurfaceError(f"cannot read {raw[0]!r} as a strike") from exc

        quotes: list[float | None] = []
        for cell in raw[1 : len(labels) + 1]:
            if cell in ("", "-", "."):
                quotes.append(None)
                continue
            try:
                quotes.append(float(cell))
            except ValueError as exc:
                raise SurfaceError(
                    f"cannot read {cell!r} as a number, at strike {strike:g}"
                ) from exc
        quotes += [None] * (len(labels) - len(quotes))
        rows.append((strike, quotes))

    if not rows:
        raise SurfaceError("no strike rows found")
    return labels, years, rows


def build_surface(
    text: str,
    S: float,
    r: float,
    q: float,
    kind: str,
    cells_are: str = "price",
    as_of: datetime.date | None = None,
) -> dict:
    """Turn a pasted grid into a surface.

    `cells_are` is "price" when the grid holds option prices, which get
    inverted here, or "vol" when it already holds implied volatilities, which
    are taken as given.

    Both are worth supporting and the distinction is not cosmetic. A terminal
    will quote you either, and silently treating one as the other is a error
    with no visible symptom: a grid of 0.24-style vols read as prices produces
    a surface that looks plausible and means nothing.
    """
    from pricing.bsm import NoImpliedVol, implied_vol_with_uncertainty

    if cells_are not in ("price", "vol"):
        raise SurfaceError(f"cells_are must be 'price' or 'vol', got {cells_are!r}")

    labels, years, rows = parse_grid(text, as_of)

    points: list[SurfacePoint] = []
    for strike, quotes in rows:
        for label, T, quote in zip(labels, years, quotes):
            if quote is None:
                continue

            iv: float | None = None
            unc: float | None = None
            err: str | None = None

            if cells_are == "vol":
                # A vol quoted as 24 means 24 percent, not 2400 percent. Accept
                # both conventions rather than making the reader normalise, and
                # pick on magnitude: no equity option implies 150 in decimal.
                iv = quote / 100.0 if quote > 1.5 else quote
                unc = 0.0
            else:
                try:
                    iv, unc = implied_vol_with_uncertainty(
                        quote, S, strike, T, r, kind, q=q
                    )
                except NoImpliedVol as exc:
                    err = str(exc)

            points.append(
                SurfacePoint(
                    strike=strike, expiry=label, T=T, quote=quote,
                    implied_vol=iv, uncertainty=unc, error=err,
                )
            )

    if not points:
        raise SurfaceError("grid parsed, but every cell was empty")

    return {
        "expiries": labels,
        "years": years,
        "strikes": sorted({p.strike for p in points}),
        "spot": S,
        "kind": kind,
        "cells_are": cells_are,
        "points": [p.to_dict() for p in points],
        "inverted": sum(1 for p in points if p.implied_vol is not None),
        "failed": sum(1 for p in points if p.implied_vol is None),
    }
