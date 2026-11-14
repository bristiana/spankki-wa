/* Birky charts: draws an explore_my_data chart spec as inline SVG, no libraries.
   spec = {type: "line"|"bar", title, subtitle, unit: "€"|"", labels: [...], values: [...]}
   Colours come from the page's CSS variables (--brand, --ink, --muted, --line), so light and dark mode follow. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const DAYS = {Monday: "Mon", Tuesday: "Tue", Wednesday: "Wed", Thursday: "Thu", Friday: "Fri", Saturday: "Sat", Sunday: "Sun"};

  function num(v, unit, decimals) {
    const s = Number(v).toLocaleString("en-US", {minimumFractionDigits: decimals, maximumFractionDigits: decimals})
      .replace(/,/g, " ");
    return unit ? s + " " + unit : s;
  }
  function short(v, unit) {  // axis ticks: 1 500 € -> 1.5k €
    const s = Math.abs(v) >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + "k" : String(Math.round(v));
    return unit ? s + " " + unit : s;
  }
  function nice(max) {  // a round axis top: 1, 2, 2.5 or 5 x 10^n, with 4 steps
    if (max <= 0) return {top: 4, step: 1};
    const raw = max / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw);
    return {top: step * 4, step};
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
    const label = val => slot < 32 && val >= 1000 ? short(val, "") : num(val, "", 0);  // 1 307 -> 1.3k when tight
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    v.forEach((val, i) => {
      const h = Math.max(val ? 2 : 0, val / top * ph), cx = slot * i + slot / 2;
      svg.appendChild(el("rect", {x: cx - bw / 2, y: T + ph - h, width: bw, height: h, rx: 3,
                                  class: i === hi && val ? "kc-bar" : "kc-bar kc-soft"}));
      svg.appendChild(el("text", {x: cx, y: T + ph - h - 6, "text-anchor": "middle",
                                  class: i === hi ? "kc-val kc-strong" : "kc-val"}, label(val)));
      svg.appendChild(el("text", {x: cx, y: T + ph + 16, "text-anchor": "middle", class: "kc-axis"}, ticks[i][0]));
      if (ticks[i][1]) svg.appendChild(el("text", {x: cx, y: T + ph + 30, "text-anchor": "middle", class: "kc-axis"}, ticks[i][1]));
    });
    svg.appendChild(el("line", {x1: 0, x2: W, y1: T + ph, y2: T + ph, class: "kc-base"}));
  }

  function rows(svg, spec, W) {  // categories, places: one row each, label above its bar, value after it
    const v = spec.values, unit = spec.unit, row = 38, H = v.length * row + 4;
    const top = Math.max(...v) || 1, hi = v.indexOf(Math.max(...v)), room = 74, bw = W - room;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    v.forEach((val, i) => {
      const y0 = i * row, w = Math.max(val ? 2 : 0, val / top * bw);
      svg.appendChild(el("text", {x: 0, y: y0 + 13, class: "kc-label"}, spec.labels[i]));
      svg.appendChild(el("rect", {x: 0, y: y0 + 19, width: w, height: 12, rx: 3, class: i === hi && val ? "kc-bar" : "kc-bar kc-soft"}));
      svg.appendChild(el("text", {x: w + 6, y: y0 + 29.5, class: i === hi ? "kc-val kc-strong" : "kc-val"},
                         num(val, unit, unit && val < 100 ? 2 : 0)));
    });
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
    if (spec.type === "line" || (v.length > 13 && /^[A-Z][a-z]{2} \d{4}$/.test(spec.labels[0]))) line(svg, spec, W);
    else if (v.length <= 13 && spec.labels.every(l => tick(l, 0, spec.labels)[0].length <= 4)) columns(svg, spec, W);
    else rows(svg, spec, W);
    host.appendChild(svg);
  };
})();
