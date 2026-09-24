"""Parsing a pasted grid and inverting it into a volatility surface."""

from __future__ import annotations

import datetime

import pytest

from pricing.bsm import Inputs, price
from pricing.surface import SurfaceError, build_surface, parse_expiry, parse_grid

TODAY = datetime.date(2026, 9, 23)

TAB_GRID = "\t0.25\t0.5\n950\t12.40\t21.00\n1000\t24.20\t35.50\n"


def priced_grid(S=100.0, r=0.05, q=0.0, kind="call", sigmas=None):
    """A grid built from KNOWN volatilities, so the inversion has a right answer."""
    strikes = [90.0, 100.0, 110.0]
    expiries = [0.25, 1.0]
    sigmas = sigmas or {(90.0, 0.25): 0.32, (90.0, 1.0): 0.30,
                        (100.0, 0.25): 0.25, (100.0, 1.0): 0.26,
                        (110.0, 0.25): 0.22, (110.0, 1.0): 0.24}
    header = "strike," + ",".join(str(e) for e in expiries)
    lines = [header]
    for k in strikes:
        cells = [
            f"{price(Inputs(S=S, K=k, T=t, r=r, sigma=sigmas[(k, t)], q=q), kind):.10f}"
            for t in expiries
        ]
        lines.append(f"{k}," + ",".join(cells))
    return "\n".join(lines), sigmas


# ------------------------------------------------------------------ parsing


def test_tab_separated_paste_from_a_terminal_parses() -> None:
    labels, years, rows = parse_grid(TAB_GRID, TODAY)
    assert labels == ["0.25", "0.5"]
    assert years == pytest.approx([0.25, 0.5])
    assert [r[0] for r in rows] == [950.0, 1000.0]
    assert rows[0][1] == pytest.approx([12.40, 21.00])


def test_comma_and_whitespace_separators_parse_the_same() -> None:
    comma = "x,0.25,0.5\n950,12.40,21.00\n1000,24.20,35.50"
    spaces = "x   0.25   0.5\n950  12.40  21.00\n1000 24.20  35.50"
    a = parse_grid(comma, TODAY)
    b = parse_grid(spaces, TODAY)
    assert a[0] == b[0] and a[2] == b[2]


def test_a_header_without_a_corner_label_still_parses() -> None:
    """Some pastes carry a top-left label, some do not. Both are normal."""
    with_corner = "strike,0.25,0.5\n950,12.40,21.00"
    without = "0.25,0.5\n950,12.40,21.00"
    assert parse_grid(with_corner, TODAY)[0] == parse_grid(without, TODAY)[0] == ["0.25", "0.5"]


def test_expiries_may_be_dates_or_year_fractions() -> None:
    label, T = parse_expiry("2027-09-23", TODAY)
    assert label == "2027-09-23"
    assert T == pytest.approx(1.0)
    assert parse_expiry("0.25", TODAY)[1] == pytest.approx(0.25)


def test_a_past_expiry_is_refused() -> None:
    with pytest.raises(SurfaceError, match="not in the future"):
        parse_expiry("2020-01-17", TODAY)


@pytest.mark.parametrize("bad,msg", [
    ("", "header row"),
    ("0.25,0.5", "at least one strike"),
    ("x,next friday\n100,5.0", "cannot read"),
    ("x,0.25\nnot-a-strike,5.0", "cannot read"),
    ("x,0.25\n100,abc", "cannot read"),
])
def test_unreadable_grids_say_what_is_wrong(bad: str, msg: str) -> None:
    """The error names the offending cell. A bare 'invalid input' is useless
    against a forty-cell grid."""
    with pytest.raises(SurfaceError, match=msg):
        parse_grid(bad, TODAY)


def test_a_blank_cell_is_a_hole_not_a_zero() -> None:
    """Real grids are ragged: not every strike trades at every expiry.

    A missing quote recorded as 0.0 would invert to a garbage volatility and
    then be drawn as a real point on the curve.
    """
    labels, years, rows = parse_grid("x,0.25,0.5\n950,12.40,\n1000,,35.50", TODAY)
    assert rows[0][1] == [12.40, None]
    assert rows[1][1] == [None, 35.50]


# ---------------------------------------------------------------- inverting


def test_every_cell_recovers_the_volatility_it_was_priced_with() -> None:
    text, sigmas = priced_grid()
    out = build_surface(text, S=100.0, r=0.05, q=0.0, kind="call", as_of=TODAY)

    assert out["failed"] == 0
    assert out["inverted"] == 6
    for pt in out["points"]:
        expected = sigmas[(pt["strike"], round(pt["T"], 10))]
        assert pt["implied_vol"] == pytest.approx(expected, abs=1e-6), pt


def test_the_skew_is_visible_in_the_output() -> None:
    """The whole reason to draw this: implied vol should fall as strike rises."""
    text, _ = priced_grid()
    out = build_surface(text, S=100.0, r=0.05, q=0.0, kind="call", as_of=TODAY)
    near = sorted(
        [p for p in out["points"] if p["expiry"] == "0.25"], key=lambda p: p["strike"]
    )
    vols = [p["implied_vol"] for p in near]
    assert all(b < a for a, b in zip(vols, vols[1:])), vols


def test_a_cell_that_cannot_be_inverted_keeps_its_reason_and_the_rest_survive() -> None:
    """One impossible quote must not take the whole surface down."""
    text = "x,0.25\n90,0.01\n100,8.0\n110,4.0"
    out = build_surface(text, S=100.0, r=0.05, q=0.0, kind="call", as_of=TODAY)
    bad = [p for p in out["points"] if p["strike"] == 90.0][0]
    assert bad["implied_vol"] is None
    assert "no-arbitrage floor" in bad["error"]
    assert out["failed"] == 1
    assert out["inverted"] == 2


# ------------------------------------------------------- prices versus vols


def test_a_grid_of_vols_is_taken_as_given_not_inverted() -> None:
    out = build_surface("x,0.25\n100,0.24", S=100.0, r=0.05, q=0.0,
                        kind="call", cells_are="vol", as_of=TODAY)
    assert out["points"][0]["implied_vol"] == pytest.approx(0.24)


def test_percent_and_decimal_vol_conventions_both_land_on_the_same_number() -> None:
    """A terminal quotes 24, a textbook quotes 0.24. Both mean 24 percent.

    Reading one as the other is an error with no visible symptom: the surface
    still draws, it just means nothing.
    """
    as_pct = build_surface("x,0.25\n100,24", S=100.0, r=0.05, q=0.0,
                           kind="call", cells_are="vol", as_of=TODAY)
    as_dec = build_surface("x,0.25\n100,0.24", S=100.0, r=0.05, q=0.0,
                           kind="call", cells_are="vol", as_of=TODAY)
    assert as_pct["points"][0]["implied_vol"] == pytest.approx(0.24)
    assert as_dec["points"][0]["implied_vol"] == pytest.approx(0.24)


def test_reading_prices_as_vols_produces_a_visibly_different_surface() -> None:
    """Control on the distinction above: the two modes must not agree.

    If they did, the cells_are flag would be decorative and a grid of prices
    read as vols would pass unnoticed.
    """
    text, _ = priced_grid()
    as_price = build_surface(text, S=100.0, r=0.05, q=0.0, kind="call", as_of=TODAY)
    as_vol = build_surface(text, S=100.0, r=0.05, q=0.0, kind="call", cells_are="vol", as_of=TODAY)
    a = [p["implied_vol"] for p in as_price["points"]]
    b = [p["implied_vol"] for p in as_vol["points"]]
    assert a != pytest.approx(b)


def test_an_unknown_cells_are_value_is_refused() -> None:
    with pytest.raises(SurfaceError, match="must be 'price' or 'vol'"):
        build_surface(TAB_GRID, S=100.0, r=0.05, q=0.0, kind="call", cells_are="iv")
