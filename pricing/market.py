"""Live market data for the ticker field. The ONLY networked part of the project.

Deliberately quarantined in its own module and behind its own endpoint, because
everything else here is pure arithmetic that works offline and always will. If
this file stops working, the pricer does not: the UI falls back to typing the
spot in by hand, which is how it worked before.

Three things it returns, and the honesty notes on each matter more than the code:

1. SPOT. Yahoo's last price, via yfinance. It can be delayed, and it is a single
   source, so the as-of timestamp is returned with it and shown in the UI rather
   than presenting it as a live feed.

2. REALIZED VOLATILITY. The standard deviation of daily log returns, annualised
   by sqrt(252). Offered as a convenience for pre-filling sigma, and labelled
   loudly as realized rather than implied, because THOSE ARE NOT THE SAME THING
   and the difference is most of what the course is about. BSM's sigma is the
   market's implied volatility, backed out of an option's traded price. What is
   computed here is what the stock actually did. Using realized as a stand-in is
   a modelling assumption, not a data lookup, and the UI says so.

3. DIVIDEND YIELD. Derived from the dividends actually paid over the trailing
   year, divided by spot. NOT taken from yfinance's `dividendYield` field, which
   is reported in PERCENT (0.32 means 0.32%). Feeding that straight into q as a
   decimal would be a 100x error that silently wrecks every price, and it would
   look like a modelling problem rather than a units bug. Measured 2026-09-22:
   the field said 0.32 for AAPL while the actual trailing payments give 0.389%,
   so it is both differently scaled AND a different number. Summing the real
   payments is unambiguous, auditable, and correctly returns zero for a
   non-payer such as BRK-B.
"""

from __future__ import annotations

import datetime
import math
import os
import time
from dataclasses import dataclass, asdict

# Two different day counts, deliberately, because they measure different things
# and using one for both is a classic quiet error:
#   TRADING_DAYS annualises a volatility, and volatility only accumulates on
#   days the market is open.
#   CALENDAR_DAYS converts a date into a time to expiry, and an option decays
#   over weekends too.
CALENDAR_DAYS = 365.0
TRADING_DAYS = 252
# Fewer returns than this and a volatility estimate is meaningless, not merely
# imprecise, so the honest answer is None rather than a number.
MIN_RETURNS = 19
CACHE_TTL_SECONDS = 60.0

_cache: dict[str, tuple[float, "Quote"]] = {}


class MarketDataUnavailable(RuntimeError):
    """Raised when a quote cannot be fetched. Never fatal to the pricer."""


@dataclass(frozen=True)
class Quote:
    ticker: str
    spot: float
    currency: str
    as_of: str
    realized_vol_1y: float | None
    realized_vol_30d: float | None
    dividend_yield: float
    trailing_dividends: float
    bars_used: int
    source: str = "Yahoo Finance via yfinance"

    def to_dict(self) -> dict:
        return asdict(self)


def _guard_against_tests() -> None:
    """Refuse to touch the network from inside a test run.

    Isolation belongs at the RESOURCE, not in each test file. A test that
    reaches a real service reaches it for real, and a suite that quietly depends
    on Yahoo being up is a suite that fails for reasons that have nothing to do
    with the code. Tests inject a fake fetcher instead; see test_market.py.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise MarketDataUnavailable(
            "refusing to fetch live market data from inside a test; "
            "inject a fake fetcher instead"
        )


def _annualised_vol(closes: list[float]) -> float | None:
    """Standard deviation of daily log returns, annualised by sqrt(252).

    Returns None rather than a number when there is not enough data. A
    volatility computed from three observations is not a small estimate, it is
    a meaningless one, and handing it back as though it were a measurement is
    how a plausible-looking wrong number gets into a price.

    Non-finite closes are dropped HERE rather than relying on the caller having
    done it. NaN is contagious and raises nothing: one NaN close makes the
    standard deviation NaN, which becomes sigma, which makes every price NaN
    with no error anywhere in the chain. Yahoo really does return a NaN close
    for the current session's bar, so this is a live case and not a hypothetical
    one. The fetcher filters them too; defence in depth is deliberate, because
    the failure is silent.
    """
    closes = [c for c in closes if isinstance(c, (int, float)) and math.isfinite(c) and c > 0]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    # ONE guard. There used to be a second, on len(closes) < 20, and since n
    # closes always give exactly n-1 returns the two fired on identical inputs.
    # The first was dead code, which a mutation proved by surviving: breaking it
    # changed nothing because the other one caught every case anyway. A
    # redundant guard is not defence in depth, it is an untestable line.
    if len(rets) < MIN_RETURNS:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def trailing_dividend_total(payments, as_of: datetime.date) -> float:
    """Dividends paid in the 365 days ending at `as_of`.

    `payments` is an iterable of (date, amount). Extracted out of the fetcher
    deliberately: this is the part with the actual risk in it, and while it
    lived inside the networked function no test could reach it. Every test
    injected a pre-computed total and skipped this arithmetic entirely.

    The bug that forced this out (found in review, verified against live AAPL
    data): the window used to be anchored to the LAST PAYMENT DATE rather than
    to today.

        cutoff = divs.index.max() - 365 days      # wrong
        cutoff = today - 365 days                 # right

    Anchoring to the last payment slides the window backwards by however long
    it has been since that payment, which pulls an extra historical payment in.
    On AAPL, 44 days after its last ex-date, that captured FIVE quarterly
    payments instead of four: 1.32 against a correct 1.06, a 24.5% overstated
    yield, flowing silently into q and from there into d1, the discounting, and
    delta, theta and charm.

    Worse in the other direction: a company that SUSPENDED its dividend two
    years ago still reports a full year of payments, because the window follows
    the payments backwards instead of staying put. A non-payer is reported as a
    payer, with nothing raised anywhere.

    Future-dated payments are excluded too. A declared-but-not-yet-paid ex-date
    can appear in the series, and counting it would inflate a TRAILING figure
    with something that has not happened.
    """
    cutoff = as_of - datetime.timedelta(days=365)
    return float(sum(amount for when, amount in payments if cutoff < when <= as_of))


def _default_fetcher(ticker: str) -> dict:
    """Pull the raw pieces from yfinance. The only function here that uses the network."""
    # Guarded here as well as in fetch_quote. The module argues elsewhere that
    # a silent failure deserves two checks rather than one, and a test that
    # quietly reaches Yahoo is exactly that kind of failure, so the leaf gets
    # the same treatment as the entry point.
    _guard_against_tests()

    import yfinance as yf

    t = yf.Ticker(ticker)

    # An unknown symbol surfaces from yfinance as an assortment of internal
    # errors (KeyError 'currentTradingPeriod' is the current one), which is
    # useless to whoever typed the symbol. Translate it once, here, rather than
    # letting an implementation detail reach the UI.
    try:
        fast = t.fast_info
        spot = fast.get("lastPrice")
        currency = fast.get("currency") or "USD"
    except Exception as exc:  # noqa: BLE001
        raise MarketDataUnavailable(
            f"no market data for {ticker}. Check the symbol, or type the spot in by hand."
        ) from exc

    if spot is None:
        raise MarketDataUnavailable(
            f"no price returned for {ticker}. Check the symbol, or type the spot in by hand."
        )

    hist = t.history(period="1y")
    # Today's bar can carry a NaN close while the session is still open or has
    # just closed. Taking the last close naively yields NaN, which then
    # propagates into the volatility and out into a price with no error raised
    # anywhere. Drop them rather than trusting the last row.
    closes = [float(c) for c in hist["Close"].tolist() if c == c]
    last_bar = str(hist.index[-1]) if len(hist.index) else None

    divs = t.dividends
    payments = [(ts.date(), float(amount)) for ts, amount in divs.items()]
    trailing = trailing_dividend_total(payments, datetime.date.today())

    return {
        "spot": spot,
        "currency": currency,
        "closes": closes,
        "last_bar": last_bar,
        "trailing_dividends": trailing,
    }


def fetch_quote(ticker: str, fetcher=None, now=None, use_cache: bool = True) -> Quote:
    """Fetch and assemble a Quote. Raises MarketDataUnavailable on any failure.

    `fetcher` is injectable so the whole assembly path, including the units
    handling that is the actual risk here, can be tested without a network.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise MarketDataUnavailable("no ticker given")
    if len(ticker) > 12 or not all(c.isalnum() or c in ".-^=" for c in ticker):
        raise MarketDataUnavailable(f"{ticker!r} does not look like a ticker symbol")

    clock = now or time.time
    if fetcher is None:
        _guard_against_tests()
        fetcher = _default_fetcher

    # Checked for every caller, not only the live one, so the cache is
    # reachable from a test with an injected fetcher and a fake clock. It had
    # no coverage at all while it sat behind the network guard.
    if use_cache:
        hit = _cache.get(ticker)
        if hit and clock() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]

    try:
        raw = fetcher(ticker)
    except MarketDataUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any upstream failure is the same to us
        raise MarketDataUnavailable(f"could not fetch {ticker}: {type(exc).__name__}: {exc}") from exc

    spot = raw.get("spot")
    if spot is None or not isinstance(spot, (int, float)) or spot != spot or spot <= 0:
        raise MarketDataUnavailable(f"no usable price returned for {ticker}")

    closes = raw.get("closes") or []
    trailing = float(raw.get("trailing_dividends") or 0.0)

    quote = Quote(
        ticker=ticker,
        spot=float(spot),
        currency=raw.get("currency") or "USD",
        as_of=raw.get("last_bar") or "unknown",
        realized_vol_1y=_annualised_vol(closes),
        realized_vol_30d=_annualised_vol(closes[-31:]) if len(closes) >= 31 else None,
        # Derived, not read off a field. See the module docstring.
        dividend_yield=trailing / float(spot),
        trailing_dividends=trailing,
        bars_used=len(closes),
    )

    if use_cache:
        _cache[ticker] = (clock(), quote)
    return quote


@dataclass(frozen=True)
class ChainRow:
    """One listed contract, with the implied volatility WE computed."""

    strike: float
    last_price: float
    volume: float
    open_interest: float
    last_trade: str
    implied_vol: float | None
    implied_vol_uncertainty: float | None
    implied_vol_error: str | None
    yahoo_implied_vol: float | None
    in_the_money: bool

    def to_dict(self) -> dict:
        return asdict(self)


def build_chain(rows, spot: float, T: float, r: float, q: float, kind: str) -> list[ChainRow]:
    """Turn raw chain rows into ChainRows, inverting each price for its own vol.

    Pure and injectable on purpose: this is where the judgement is, so it has
    to be testable without a network.

    The implied volatility here is computed by inverting OUR pricer against the
    contract's traded price. It is deliberately NOT read from the feed's own
    `impliedVolatility` field, and that is not stylistic preference. Measured
    against live AAPL on 2026-09-23, that field returned between 0.025% and
    0.099% across every expiry, which is not a volatility, while inverting the
    same contracts' traded prices gives 23.0%, 25.5%, 25.0% and 27.3%, a
    sensible term structure sitting either side of the 24.7% the stock actually
    realized. The field is carried through anyway, clearly labelled, so the two
    can be compared rather than one being quietly trusted.

    A row that cannot be inverted keeps its reason instead of being dropped.
    Silently omitting the contracts that failed would make the chain look
    cleaner than it is, and the failures are usually the informative part: a
    price below intrinsic means a stale quote, and a price flat in volatility
    means the contract carries no volatility information at all.
    """
    from pricing.bsm import NoImpliedVol, implied_vol_with_uncertainty

    out: list[ChainRow] = []
    for row in rows:
        iv: float | None = None
        unc: float | None = None
        err: str | None = None
        try:
            # Keep the uncertainty, do not discard it. An implied vol is only
            # as good as the vega behind it, and that varies by ten orders of
            # magnitude across one chain. A row quoted to four decimals that is
            # only good to two is the kind of thing nobody notices until it
            # matters.
            iv, unc = implied_vol_with_uncertainty(
                float(row["lastPrice"]), spot, float(row["strike"]), T, r, kind, q=q
            )
        except (NoImpliedVol, ValueError, TypeError) as exc:
            err = str(exc)

        yahoo = row.get("impliedVolatility")
        out.append(
            ChainRow(
                strike=float(row["strike"]),
                last_price=float(row["lastPrice"]),
                volume=float(row.get("volume") or 0.0),
                open_interest=float(row.get("openInterest") or 0.0),
                last_trade=str(row.get("lastTradeDate") or "")[:19],
                implied_vol=iv,
                implied_vol_uncertainty=unc,
                implied_vol_error=err,
                yahoo_implied_vol=float(yahoo) if yahoo is not None else None,
                in_the_money=bool(row.get("inTheMoney", False)),
            )
        )
    return out


def fetch_expiries(ticker: str) -> list[str]:
    """Listed expiry dates for a ticker, soonest first."""
    _guard_against_tests()
    import yfinance as yf

    try:
        return list(yf.Ticker(ticker.strip().upper()).options)
    except Exception as exc:  # noqa: BLE001
        raise MarketDataUnavailable(f"no expiries for {ticker}: {type(exc).__name__}") from exc


def _default_chain_fetcher(ticker: str, expiry: str, kind: str) -> list[dict]:
    """Raw chain rows from yfinance. Networked."""
    _guard_against_tests()
    import yfinance as yf

    try:
        chain = yf.Ticker(ticker).option_chain(expiry)
    except Exception as exc:  # noqa: BLE001
        raise MarketDataUnavailable(
            f"no {kind} chain for {ticker} at {expiry}. Check the expiry date."
        ) from exc

    frame = chain.calls if kind == "call" else chain.puts
    return frame.to_dict("records")


def fetch_chain(
    ticker: str,
    expiry: str,
    spot: float,
    r: float,
    q: float,
    kind: str,
    as_of: datetime.date | None = None,
    fetcher=None,
) -> dict:
    """The chain for one expiry, with our own implied vol on every row."""
    ticker = (ticker or "").strip().upper()
    as_of = as_of or datetime.date.today()
    try:
        exp_date = datetime.date.fromisoformat(expiry)
    except ValueError as exc:
        raise MarketDataUnavailable(f"{expiry!r} is not a date") from exc

    days = (exp_date - as_of).days
    if days <= 0:
        raise MarketDataUnavailable(f"{expiry} is not in the future; nothing left to price")
    T = days / CALENDAR_DAYS

    rows = (fetcher or _default_chain_fetcher)(ticker, expiry, kind)
    chain = build_chain(rows, spot, T, r, q, kind)
    return {
        "ticker": ticker,
        "expiry": expiry,
        "days_to_expiry": days,
        "T": T,
        "kind": kind,
        "spot": spot,
        "rows": [row.to_dict() for row in chain],
    }
