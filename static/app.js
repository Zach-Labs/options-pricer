/* Front end for the pricer.
 *
 * No chart library. The plots are hand-drawn SVG, partly to keep the page
 * dependency-free and offline, and partly because a chart you drew yourself is
 * one you can explain. Roughly 80 lines of it.
 */

"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
// Ordered light-to-dark so the shortest expiry reads strongest against paper.
const PALETTE = ["#b9c6d6", "#8ba3bf", "#5f7fa5", "#3c5f8a", "#1a3e6f", "#8f2d56"];

const state = {
  kind: "call",
  style: "european",
  tmode: "years",
  curve: "gamma",
  latSteps: 5,
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ utils */

function fmt(x, dp = 4) {
  if (x === null || x === undefined) return "-";
  if (!isFinite(x)) return "n/a";
  if (x !== 0 && Math.abs(x) < 1e-7) return x.toExponential(2);
  return x.toFixed(dp);
}

function payload() {
  return {
    S: parseFloat($("S").value),
    K: parseFloat($("K").value),
    T: parseFloat($("T").value),
    r: parseFloat($("r").value),
    sigma: parseFloat($("sigma").value),
    q: parseFloat($("q").value),
    steps: parseInt($("steps").value, 10),
    kind: state.kind,
    american: state.style === "american",
    use_expiry_date: state.tmode === "date",
    expiry: $("expiry").value,
  };
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `request failed: ${res.status}`);
  return data;
}

async function get(url) {
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `request failed: ${res.status}`);
  return data;
}

/* ------------------------------------------------------------- SVG drawing */

function el(tag, attrs, text) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text !== undefined) n.textContent = text;
  return n;
}

function niceTicks(lo, hi, count) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(v);
  return out;
}

/* Draw a line chart. series = [{label, color, points: [[x,y], ...]}] */
function plot(svg, series, opts = {}) {
  svg.innerHTML = "";
  const W = 900, H = svg.viewBox.baseVal.height || 340;
  const M = { l: 62, r: 16, t: 14, b: 38 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const all = series.flatMap((s) => s.points);
  if (!all.length) return;

  let xlo = Math.min(...all.map((p) => p[0])), xhi = Math.max(...all.map((p) => p[0]));
  let ylo = Math.min(...all.map((p) => p[1])), yhi = Math.max(...all.map((p) => p[1]));

  if (opts.extraY !== undefined) { ylo = Math.min(ylo, opts.extraY); yhi = Math.max(yhi, opts.extraY); }
  const pad = (yhi - ylo) * 0.08 || Math.abs(yhi) * 0.1 || 1;
  ylo -= pad; yhi += pad;
  if (ylo > 0 && ylo < (yhi - ylo) * 0.4) ylo = 0;

  const X = (v) => M.l + ((v - xlo) / (xhi - xlo)) * iw;
  const Y = (v) => M.t + ih - ((v - ylo) / (yhi - ylo)) * ih;

  // grid and ticks
  for (const t of niceTicks(ylo, yhi, 5)) {
    if (t < ylo || t > yhi) continue;
    svg.appendChild(el("line", { class: "grid", x1: M.l, x2: W - M.r, y1: Y(t), y2: Y(t) }));
    svg.appendChild(el("text", { class: "tick", x: M.l - 8, y: Y(t) + 3.5, "text-anchor": "end" }, fmt(t, Math.abs(t) < 1 ? 3 : 2)));
  }
  for (const t of niceTicks(xlo, xhi, 7)) {
    if (t < xlo || t > xhi) continue;
    svg.appendChild(el("text", { class: "tick", x: X(t), y: H - M.b + 16, "text-anchor": "middle" }, fmt(t, opts.xdp ?? 0)));
  }

  // zero line, if the range straddles it
  if (ylo < 0 && yhi > 0) {
    svg.appendChild(el("line", { class: "axis", x1: M.l, x2: W - M.r, y1: Y(0), y2: Y(0) }));
  }
  svg.appendChild(el("line", { class: "axis", x1: M.l, x2: M.l, y1: M.t, y2: M.t + ih }));
  svg.appendChild(el("line", { class: "axis", x1: M.l, x2: W - M.r, y1: M.t + ih, y2: M.t + ih }));

  // a vertical marker (the strike, usually)
  if (opts.markerX !== undefined && opts.markerX >= xlo && opts.markerX <= xhi) {
    svg.appendChild(el("line", { class: "marker", x1: X(opts.markerX), x2: X(opts.markerX), y1: M.t, y2: M.t + ih }));
    svg.appendChild(el("text", { class: "marker-label", x: X(opts.markerX) + 5, y: M.t + 11 }, opts.markerLabel || ""));
  }
  // a horizontal marker (the closed-form value, usually)
  if (opts.markerY !== undefined) {
    svg.appendChild(el("line", { class: "marker", x1: M.l, x2: W - M.r, y1: Y(opts.markerY), y2: Y(opts.markerY) }));
    svg.appendChild(el("text", { class: "marker-label", x: W - M.r - 4, y: Y(opts.markerY) - 5, "text-anchor": "end" }, opts.markerYLabel || ""));
  }

  for (const s of series) {
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(2)},${Y(p[1]).toFixed(2)}`).join("");
    svg.appendChild(el("path", { class: "series", d, stroke: s.color }));
  }

  if (opts.xlabel) svg.appendChild(el("text", { class: "axis-label", x: M.l + iw / 2, y: H - 4, "text-anchor": "middle" }, opts.xlabel));
  if (opts.ylabel) svg.appendChild(el("text", { class: "axis-label", x: 14, y: M.t + ih / 2, "text-anchor": "middle", transform: `rotate(-90 14 ${M.t + ih / 2})` }, opts.ylabel));
}

/* ------------------------------------------------------- render: the model */

/* Split a number so the decimals can be held back typographically. The eye
 * should land on the dollars; the cents are confirmation, not headline. */
function splitNumber(x, dp = 4) {
  const str = Math.abs(x) < 1e-7 && x !== 0 ? x.toExponential(2) : x.toFixed(dp);
  const i = str.indexOf(".");
  return i < 0 ? [str, ""] : [str.slice(0, i), str.slice(i)];
}

function renderCards(d) {
  const [whole, frac] = splitNumber(d.closed_form, 4);
  $("price-hero").innerHTML =
    `<div class="price-main">${whole}<span class="frac">${frac}</span></div>`;

  const style = state.style === "american" ? "American" : "European";
  const days = Math.round(d.inputs.T * 365);
  $("price-caption").innerHTML =
    `${style} <b>${state.kind}</b>, strike <b>${fmt(d.inputs.K, 2)}</b>, ` +
    `${days} days out, on a spot of <b>${fmt(d.inputs.S, 2)}</b> at ` +
    `<b>${(d.inputs.sigma * 100).toFixed(2)}%</b> volatility.`;

  const tv = d.closed_form - d.intrinsic;
  const stats = [
    { k: "Intrinsic", v: fmt(d.intrinsic, 4), n: "if exercised now" },
    { k: "Time value", v: fmt(tv, 4), n: "what the optionality is worth" },
  ];

  if (d.tree && !d.tree.error) {
    stats.push({
      k: `Lattice, ${d.inputs.steps} steps`,
      v: fmt(d.tree.european, 4),
      n: `${fmt(d.tree.tree_vs_closed_form, 6)} from the closed form`,
    });
    stats.push({
      k: "Early exercise",
      v: fmt(d.tree.early_exercise_premium, 4),
      n: "American minus European",
    });
  }

  $("stats").innerHTML = stats.map((c) =>
    `<div class="stat">
       <div class="stat-label">${c.k}</div>
       <div class="stat-value">${c.v}</div>
       <div class="stat-note">${c.n}</div>
     </div>`).join("");

  if (d.tree && d.tree.error) {
    $("tree-note").innerHTML = `<div class="err">Lattice refused: ${d.tree.error}</div>`;
  } else if (d.tree) {
    const p = d.tree.params;
    const ok = p.d < p.growth && p.growth < p.u;
    $("tree-note").innerHTML =
      `CRR parameters: u = ${fmt(p.u, 6)}, d = ${fmt(p.d, 6)}, ` +
      `e<sup>(r&#8722;q)dt</sup> = ${fmt(p.growth, 6)}, q = ${fmt(p.qp, 6)}. ` +
      `No-arbitrage d &lt; growth &lt; u <span class="badge ${ok ? "ok" : "no"}">` +
      `${ok ? "holds" : "VIOLATED"}</span>`;
  }
}

function renderGreeks(d) {
  $("greeks-body").innerHTML = GREEK_META.map((g) => {
    const shown = d.greeks_display[g.key];
    const raw = d.greeks_raw[g.key];
    const rescaled = Math.abs(shown - raw) > 1e-12;
    return `<tr>
      <td>
        <div><span class="greek-name">${g.name}</span><span class="greek-sym">${g.symbol}</span></div>
        <div class="greek-partial">${g.partial}</div>
      </td>
      <td class="num">
        <div class="greek-val">${fmt(shown, 5)}</div>
        ${rescaled ? `<div class="greek-raw">${g.scale}</div>` : ""}
      </td>
      <td><div class="greek-meaning">${g.meaning}</div></td>
    </tr>`;
  }).join("");
}

async function refreshCurves() {
  const data = await post("/api/curves", payload());
  const key = state.curve;
  const series = data.series.map((s, i) => ({
    label: s.label,
    color: PALETTE[i % PALETTE.length],
    points: data.spots.map((x, j) => [x, s.rows[key][j]]),
  }));
  plot($("curve-plot"), series, {
    markerX: data.strike,
    markerLabel: `K = ${data.strike}`,
    xlabel: "spot",
    ylabel: key,
  });
  $("curve-legend").innerHTML = series.map((s) =>
    `<span><i style="background:${s.color}"></i>${s.label}</span>`).join("");
}

async function refreshConvergence() {
  const body = payload();
  body.max_steps = 150;
  const data = await post("/api/convergence", body);
  plot($("conv-plot"), [{
    color: PALETTE[0],
    points: data.points.map((p) => [p.n, p.price]),
  }], {
    markerY: data.closed_form,
    markerYLabel: `closed form ${fmt(data.closed_form, 4)}`,
    extraY: data.closed_form,
    xlabel: "steps N",
    ylabel: "price",
  });
}

async function refreshLattice() {
  const body = payload();
  body.steps = state.latSteps;
  body.american = state.style === "american";
  const svg = $("lat-plot");
  let data;
  try {
    data = await post("/api/lattice", body);
  } catch (e) {
    svg.innerHTML = "";
    $("lat-note").innerHTML = `<div class="err">${e.message}</div>`;
    return;
  }

  svg.innerHTML = "";
  // Read the height off the viewBox rather than hard-coding it. Hard-coding is
  // how the markup and the drawing code drifted apart and clipped the bottom
  // row of nodes when the viewBox was changed.
  const W = 900;
  const H = svg.viewBox.baseVal.height || 400;
  const M = { l: 30, r: 30, t: 24, b: 24 };
  const n = data.params.steps;
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const allSpots = data.slices.flatMap((s) => s.spots);
  const lo = Math.min(...allSpots), hi = Math.max(...allSpots);
  const X = (step) => M.l + (n === 0 ? 0 : (step / n) * iw);
  const Y = (spot) => M.t + ih - ((spot - lo) / (hi - lo || 1)) * ih;

  // edges first, so nodes sit on top
  for (let s = 0; s < n; s++) {
    for (let j = 0; j <= s; j++) {
      const x1 = X(s), y1 = Y(data.slices[s].spots[j]);
      for (const cj of [j, j + 1]) {
        svg.appendChild(el("line", {
          class: "edge", x1, y1, x2: X(s + 1), y2: Y(data.slices[s + 1].spots[cj]),
        }));
      }
    }
  }

  for (let s = 0; s <= n; s++) {
    const sl = data.slices[s];
    for (let j = 0; j < sl.spots.length; j++) {
      const x = X(s), y = Y(sl.spots[j]);
      const ex = sl.exercise[j] && s < n;
      svg.appendChild(el("rect", {
        class: `node${ex ? " ex" : ""}`, x: x - 26, y: y - 15, width: 52, height: 30, rx: 4,
      }));
      svg.appendChild(el("text", { class: "spot", x, y: y - 3, "text-anchor": "middle" }, fmt(sl.spots[j], 2)));
      svg.appendChild(el("text", { class: `val${ex ? " ex" : ""}`, x, y: y + 9, "text-anchor": "middle" }, fmt(sl.values[j], 3)));
    }
  }

  const exCount = data.slices.slice(0, -1).reduce((a, s) => a + s.exercise.filter(Boolean).length, 0);
  $("lat-note").innerHTML =
    `Top number is the spot at that node, bottom is the option's value there. ` +
    (state.style === "american"
      ? `Highlighted nodes are where an American holder exercises rather than waits: ${exCount} interior node${exCount === 1 ? "" : "s"}. ` +
        `Nothing told the model where that boundary is. It emerged from one max(intrinsic, continuation) per node.`
      : `European exercise, so there is no early-exercise decision to make at any node. Switch to American on the left to see the boundary appear.`);
}

/* ------------------------------------------------------------ live quotes */

/* The only networked feature. It fills the form and then gets out of the way:
 * every field stays editable, and a failure leaves whatever was already there.
 * The pricer never depended on this and still does not. */

/* Round to the kind of increment exchanges actually list strikes on, so the
 * default contract looks like a real one rather than an arbitrary decimal. */
function nearestStrike(spot) {
  const step = spot < 25 ? 0.5 : spot < 100 ? 1 : spot < 250 ? 2.5 : 5;
  return Math.round(spot / step) * step;
}

async function fetchQuote() {
  const sym = $("ticker").value.trim().toUpperCase();
  const status = $("quote-status");
  const btn = $("btn-fetch");
  if (!sym) return;

  btn.disabled = true;
  status.className = "hint";
  status.textContent = `fetching ${sym}...`;
  document.querySelector(".quote-detail")?.remove();

  try {
    const q = await get(`/api/quote/${encodeURIComponent(sym)}`);

    $("S").value = q.spot.toFixed(2);
    // 8 decimals, not 4. These fields are WRITTEN by code and then READ BACK to
    // price with, so truncating here silently changes the input. Rounding the
    // dividend yield to 4dp shifted it by 2e-5, which moved the repriced value
    // by 9e-4 and made the chain's "reprices the market exactly" line show a
    // visible residual that had nothing to do with the solver.
    $("q").value = q.dividend_yield.toFixed(8);
    // Move the strike to the money as well. Leaving a stale strike behind means
    // fetching a $230 stock against a $100 strike, which lands you on a deep
    // in-the-money option whose greeks are all pinned (delta at 1, gamma at 0)
    // and shows none of the behaviour worth looking at. Overwrite it, and say
    // so in the panel rather than changing a field silently.
    $("K").value = nearestStrike(q.spot).toFixed(2);
    // Prefer the 1-year window: it is the more stable estimate, and a 30-day
    // number is jumpy enough that pre-filling with it would make the price
    // look unstable for reasons that have nothing to do with the model.
    const rv = q.realized_vol_1y ?? q.realized_vol_30d;
    if (rv !== null && rv !== undefined) $("sigma").value = rv.toFixed(8);

    status.className = "hint ok";
    status.textContent = `${q.ticker} ${q.spot.toFixed(2)} ${q.currency}`;

    const pct = (x) => (x === null || x === undefined ? "n/a" : (x * 100).toFixed(2) + "%");
    const detail = document.createElement("div");
    detail.className = "quote-detail";
    detail.innerHTML =
      `spot          ${q.spot.toFixed(2)} ${q.currency}<br>` +
      `as of         ${String(q.as_of).slice(0, 10)}<br>` +
      `realized vol  ${pct(q.realized_vol_1y)} (1y) · ${pct(q.realized_vol_30d)} (30d)<br>` +
      `div yield     ${pct(q.dividend_yield)} (${q.trailing_dividends.toFixed(2)} paid / spot)<br>` +
      `source        ${q.source}<br>` +
      `strike        ${Number($("K").value).toFixed(2)}, nearest listed` +
      `<span class="warn-note">Sigma is <b>realized</b> vol, what the stock did. ` +
      `Black-Scholes wants <b>implied</b> vol. Click a chain row for the real one.</span>`;
    $("quote-status").after(detail);

    scheduleRefresh();
    loadExpiries(sym);
  } catch (e) {
    status.className = "hint bad";
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------- the option chain */

let chainData = null;

async function loadExpiries(ticker) {
  try {
    const d = await get(`/api/expiries/${encodeURIComponent(ticker)}`);
    const sel = $("chain-expiry");
    sel.innerHTML = d.expiries.map((e) => `<option value="${e}">${e}</option>`).join("");
    // Default to roughly three months out rather than the front week. The
    // nearest expiries are the ones whose quotes are thinnest and most likely
    // to be stale, which makes a bad first impression of a working chain.
    const target = d.expiries.find((e) => (new Date(e) - new Date()) / 86400000 > 80);
    if (target) sel.value = target;
    $("chain-section").hidden = false;
    await loadChain();
  } catch (e) {
    $("chain-section").hidden = true;
  }
}

async function loadChain() {
  const ticker = $("ticker").value.trim().toUpperCase();
  const expiry = $("chain-expiry").value;
  if (!ticker || !expiry) return;

  $("chain-body").innerHTML = `<div class="footnote">loading ${ticker} ${expiry}...</div>`;
  try {
    chainData = await get(
      `/api/chain/${encodeURIComponent(ticker)}/${encodeURIComponent(expiry)}` +
      `?kind=${state.kind}&r=${encodeURIComponent($("r").value)}`
    );
    renderChain();
  } catch (e) {
    chainData = null;
    $("chain-body").innerHTML = `<div class="err">${e.message}</div>`;
  }
}

function renderChain() {
  const d = chainData;
  if (!d) return;

  // Show a window around the money. A full 97-row chain is mostly deep wings
  // with stale quotes, and the interesting structure is near the spot.
  const near = d.rows
    .filter((r) => Math.abs(r.strike - d.spot) <= d.spot * 0.15)
    .sort((a, b) => a.strike - b.strike);

  const withIv = near.filter((r) => r.implied_vol !== null);
  $("chain-summary").textContent =
    `${d.days_to_expiry} days · ${near.length} strikes near the money · ` +
    `${withIv.length} inverted`;

  const rows = near.map((r) => {
    const atm = Math.abs(r.strike - d.spot) < d.spot * 0.008;
    // Flag a row whose implied vol is real but imprecise. Vega varies by ten
    // orders of magnitude across one chain, so the same price noise is
    // invisible at the money and material in the wings.
    const shaky = r.implied_vol !== null && r.implied_vol_uncertainty > 1e-5;
    const iv = r.implied_vol !== null
      ? `<span class="ours">${(r.implied_vol * 100).toFixed(2)}%</span>` +
        (shaky ? `<span class="bad-iv" title="only pinned to about ${(r.implied_vol_uncertainty * 100).toFixed(3)} vol points here, because vega is small at this strike"> ±${(r.implied_vol_uncertainty * 100).toFixed(2)}</span>` : "")
      : `<span class="bad-iv" title="${(r.implied_vol_error || "").replace(/"/g, "&quot;")}">no vol</span>`;
    const yiv = r.yahoo_implied_vol !== null
      ? `<span class="dim">${(r.yahoo_implied_vol * 100).toFixed(3)}%</span>` : "-";
    return `<tr data-strike="${r.strike}" class="${atm ? "atm" : ""}">
      <td>${r.strike.toFixed(2)}</td>
      <td>${r.last_price.toFixed(2)}</td>
      <td>${iv}</td>
      <td>${yiv}</td>
      <td class="dim">${r.volume.toFixed(0)}</td>
      <td class="dim">${r.last_trade.slice(0, 10)}</td>
    </tr>`;
  }).join("");

  $("chain-body").innerHTML = `
    <table class="chain">
      <thead><tr>
        <th>Strike</th><th>Last traded</th>
        <th>Implied vol (ours)</th><th>Implied vol (feed)</th>
        <th>Volume</th><th>Last trade</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div id="chain-compare"></div>`;

  $("chain-body").querySelectorAll("tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => applyContract(parseFloat(tr.dataset.strike), tr));
  });
}

async function applyContract(strike, tr) {
  const d = chainData;
  const row = d.rows.find((r) => r.strike === strike);
  if (!row) return;

  $("chain-body").querySelectorAll("tr").forEach((el) => el.classList.remove("picked"));
  tr.classList.add("picked");

  $("K").value = strike.toFixed(2);
  $("T").value = d.T.toFixed(8);
  state.tmode = "years";
  $("T").hidden = false;
  $("expiry").hidden = true;
  [...$("seg-tmode").querySelectorAll("button")].forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.val === "years")));

  if (row.implied_vol !== null) $("sigma").value = row.implied_vol.toFixed(8);

  await refreshAll();

  // The point of the whole feature, made explicit: fed the market's own
  // implied volatility, the model reproduces the market's own price. That is
  // not a coincidence, it is what implied vol MEANS, and seeing the two land
  // on the same number is the clearest possible demonstration of it.
  const priced = await post("/api/price", payload());
  const cmp = $("chain-compare");
  if (!cmp) return;

  if (row.implied_vol === null) {
    cmp.innerHTML = `<div class="caption">
      Loaded the ${strike.toFixed(2)} strike, but this contract has
      <strong>no implied volatility</strong>: ${row.implied_vol_error}
      Sigma was left at its previous value, so the price above is the model's, not the market's.
    </div>`;
    return;
  }

  const diff = priced.closed_form - row.last_price;
  const cells = [
    { k: "Market last traded", v: row.last_price.toFixed(4), n: "" },
    { k: "Implied volatility", v: (row.implied_vol * 100).toFixed(4) + "%", n: "inverted from that price" },
    { k: "Our model at that vol", v: priced.closed_form.toFixed(4), n: "" },
    { k: "Difference", v: diff.toExponential(2), n: "" },
  ];
  cmp.innerHTML =
    `<div class="compare">` +
    cells.map((c) =>
      `<div class="stat">
         <div class="stat-label">${c.k}</div>
         <div class="stat-value">${c.v}</div>
         <div class="stat-note">${c.n}</div>
       </div>`).join("") +
    `</div>
     <div class="caption">Fed the market&#8217;s own implied volatility, the formula reproduces
     the market&#8217;s own price. That is not a coincidence, it is what implied volatility
     means.</div>`;
}

/* -------------------------------------------------- render: the walkthrough */

/* Single-pass tokenizer.
 *
 * This started as four chained .replace() calls and that was broken in two ways
 * that only showed up once the page was actually rendered. The string pass
 * inserted `<span class="tok-str">`, and then the KEYWORD pass matched the word
 * `class` inside that markup and wrapped it, so the attribute leaked onto the
 * page as literal text. The same chaining also highlighted `or`, `in` and
 * `return` where they appeared as ordinary English inside docstrings.
 *
 * Both have one cause: later passes re-reading text earlier passes had already
 * claimed. One regex with alternation fixes it, because each character is
 * consumed exactly once and escaping happens per token rather than up front.
 */
const TOKENS = new RegExp([
  /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/,  // 1 string
  /(#[^\n]*)/,                                                                  // 2 comment
  /\b(def|class|return|if|elif|else|for|while|in|not|and|or|import|from|raise|try|except|finally|with|as|lambda|None|True|False|assert|yield|pass|continue|break|global|is|del)\b/, // 3 keyword
  /\b(\d+\.?\d*(?:[eE][-+]?\d+)?)\b/,                                           // 4 number
].map((r) => r.source).join("|"), "g");

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function highlight(code) {
  const CLASSES = { 1: "tok-str", 2: "tok-com", 3: "tok-kw", 4: "tok-num" };
  let out = "";
  let last = 0;
  let m;
  TOKENS.lastIndex = 0;
  while ((m = TOKENS.exec(code)) !== null) {
    out += escapeHtml(code.slice(last, m.index));
    for (const g of [1, 2, 3, 4]) {
      if (m[g] !== undefined) {
        out += `<span class="${CLASSES[g]}">${escapeHtml(m[g])}</span>`;
        break;
      }
    }
    last = m.index + m[0].length;
  }
  return out + escapeHtml(code.slice(last));
}

async function loadCodeBlocks() {
  const blocks = document.querySelectorAll(".code-block[data-symbol], .code-block[data-file]");
  for (const b of blocks) {
    if (b.dataset.loaded) continue;
    try {
      let data, head;
      if (b.dataset.symbol) {
        const [mod, sym] = b.dataset.symbol.split("/");
        data = await get(`/api/symbol/${mod}/${sym}`);
        head = `${data.path} : ${data.symbol}()  line ${data.start_line}`;
      } else {
        data = await get(`/api/source/${b.dataset.file}`);
        head = `${data.path}  (${data.lines} lines)`;
      }
      b.innerHTML =
        `<div class="code-head"><span>${head}</span>
           <span class="live">read from disk just now, not a copy</span></div>
         <pre>${highlight(data.code)}</pre>`;
      b.dataset.loaded = "1";
    } catch (e) {
      b.innerHTML = `<div class="err">could not load: ${e.message}</div>`;
    }
  }
}

async function loadVerify() {
  const panel = $("verify-panel");
  try {
    const data = await get("/api/verify");
    panel.innerHTML = data.rows.map((r) =>
      `<div class="check">
         <span class="dot ${r.passed ? "ok" : "no"}"></span>
         <span class="label">${r.label}</span>
         <span class="got">${r.got === null ? "" : fmt(r.got, 8)}</span>
         <span class="target">${r.target === null || r.target === undefined ? "" : "target " + fmt(r.target, 6)}</span>
         <span class="note">${r.note}</span>
       </div>`).join("") +
      `<div style="padding:10px 10px 0"><span class="badge ${data.all_passed ? "ok" : "no"}">
         ${data.all_passed ? "all checks pass" : "SOMETHING FAILED"}</span></div>`;
  } catch (e) {
    panel.innerHTML = `<div class="err">${e.message}</div>`;
  }
}

/* ------------------------------------------------------------------ wiring */

let pending = null;
function scheduleRefresh() {
  clearTimeout(pending);
  pending = setTimeout(refreshAll, 140);
}

async function refreshAll() {
  try {
    const d = await post("/api/price", payload());
    renderCards(d);
    renderGreeks(d);
    $("t-readout").textContent =
      state.tmode === "date"
        ? `T = ${fmt(d.inputs.T, 4)} years (ACT/365)`
        : `${(d.inputs.T * 365).toFixed(0)} calendar days`;
    await refreshCurves();
    await refreshConvergence();
    await refreshLattice();
  } catch (e) {
    $("tree-note").innerHTML = `<div class="err">${e.message}</div>`;
  }
}

function wireSegment(id, key, after) {
  const box = $(id);
  box.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    [...box.querySelectorAll("button")].forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    state[key] = btn.dataset.val;
    if (after) after();
    scheduleRefresh();
  });
}

function init() {
  ["S", "K", "T", "r", "sigma", "q", "steps", "expiry"].forEach((id) =>
    $(id).addEventListener("input", scheduleRefresh));

  wireSegment("seg-kind", "kind", () => {
    // Calls and puts are different chains, so the table has to be refetched.
    if (chainData) loadChain();
  });
  wireSegment("seg-style", "style");
  wireSegment("seg-tmode", "tmode", () => {
    const byDate = state.tmode === "date";
    $("T").hidden = byDate;
    $("expiry").hidden = !byDate;
    if (byDate && !$("expiry").value) {
      const d = new Date();
      d.setDate(d.getDate() + 365);
      $("expiry").value = d.toISOString().slice(0, 10);
    }
  });

  $("curve-pick").addEventListener("change", (e) => {
    state.curve = e.target.value;
    refreshCurves();
  });
  $("lat-steps").addEventListener("change", (e) => {
    state.latSteps = parseInt(e.target.value, 10);
    refreshLattice();
  });

  $("btn-fetch").addEventListener("click", fetchQuote);
  $("chain-expiry").addEventListener("change", loadChain);
  $("ticker").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fetchQuote();
  });

  $("btn-bench").addEventListener("click", () => {
    $("S").value = 100; $("K").value = 100; $("T").value = 1;
    $("r").value = 0.05; $("sigma").value = 0.20; $("q").value = 0;
    $("steps").value = 500;
    state.tmode = "years";
    $("T").hidden = false; $("expiry").hidden = true;
    document.querySelector(".quote-detail")?.remove();
    $("chain-section").hidden = true;
    chainData = null;
    $("quote-status").className = "hint";
    $("quote-status").textContent = "Pulls spot, realized volatility and dividend yield.";
    [...$("seg-tmode").querySelectorAll("button")].forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.val === "years")));
    scheduleRefresh();
  });

  document.querySelectorAll("nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll("nav button").forEach((b) =>
        b.setAttribute("aria-selected", String(b === btn)));
      $("tab-model").hidden = tab !== "model";
      $("tab-walk").hidden = tab !== "walk";
      if (tab === "walk") { loadVerify(); loadCodeBlocks(); }
    });
  });

  refreshAll();
}

document.addEventListener("DOMContentLoaded", init);
