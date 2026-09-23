# Options Pricer

Black-Scholes-Merton and a Cox-Ross-Rubinstein binomial lattice, with the full
greek set and a browser front end that shows the derivation next to the code
that implements it.

Built for Traders@SMU Quantitative Strategies, week 1.

## What is in it

- **BSM closed form** for European calls and puts, with delta, gamma, vega,
  theta, rho, vanna, volga and charm.
- **CRR binomial lattice**, European and American exercise, with the
  no-arbitrage condition `d < exp((r-q)dt) < u` asserted before any pricing
  happens.
- **A localhost app** with two tabs: the model itself, and a walkthrough that
  puts the derivation beside the real source file it produced.
- **Live quotes.** Type a ticker and it fills spot, realized volatility and
  dividend yield from Yahoo. This is the only networked part of the project and
  it is quarantined: if it fails, the pricer does not, and you type the spot in
  by hand as before.

## Running it

```
python3 -m pip install -r requirements.txt
python3 app.py
```

Then open http://127.0.0.1:8899

## Running the tests

```
python3 -m pytest tests/ -q
```

## What "done" means here

Not "it returns a number." The suite pins the published reference case
(S = K = 100, r = 5%, sigma = 20%, T = 1):

| Quantity | Value |
|---|---|
| European call, closed form | 10.4506 |
| European put, closed form | 5.5735 |
| European call, 2000-step tree | 10.4496 |
| American put, 2000-step tree | 6.09 |
| Early-exercise premium on the put | about 0.52 |
| American call minus European call, no dividend | exactly 0 |
| Put-call parity residual | 0 to machine precision |

It also pins the four sanity checks: deep in the money goes to intrinsic, deep
out of the money goes to zero, higher volatility always raises the price for
both calls and puts, and as the rate goes to zero the put's early-exercise
edge disappears.

Every analytic greek is checked against a central finite difference of the
price function, because a greek formula with one wrong sign still returns
plausible numbers and only a numerical derivative catches it.

## A note on volatility

The ticker fetch fills sigma with **realized** volatility, which is what the
stock actually did. Black-Scholes wants **implied** volatility, backed out of a
traded option price. They are different numbers, and substituting one for the
other is a modelling assumption rather than a data lookup. The app says so on
screen every time it fills the field.

## A note on the tests

The test suite has been mutation-tested: each formula was deliberately broken
one at a time to confirm a specific test goes red. A passing test proves
nothing until you have watched it fail. One test was found to be passing for
the wrong reason that way and was narrowed.
