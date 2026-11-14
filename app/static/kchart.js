/* Oiva charts: draws an explore_my_data chart spec as inline SVG, no libraries.
   spec = {type: "line"|"bar"|"donut"|"pace", title, subtitle, unit: "€"|"", labels: [...], values: [...]}
   "pace" also takes budget, days (in the month) and today (index into values).
   Colours come from the page's CSS variables (--brand, --ink, --muted, --line), so light and dark mode follow. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const DAYS = {Monday: "Mon", Tuesday: "Tue", Wednesday: "Wed", Thursday: "Thu", Friday: "Fri", Saturday: "Sat", Sunday: "Sun"};

  const NB = "\u00a0", MINUS = "\u2212";
  function num(v, unit, decimals) {  // the S-mobiili app's style: 1 234,56 € (no-break spaces, decimal comma)
    const s = Math.abs(Number(v)).toLocaleString("en-US", {minimumFractionDigits: decimals, maximumFractionDigits: decimals})
      .replace(/,/g, NB).replace(".", ",");
    return (v < 0 ? MINUS : "") + (unit ? s + NB + unit : s);
  }
  function short(v, unit) {  // axis ticks: 1 500 € -> 1,5k €
    const a = Math.abs(v), s = a >= 1000 ? (a / 1000).toFixed(a % 1000 ? 1 : 0).replace(".", ",") + "k" : String(Math.round(a));
    return (v < 0 ? MINUS : "") + (unit ? s + NB + unit : s);
  }
  function nice(max) {  // a round axis top: 1, 2, 2.5 or 5 x 10^n, with 4 steps
    if (max <= 0) return {top: 4, step: 1};
    const raw = max / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw);
    return {top: step * 4, step};
  }
  function fit(text, px, perChar) {  // cut a label to about px wide, with an ellipsis
    const n = Math.max(3, Math.floor(px / perChar));
    return text.length > n ? text.slice(0, n - 1).trimEnd() + "\u2026" : text;
  }
  function el(name, attrs, text) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function tick(label, i, labels) {  // "Oct 2025" -> "Oct" (+ year under the first one and January)
    let m = /^([A-Z][a-z]{2}) (\d{4})$/.exec(label);
    if (m) return [m[1], i === 0 || m[1] === "Jan" ? m[2] : ""];
    m = /^Week of (\d+ [A-Z][a-z]{2}) (\d{4})$/.exec(label);
    if (m) return [m[1], i === 0 || label.slice(-4) !== labels[i - 1].slice(-4) ? m[2] : ""];
    return [DAYS[label] || label, ""];
  }

  function line(svg, spec, W) {
    const v = spec.values, n = v.length, unit = spec.unit, H = 210;
    const L = 46, R = 14, T = 22, B = 40, pw = W - L - R, ph = H - T - B;
    const {top, step} = nice(Math.max(...v));
    const x = i => L + (n === 1 ? pw / 2 : i * pw / (n - 1)), y = val => T + ph - val / top * ph;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    for (let t = 0; t <= top + 1e-9; t += step) {
      svg.appendChild(el("line", {x1: L, x2: W - R, y1: y(t), y2: y(t), class: t ? "kc-grid" : "kc-base"}));
      svg.appendChild(el("text", {x: L - 6, y: y(t) + 4, "text-anchor": "end", class: "kc-axis"}, short(t, unit)));
    }
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 38))));
    spec.labels.forEach((lab, i) => {
      if (i % every) return;
      const [a, b] = tick(lab, i, spec.labels);
      svg.appendChild(el("text", {x: x(i), y: H - B + 16, "text-anchor": "middle", class: "kc-axis"}, a));
      if (b) svg.appendChild(el("text", {x: x(i), y: H - B + 30, "text-anchor": "middle", class: "kc-axis"}, b));
    });
    const pts = v.map((val, i) => `${x(i).toFixed(1)},${y(val).toFixed(1)}`).join(" ");
    svg.appendChild(el("polygon", {points: `${x(0)},${y(0)} ${pts} ${x(n - 1)},${y(0)}`, class: "kc-area"}));
    svg.appendChild(el("polyline", {points: pts, class: "kc-line"}));
    const hi = v.indexOf(Math.max(...v));
    v.forEach((val, i) => {
      if (n <= 26 || i === hi) svg.appendChild(el("circle", {cx: x(i), cy: y(val), r: i === hi ? 4 : 2.6, class: "kc-dot"}));
    });
    const mark = (i, cls) => {
      const anchor = x(i) < L + 30 ? "start" : x(i) > W - R - 30 ? "end" : "middle";
      svg.appendChild(el("text", {x: x(i), y: y(v[i]) - 9, "text-anchor": anchor, class: cls}, num(v[i], unit, 0)));
    };
    if (v[hi] > 0) mark(hi, "kc-val kc-strong");
  }

  function columns(svg, spec, W) {  // weekdays, months: vertical bars, value on top, busiest one solid
    const v = spec.values, n = v.length, ticks = spec.labels.map((l, i) => tick(l, i, spec.labels));
    const years = ticks.some(t => t[1]), H = years ? 214 : 200, T = 22, B = years ? 40 : 26, ph = H - T - B;
    const top = Math.max(...v) || 1, slot = W / n, bw = Math.min(34, slot * 0.62), hi = v.indexOf(Math.max(...v));
    const label = val => slot < 32 && val >= 1000 ? short(val, "") : num(val, "", 0);  // 1 307 -> 1,3k when tight
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    if (slot < 32) svg.classList.add("kc-tight");  // 12 months on a phone: smaller labels that don't touch
    const inside = (cx, half) => Math.min(Math.max(cx, half), W - half);  // "2025" under the first bar stays on screen
    v.forEach((val, i) => {
      const h = Math.max(val ? 2 : 0, val / top * ph), cx = slot * i + slot / 2;
      svg.appendChild(el("rect", {x: cx - bw / 2, y: T + ph - h, width: bw, height: h, rx: 3,
                                  class: i === hi && val ? "kc-bar" : "kc-bar kc-soft"}));
      svg.appendChild(el("text", {x: inside(cx, 13), y: T + ph - h - 6, "text-anchor": "middle",
                                  class: i === hi ? "kc-val kc-strong" : "kc-val"}, label(val)));
      svg.appendChild(el("text", {x: cx, y: T + ph + 16, "text-anchor": "middle", class: "kc-axis"}, ticks[i][0]));
      if (ticks[i][1]) svg.appendChild(el("text", {x: inside(cx, 14), y: T + ph + 30, "text-anchor": "middle", class: "kc-axis"}, ticks[i][1]));
    });
    svg.appendChild(el("line", {x1: 0, x2: W, y1: T + ph, y2: T + ph, class: "kc-base"}));
  }

  function rows(svg, spec, W) {  // categories, places: one row each, label above its bar, value after it
    const v = spec.values, unit = spec.unit, row = 38, H = v.length * row + 4;
    const top = Math.max(...v) || 1, hi = v.indexOf(Math.max(...v)), room = 74, bw = W - room;
    const dec = unit && top < 100 ? 2 : 0;  // the same decimals on every row: 141 € next to 86 €, not 85,53 €
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    v.forEach((val, i) => {
      const y0 = i * row, w = Math.max(val ? 2 : 0, val / top * bw);
      svg.appendChild(el("text", {x: 0, y: y0 + 13, class: "kc-label"}, spec.labels[i]));
      svg.appendChild(el("rect", {x: 0, y: y0 + 19, width: w, height: 12, rx: 3, class: i === hi && val ? "kc-bar" : "kc-bar kc-soft"}));
      svg.appendChild(el("text", {x: w + 6, y: y0 + 29.5, class: i === hi ? "kc-val kc-strong" : "kc-val"},
                         num(val, unit, dec)));
    });
  }

  function donut(svg, spec, W) {  // where the money went: a ring, the total in the middle, a legend with amounts
    const col = i => (spec.colors && spec.colors[i] != null ? spec.colors[i] : i % 6);  // optional colour index per slice
    const v = spec.values, unit = spec.unit, sum = v.reduce((a, b) => a + Math.max(b, 0), 0) || 1;
    const R = W < 340 ? W / 5.2 : Math.min(78, W / 4), r = R * 0.62  /* a smaller ring in a narrow chat bubble: room for the names */, cx = R + 4, cy = R + 4, row = 22;
    const H = Math.max(2 * R + 8, v.length * row + 4);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let a0 = -Math.PI / 2;
    v.forEach((val, i) => {
      const part = Math.max(val, 0) / sum, a1 = a0 + part * 2 * Math.PI;
      if (part >= 0.9999) {  // one category is everything: a full ring
        svg.appendChild(el("circle", {cx, cy, r: (R + r) / 2, fill: "none", "stroke-width": R - r, class: "kc-arc kc-c" + col(i)}));
      } else if (part > 0) {
        const big = a1 - a0 > Math.PI ? 1 : 0, p = (rad, a) => `${(cx + rad * Math.cos(a)).toFixed(2)},${(cy + rad * Math.sin(a)).toFixed(2)}`;
        svg.appendChild(el("path", {d: `M${p(R, a0)} A${R},${R} 0 ${big} 1 ${p(R, a1)} L${p(r, a1)} A${r},${r} 0 ${big} 0 ${p(r, a0)} Z`,
                                    class: "kc-arc kc-c" + col(i)}));
      } else svg.appendChild(el("path", {d: "", class: "kc-arc kc-c" + col(i)}));  // keeps one arc per label
      a0 = a1;
      const lx = 2 * R + 22, ly = i * row + (H - v.length * row) / 2 + 15;
      svg.appendChild(el("rect", {x: lx, y: ly - 10, width: 11, height: 11, rx: 3, class: "kc-c" + col(i)}));
      const amount = num(val, unit, 0), room = W - (lx + 17) - amount.length * 7 - 8;  // ~7 px a character
      svg.appendChild(el("text", {x: lx + 17, y: ly, class: "kc-label"}, fit(spec.labels[i], room, 7)));
      svg.appendChild(el("text", {x: W, y: ly, "text-anchor": "end", class: "kc-val"}, amount));
    });
    svg.appendChild(el("text", {x: cx, y: cy + 1, "text-anchor": "middle", class: "kc-total", style: R < 70 ? "font-size:14.5px" : ""}, num(sum, unit, 0)));
    svg.appendChild(el("text", {x: cx, y: cy + 17, "text-anchor": "middle", class: "kc-axis"}, "in total"));
  }

  // spending so far this month (cumulative, one value per day so far) against an even pace up to the budget.
  // spec adds: budget (number), days (in the month), today (index of today in values)
  function pace(svg, spec, W) {
    const v = spec.values, unit = spec.unit, days = spec.days || 31, budget = spec.budget || 0, H = 190;
    const L = 46, R = 14, T = 18, B = 26, pw = W - L - R, ph = H - T - B;
    const {top, step} = nice(Math.max(budget, ...v));
    const x = d => L + (d / days) * pw, y = val => T + ph - val / top * ph;  // d = day number, 0 = start of month
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    for (let t = 0; t <= top + 1e-9; t += step) {
      svg.appendChild(el("line", {x1: L, x2: W - R, y1: y(t), y2: y(t), class: t ? "kc-grid" : "kc-base"}));
      svg.appendChild(el("text", {x: L - 6, y: y(t) + 4, "text-anchor": "end", class: "kc-axis"}, short(t, unit)));
    }
    [1, Math.round(days / 2), days].forEach(d => svg.appendChild(el("text", {x: x(d), y: H - 8, "text-anchor": d === 1 ? "start" : "middle",
                                                                            class: "kc-axis"}, d === 1 ? "day 1" : String(d))));
    if (budget) {
      svg.appendChild(el("line", {x1: x(0), y1: y(0), x2: x(days), y2: y(budget), class: "kc-pace"}));
    }
    const today = Math.min(spec.today == null ? v.length - 1 : spec.today, v.length - 1);
    const dayOf = i => Number(spec.labels[i]) || i + 1;
    const over = budget > 0 && v[today] > budget * dayOf(today) / days;
    const pts = [`${x(0)},${y(0)}`].concat(v.map((val, i) => `${x(dayOf(i)).toFixed(1)},${y(val).toFixed(1)}`)).join(" ");
    svg.appendChild(el("polyline", {points: pts, class: "kc-line" + (over ? " kc-over" : "")}));
    svg.appendChild(el("circle", {cx: x(dayOf(today)), cy: y(v[today]), r: 4.5, class: "kc-dot kc-today" + (over ? " kc-over" : "")}));
    // "412 € so far" in the first free spot next to the dot: clear of the even-pace line and of the solid line
    // (which only runs left of the dot, rising), on a halo for what is left
    const tx = x(dayOf(today)), ty = y(v[today]), label = num(v[today], unit, 0) + " so far", tw = label.length * 6.6;
    const paceY = px => y(budget * Math.min(Math.max((px - L) / pw, 0), 1));
    const spots = [["start", tx + 9, ty - 9], ["start", tx + 9, ty + 19], ["end", tx - 9, ty - 9], ["end", tx - 9, ty - 24]];
    const clear = ([a, sx, sy]) => {
      const x0 = a === "start" ? sx : sx - tw, x1 = x0 + tw, top = sy - 12, bottom = sy + 4;
      if (x0 < L || x1 > W - 2 || top < 2 || bottom > T + ph) return false;
      if (!budget) return true;
      const ys = [paceY(x0), paceY(x1)];  // a straight line: its height over the box is between these two
      return Math.max(...ys) < top || Math.min(...ys) > bottom;
    };
    const [anchor, lx, ly] = spots.find(clear) || spots[0];
    svg.appendChild(el("text", {x: lx, y: ly, "text-anchor": anchor, class: "kc-val kc-strong kc-halo"}, label));
    if (budget) svg.appendChild(el("text", {x: W - R, y: y(budget) - 7, "text-anchor": "end", class: "kc-axis kc-halo"},
                                   "budget " + num(budget, unit, 0)));
  }

  window.birkyChart = function (host, spec) {
    host.innerHTML = "";
    const W = Math.max(220, Math.floor(host.clientWidth || 300));
    const svg = el("svg", {class: "kc", role: "img", "aria-label": spec.title + ", " + spec.subtitle, width: W});
    svg.appendChild(el("title", {}, spec.title));
    const v = spec.values || [];
    if (!v.length || !v.some(x => x)) {
      const p = document.createElement("p"); p.className = "kc-empty"; p.textContent = "Nothing in this view yet.";
      host.appendChild(p); return;
    }
    if (spec.type === "donut") { donut(svg, spec, W); host.appendChild(svg); return; }
    if (spec.type === "pace") { pace(svg, spec, W); host.appendChild(svg); return; }
    if (spec.type === "line" || (v.length > 13 && /^[A-Z][a-z]{2} \d{4}$/.test(spec.labels[0]))) line(svg, spec, W);
    else if (v.length <= 13 && spec.labels.every(l => tick(l, 0, spec.labels)[0].length <= 4)) columns(svg, spec, W);
    else rows(svg, spec, W);
    host.appendChild(svg);
  };
})();
