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
  cells: "price",
  surfaceView: "smile",
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

    clearImplied();
    status.className = "hint ok";
    status.textContent =
      `${q.ticker} ${q.spot.toFixed(2)} ${q.currency} · ${String(q.as_of).slice(0, 10)}`;

    scheduleRefresh();
  } catch (e) {
    status.className = "hint bad";
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------- implied volatility */

/* Type in a price from wherever the price is good, and solve for the
 * volatility that reproduces it. This replaced an option-chain browser fed by
 * a free feed's last-traded prices, which were stale on any thin strike and
 * produced volatilities in the wings that were noise. The weak link there was
 * the data, not the maths, so the data moved out and the maths stayed. */

/* A market price belongs to ONE contract. Loading a different one has to clear
 * it, or the panel keeps showing an implied vol solved for something else,
 * which is worse than showing nothing because it looks current. */
function clearImplied() {
  $("market-price").value = "";
  $("implied-status").textContent = "";
  $("implied-status").className = "hint";
}

async function solveImplied() {
  const raw = $("market-price").value.trim();
  const status = $("implied-status");
  if (!raw) { status.textContent = ""; status.className = "hint"; return; }

  const body = payload();
  body.market_price = parseFloat(raw);

  try {
    const d = await post("/api/implied", body);
    $("sigma").value = d.implied_vol.toFixed(8);

    // Show the precision, because it is not constant. Vega varies enormously
    // across a chain, so the same price resolution pins vol to 1e-12 at the
    // money and to a fraction of a point in the wings.
    const pts = d.uncertainty * 100;
    const precision = pts < 0.001 ? "" : ` ±${pts.toFixed(3)} pts`;
    status.className = "hint ok";
    status.textContent = `implied ${(d.implied_vol * 100).toFixed(3)}%${precision}`;
    await refreshAll();
  } catch (e) {
    status.className = "hint bad";
    status.textContent = e.message;
  }
}

/* --------------------------------------------------------- the vol surface */

let surfaceData = null;

async function buildSurface() {
  const text = $("surface-input").value.trim();
  const status = $("surface-status");
  if (!text) {
    surfaceData = null;
    $("surface-plot").innerHTML = "";
    $("surface-legend").innerHTML = "";
    status.textContent = "";
    status.className = "hint";
    return;
  }

  const body = payload();
  body.grid = text;
  body.cells_are = state.cells;

  try {
    surfaceData = await post("/api/surface", body);
    const d = surfaceData;
    status.className = "hint ok";
    status.textContent =
      `${d.inverted} of ${d.inverted + d.failed} cells` +
      (d.cells_are === "vol" ? " read as vols" : " inverted") +
      ` · ${d.expiries.length} expiries · ${d.strikes.length} strikes`;
    renderSurface();
  } catch (e) {
    surfaceData = null;
    $("surface-plot").innerHTML = "";
    $("surface-legend").innerHTML = "";
    status.className = "hint bad";
    status.textContent = e.message;
  }
}

function renderSurface() {
  const d = surfaceData;
  if (!d) return;
  const good = d.points.filter((p) => p.implied_vol !== null);
  if (!good.length) { $("surface-plot").innerHTML = ""; return; }

  let series;
  let opts;

  if (state.surfaceView === "smile") {
    // One line per expiry, vol against strike. This is the skew.
    series = d.expiries.map((label, i) => {
      const pts = good
        .filter((p) => p.expiry === label)
        .sort((a, b) => a.strike - b.strike)
        .map((p) => [p.strike, p.implied_vol * 100]);
      return { label, color: PALETTE[i % PALETTE.length], points: pts };
    }).filter((s) => s.points.length > 1);
    opts = { xlabel: "strike", ylabel: "implied vol %", markerX: d.spot, markerLabel: `spot ${fmt(d.spot, 2)}` };
  } else {
    // One line per strike, vol against time. This is the term structure.
    series = d.strikes.map((k, i) => {
      const pts = good
        .filter((p) => p.strike === k)
        .sort((a, b) => a.T - b.T)
        .map((p) => [p.T, p.implied_vol * 100]);
      return { label: String(k), color: PALETTE[i % PALETTE.length], points: pts };
    }).filter((s) => s.points.length > 1);
    opts = { xlabel: "years to expiry", ylabel: "implied vol %", xdp: 2 };
  }

  plot($("surface-plot"), series, opts);
  $("surface-legend").innerHTML = series
    .map((s) => `<span><i style="background:${s.color}"></i>${s.label}</span>`)
    .join("");

  const failed = d.points.filter((p) => p.implied_vol === null);
  const caption = $("surface-caption");
  const base = state.surfaceView === "smile"
    ? "Implied vol falling as strike rises is the <strong>skew</strong>. Black-Scholes cannot express it: it has one sigma per underlying, not one per strike."
    : "Vol against time at a fixed strike is the <strong>term structure</strong>. It usually slopes up, and inverts when a known event sits inside the near expiry.";
  caption.innerHTML = base + (failed.length
    ? ` <span class="flagged">${failed.length} cell${failed.length === 1 ? "" : "s"} could not be inverted and ${failed.length === 1 ? "is" : "are"} not plotted: ${failed[0].error}</span>`
    : "");
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
let impliedPending = null;
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
    if (surfaceData) await buildSurface();
  } catch (e) {
    $("tree-note").innerHTML = `<div class="err">${e.message}</div>`;
  }
}

function wireSegment(id, key, after, refresh = true) {
  const box = $(id);
  box.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    [...box.querySelectorAll("button")].forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    state[key] = btn.dataset.val;
    if (after) after();
    if (refresh) scheduleRefresh();
  });
}

function init() {
  ["S", "K", "T", "r", "sigma", "q", "steps", "expiry"].forEach((id) =>
    $(id).addEventListener("input", scheduleRefresh));

  wireSegment("seg-kind", "kind");
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

  let surfacePending = null;
  $("surface-input").addEventListener("input", () => {
    clearTimeout(surfacePending);
    surfacePending = setTimeout(buildSurface, 400);
  });
  $("btn-surface-demo").addEventListener("click", async () => {
    // Priced off the contract on screen, so it always inverts cleanly. A
    // hard-coded grid goes below intrinsic the moment the spot changes.
    try {
      const d = await post("/api/sample-grid", payload());
      $("surface-input").value = d.grid;
      $("seg-cells").querySelectorAll("button").forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.val === "price")));
      state.cells = "price";
      await buildSurface();
    } catch (e) {
      $("surface-status").className = "hint bad";
      $("surface-status").textContent = e.message;
    }
  });
  wireSegment("seg-cells", "cells", buildSurface, false);
  wireSegment("seg-surface-view", "surfaceView", renderSurface, false);
  $("market-price").addEventListener("input", () => {
    clearTimeout(impliedPending);
    impliedPending = setTimeout(solveImplied, 300);
  });
  $("ticker").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fetchQuote();
  });

  $("btn-bench").addEventListener("click", () => {
    $("S").value = 100; $("K").value = 100; $("T").value = 1;
    $("r").value = 0.05; $("sigma").value = 0.20; $("q").value = 0;
    $("steps").value = 500;
    state.tmode = "years";
    $("T").hidden = false; $("expiry").hidden = true;
      clearImplied();
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
