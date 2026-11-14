// The seven Oiva charts on S-mobiili's Your money screen (Pankki -> Your money). Ported from the chart mockup
// (app/static/mockups/charts/index.html), drawn from the same fixtures (data-src on #money), built by
// scripts/build_chart_mockup_fixtures.py from Antti's own transactions and his real peer group.
// The words follow S-mobiili's FI/EN switch (the page's <html lang>); the figures are the same in both.
(async function () {
  "use strict";
  const HOST = document.getElementById("money");
  if (!HOST) return;
  const BASE = HOST.dataset.src;
  const FI = document.documentElement.lang === "fi", T = (fi, en) => FI ? fi : en;
  const NB = " ", MINUS = "−", NS = "http://www.w3.org/2000/svg";
  const get = n => fetch(BASE + n + ".json").then(r => { if (!r.ok) throw new Error(n + ".json: " + r.status); return r.json(); });
  const [spend, budget, compare, rec] = await Promise.all(["spend", "budget", "compare", "recurring"].map(get));

  // S-mobiili money: 1 234,56 €
  function eur(v, d = 2) {
    const a = Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d}).replace(/,/g, NB).replace(".", ",");
    return (v < 0 && Math.abs(v) >= 0.5 * 10 ** -d ? MINUS : "") + a + NB + "€";
  }
  function hero(v) {  // big whole euros, small cents (Revolut)
    const [w, c] = eur(v).replace(NB + "€", "").split(",");
    return `${w}<small>,${c}${NB}€</small>`;
  }
  const el = (n, a, t) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (t != null) e.textContent = t; return e; };
  const pct = v => Math.round(v * 100) + NB + "%";
  const sum = (a, f) => a.reduce((s, x) => s + f(x), 0);
  const list = (a, and) => a.length > 1 ? a.slice(0, -1).join(", ") + " " + and + " " + a[a.length - 1] : a[0];

  // dates, all from the data: as_of is "today", month is the month shown
  const ymd = s => { const [y, m, d] = s.split("-").map(Number); return {y, m, d}; };
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const FI_MONTH = ["tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu", "kesäkuu", "heinäkuu", "elokuu", "syyskuu", "lokakuu", "marraskuu", "joulukuu"];
  const asOf = ymd(spend.as_of), mon = ymd(spend.month + "-01");
  const DAYS = new Date(Date.UTC(mon.y, mon.m, 0)).getUTCDate(), TODAY = asOf.d;
  const LEFT_DAYS = DAYS - TODAY + 1;  // the screens count today as a day still to spend (oiva/data.js divides by 26)
  const M = MON[mon.m - 1], MONTH_NAME = MONTH[mon.m - 1], FI_M = FI_MONTH[mon.m - 1];
  const dm = d => FI ? `${d}.${mon.m}.` : `${d} ${M}`;  // a day of this month: 6.9. / 6 Sep
  const dayOf = s => { const t = ymd(s); return t.y === mon.y && t.m === mon.m ? t.d : null; };  // a date's day, if it is in this month

  // category look: fixed colour per category (follows the entity, as on today's donut), plus a glyph
  const ICON = {
    Groceries: '<path d="M4 7h16l-1.5 10h-13z"/><path d="M8 7l3-4M16 7l-3-4"/>',
    Restaurants: '<path d="M7 3v8M5 3v5a2 2 0 0 0 4 0V3M7 11v10M16 3c-2 1-3 4-3 7h3v11"/>',
    Lifestyle: '<path d="M6 8h12l-1 12H7z"/><path d="M9 8a3 3 0 0 1 6 0"/>',
    Transportation: '<rect x="6" y="3" width="12" height="14" rx="3"/><path d="M6 11h12M9 20l-1.5 1.5M15 20l1.5 1.5"/><circle cx="9" cy="14" r=".6"/><circle cx="15" cy="14" r=".6"/>',
    Entertainment: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M10 9.5v5l4.5-2.5z"/>',
    "Home & Tech": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    Health: '<path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10z"/>',
    Travel: '<path d="M3 13l18-7-5 14-3-6z"/>'
  };
  const COL = {Groceries: "c0", Restaurants: "c1", Lifestyle: "c2", Transportation: "c3", Entertainment: "c4", "Home & Tech": "c5", Health: "c6", Travel: "c7"};
  const CAT_FI = {Groceries: "Ruoka", Restaurants: "Ravintolat", Lifestyle: "Ostokset", Transportation: "Liikkuminen",
    Entertainment: "Viihde", "Home & Tech": "Koti ja tekniikka", Health: "Terveys", Travel: "Matkailu"};
  const lab = c => FI ? CAT_FI[c.category] || c.label : c.label;  // a category's name, in the screen's language
  const css = v => getComputedStyle(HOST).getPropertyValue("--" + v).trim();
  const lum = hex => { const n = parseInt(hex.slice(1), 16), c = [n >> 16, n >> 8 & 255, n & 255].map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; };
  const inkOn = hex => lum(hex) > .2 ? css("glyph-dark") : css("glyph-light");  // glyph colour that reads on a fill, in either theme
  const icon = cat => { const c = css(COL[cat]);
    return `<span class="ico" style="background:${c}"><svg viewBox="0 0 24 24" style="stroke:${inkOn(c)}">${ICON[cat] || ""}</svg></span>`; };
  const warnChip = t => `<span class="chip warn"><svg viewBox="0 0 12 12"><path d="M6 2l4 7H2z" fill="currentColor"/></svg>${t}</span>`;

  // what the data says, and where it disagrees with itself (shown, not smoothed over)
  const P = rec.regular_payments, by = id => P.find(p => p.id === id);
  const B = spend.budget_total_eur, catSet = budget.items.filter(i => i.limit_eur != null);
  const catLim = sum(catSet, i => i.limit_eur);
  const afterBills = by("salary").per_month_eur - rec.bills_per_month_eur - by("investing").per_month_eur;
  const span = c => { const f0 = ymd(c.from), t0 = ymd(c.to);
    return FI ? `${f0.m}/${f0.y}–${t0.m}/${t0.y}` : `${MON[f0.m - 1]}${f0.y !== t0.y ? " " + f0.y : ""}–${MON[t0.m - 1]} ${t0.y}`; };
  const AFTER = T("Jäljellä laskujen ja sijoittamisen jälkeen", "Left after bills and investing");  // card 6's name, which cards 5 and 7 point to
  const PAYMENTS = T(`${FI_M[0].toUpperCase() + FI_M.slice(1)}n säännölliset maksut`, `${MONTH_NAME}'s regular payments`);  // card 7's name
  function budgetRule() {  // the rule in budget.json, said from its own numbers
    const hm = Object.keys(spend.categories[0].history_eur), a = ymd(hm[0] + "-01"), z = ymd(hm[hm.length - 1] + "-01");
    const none = spend.categories.filter(c => c.budget_eur == null).map(lab);
    if (FI) return `Jokaisen luokan budjetti on sen mediaanikuukausi edeltävältä ${hm.length} täydeltä kuukaudelta (${a.m}–${z.m}/${z.y}), pyöristettynä lähimpään 10${NB}euroon` +
      (none.length ? `. ${none.length > 1 ? "Luokkien " + list(none, "ja") : "Luokan " + none[0]} mediaani oli 0${NB}€, joten ${none.length > 1 ? "niillä" : "sillä"} ei ole budjettia` : "") +
      `. Kokonaisbudjetti on luokkabudjettien summa, ${eur(catLim, 0)}, eli vähemmän kuin laskujen ja sijoittamisen jälkeen jäävä ${eur(afterBills)}.`;
    return `Each category's budget is its median month over the ${hm.length} full months before (${MON[a.m - 1]}–${MON[z.m - 1]} ${z.y}), rounded to the nearest 10${NB}€` +
      (none.length ? `; ${list(none, "and")} had a median of 0${NB}€, so ${none.length > 1 ? "they have" : "it has"} none` : "") +
      `. The overall budget is their sum, ${eur(catLim, 0)}, under the ${eur(afterBills)} left after bills and investing.`;
  }
  const C = compare.cohort, web = id => compare.web.find(w => w.id === id);
  const AGE = C.age_band.replace("-", "–");
  const PLACE = FI ? C.city.replace(/^the (.+) area$/, "$1n alueella") : "in " + C.city;  // "the Vantaa area": Vantaan alueella
  const asOfEl = document.getElementById("money-asof");
  if (asOfEl) asOfEl.textContent = asOfEl.textContent.replace("{date}", FI ? `${TODAY}.${mon.m}.${asOf.y}` : `${TODAY}${NB}${MONTH_NAME} ${asOf.y}`).replace("{n}", compare.cohort.n);

  function render() {
  /* 1 · spending this month */
  (function () {
    const host = document.getElementById("trend");
    const daily = spend.daily, spent = spend.total_eur;
    const ahead = spend.pace.spent_by_today_eur - spend.pace.expected_by_today_eur;
    const proj = spent / TODAY * DAYS, left = B - spent;
    const projCol = proj > B ? css("warn") : css("brand");
    const chip = spend.pace.status === "over" ? warnChip(T(`${eur(ahead)} yli tasaisen tahdin`, `${eur(ahead)} over an even pace`))
      : `<span class="chip ok">${T(`${eur(-ahead)} alle tasaisen tahdin`, `${eur(-ahead)} under an even pace`)}</span>`;
    host.innerHTML = `<p class="k">${T(`Kulutus ${FI_M}ssa tähän mennessä, kaikki luokat`, `Spending in ${MONTH_NAME} so far, all categories`)}</p><p class="hero">${hero(spent)}</p>
      ${chip}
      <p class="sub">${T(`<b>${eur(left)}</b> jäljellä ${eur(B, 0).replace("€", "euron")} budjetistasi (luokkabudjettien summa) · <b>${eur(left / LEFT_DAYS)}</b> päivässä ${LEFT_DAYS} päivän ajan`,
        `<b>${eur(left)}</b> left of your ${eur(B, 0)} budget (the sum of the category budgets) · <b>${eur(left / LEFT_DAYS)}</b> a day for ${LEFT_DAYS} days`)}</p><div class="chart-gap"></div>`;
    const W = 318, H = 176, L = 2, R = 46, T0 = 22, Bm = 22, pw = W - L - R, ph = H - T0 - Bm;
    const top = Math.ceil(Math.max(proj, B) * 1.08 / 500) * 500, step = top > 2500 ? 1000 : 500;  // axis room above the higher of budget and projection
    const x = d => L + d / DAYS * pw, y = v => T0 + ph - v / top * ph;  // d = day, 0 = start of the month
    const svg = el("svg", {class: "ch", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": T(
      `Käytetty ${eur(spent)} ${dm(TODAY)} mennessä; tällä tahdilla ${eur(proj, 0)} kuun loppuun mennessä, budjetti ${eur(B, 0)}`,
      `Spent ${eur(spent)} by ${TODAY} ${MONTH_NAME}; at this rate ${eur(proj, 0)} by the end of the month, budget ${eur(B, 0)}`)});
    const defs = el("defs", {}); defs.innerHTML = `<linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${css("bright")}" stop-opacity=".28"/><stop offset="1" stop-color="${css("bright")}" stop-opacity="0"/></linearGradient>`;
    svg.appendChild(defs);
    for (let t = 0; t <= top; t += step) {
      svg.appendChild(el("line", {x1: L, x2: L + pw, y1: y(t), y2: y(t), stroke: css("line"), "stroke-width": 1}));
      svg.appendChild(el("text", {x: W, y: y(t) + 3.5, "text-anchor": "end"}, eur(t, 0)));
    }
    // budget: a target line across the month
    svg.appendChild(el("line", {x1: L, x2: L + pw, y1: y(B), y2: y(B), stroke: css("strong"), "stroke-width": 1.2, "stroke-dasharray": "3 3"}));
    const budLab = svg.appendChild(el("text", {x: L, y: y(B) - 5, style: "font-size:12.5px;fill:" + css("strong") + ";font-weight:600"}, T("Budjetti ", "Budget ") + eur(B, 0)));
    // projection at this rate: dotted
    svg.appendChild(el("line", {x1: x(TODAY), y1: y(spent), x2: x(DAYS), y2: y(proj), stroke: projCol, "stroke-width": 2, "stroke-dasharray": "1 4", "stroke-linecap": "round"}));
    const projLab = svg.appendChild(el("text", {x: x(DAYS) - 6, y: y(proj) - 7, "text-anchor": "end", style: "font-size:12.5px;fill:" + projCol + ";font-weight:600"}, T("Tällä tahdilla ", "At this rate ") + eur(proj, 0)));
    // this month, solid with a soft fill
    const pts = [[0, 0]].concat(daily.map(d => [d.day, d.cumulative_eur]));
    const path = pts.map((p, i) => (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1)).join("");
    svg.appendChild(el("path", {d: path + `L${x(TODAY)},${y(0)}L${x(0)},${y(0)}Z`, fill: "url(#g1)"}));
    svg.appendChild(el("path", {d: path, fill: "none", stroke: css("brand"), "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round"}));
    svg.appendChild(el("circle", {cx: x(TODAY), cy: y(spent), r: 9, fill: css("bright"), opacity: .18}));
    svg.appendChild(el("circle", {cx: x(TODAY), cy: y(spent), r: 4.5, fill: css("brand"), stroke: css("card"), "stroke-width": 2}));
    const ticks = [[0, dm(1)], [TODAY, T("Tänään", "Today")], [DAYS, dm(DAYS)]];
    if (Math.abs(15 - TODAY) > 3) ticks.splice(2, 0, [15, "15"]);
    ticks.forEach(([d, t]) =>
      svg.appendChild(el("text", {x: x(d), y: H - 4, "text-anchor": d === 0 ? "start" : d === DAYS ? "end" : "middle", style: d === TODAY ? "fill:" + css("strong") + ";font-weight:600" : ""}, t)));
    // scrub: drag to read a day
    const cross = el("line", {y1: T0 - 6, y2: T0 + ph, stroke: css("strong"), "stroke-width": 1, opacity: 0});
    const dot = el("circle", {r: 4, fill: css("brand"), stroke: css("card"), "stroke-width": 2, opacity: 0});
    svg.append(cross, dot);
    host.appendChild(svg);
    const tip = document.createElement("div"); tip.className = "tip"; host.appendChild(tip);
    const move = e => {
      const r = svg.getBoundingClientRect(), sx = (e.clientX - r.left) * W / r.width;
      const d = Math.max(1, Math.min(DAYS, Math.round((sx - L) / pw * DAYS)));
      const known = daily.find(p => p.day === d), v = known ? known.cumulative_eur : spent / TODAY * d;
      cross.setAttribute("x1", x(d)); cross.setAttribute("x2", x(d)); cross.setAttribute("opacity", .35);
      dot.setAttribute("cx", x(d)); dot.setAttribute("cy", y(v)); dot.setAttribute("opacity", 1);
      dot.setAttribute("fill", known ? css("brand") : projCol);
      tip.style.display = "block"; tip.textContent = `${dm(d)} · ${eur(v, 0)}${known ? "" : T(" tällä tahdilla", " at this rate")}`;
      const hr = host.getBoundingClientRect(), half = tip.offsetWidth / 2;  // svg has no offsetLeft: measure against the card
      tip.style.left = Math.min(Math.max(r.left - hr.left + x(d) * r.width / W, half + 4), hr.width - half - 4) + "px";
      const dy = r.top - hr.top + y(v) * r.height / H, th = tip.offsetHeight;  // above the dot, or below it when there is no room
      // nor when it would sit on the Today dot (with its halo): then below, where the line is lower
      const sc = r.width / W, tx = r.left - hr.left + x(TODAY) * sc, ty = r.top - hr.top + y(spent) * sc, tr0 = 9 * sc + 2;
      const lx = parseFloat(tip.style.left), onToday = b => d !== TODAY && lx - half < tx + tr0 && lx + half > tx - tr0 && b - th < ty + tr0 && b > ty - tr0;
      const above = dy - 12, below = dy + 12 + th;
      tip.style.top = (above - th > r.top - hr.top && !(onToday(above) && !onToday(below)) ? above : below) + "px";
      const tr = tip.getBoundingClientRect();  // a label under the tooltip steps back while it is there
      [budLab, projLab].forEach(t => { const b = t.getBoundingClientRect();
        t.style.opacity = b.right > tr.left && b.left < tr.right && b.bottom > tr.top && b.top < tr.bottom ? 0 : 1; });
    };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", () => { tip.style.display = "none"; cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); budLab.style.opacity = projLab.style.opacity = 1; });
  })();

  /* 2 · where it went */
  (function () {
    const host = document.getElementById("cats"), cats = spend.categories, total = sum(cats, c => c.spent_eur);
    const used = cats.filter(c => c.spent_eur > 0), none = cats.filter(c => !(c.spent_eur > 0));
    host.innerHTML = `<p class="h3">${T("Mihin rahat menivät", "Where it went")}</p><p class="k" style="margin-top:2px">${T(`${eur(total)}, ${used.length} luokkaa`, `${eur(total)} across ${used.length} categories`)}</p>
      <div class="seg" role="img" aria-label="${T("Kulutuksen jakautuminen luokittain", "Share of spending by category")}">${used.map(c =>
        `<i style="flex:${c.spent_eur};background:${css(COL[c.category])}" title="${lab(c)} ${eur(c.spent_eur)}"></i>`).join("")}</div>
      <div>${used.map(c => `<div class="row">${icon(c.category)}<div class="mid"><div class="nm">${lab(c)}</div>
        <div class="sm">${pct(c.spent_eur / total)} · ${T("viime kuussa", "last month")} ${eur(c.prev_eur, 0)}</div></div>
        <div class="amt">${eur(c.spent_eur)}</div></div>`).join("")}</div>
      ${none.length ? `<p class="nob">${T(`Ei vielä kulutusta: ${list(none.map(lab), "sekä")}.`, `Nothing yet on ${list(none.map(lab), "or")}.`)}</p>` : ""}`;
  })();

  /* 3 · budgets and pace */
  (function () {
    const host = document.getElementById("budget"), items = budget.items, paceShare = TODAY / DAYS;
    const set = items.filter(i => i.limit_eur != null), unset = items.filter(i => i.limit_eur == null);
    const lim = sum(set, i => i.limit_eur), sp = sum(set, i => i.spent_eur), left = sum(set, i => i.left_eur);
    // status is the data's own (over / on_track = not over); pace is computed: spent share vs share of the month gone
    const fast = i => i.limit_eur > 0 && i.spent_eur / i.limit_eur > paceShare;
    // one short line per bar: the data's "over" first, then the computed pace
    const word = i => i.status === "over" ? ["over", T("Budjetti ylitetty", "Over budget")] : i.spent_eur === 0 ? ["ok", T("Ei vielä kulutusta", "Nothing spent yet")]
      : fast(i) ? ["fast", T("Tasaista tahtia nopeammin", "Faster than even pace")] : ["ok", T("Tasaisen tahdin sisällä", "Within even pace")];
    const nWithin = set.filter(i => i.status !== "over").length;
    const nFast = set.filter(i => i.status !== "over" && fast(i)).length;
    host.innerHTML = `<p class="k">${T(`Käytettävissä ${set.length} luokkabudjetissasi`, `Left to spend in your ${set.length} category budgets`)}</p><p class="hero">${hero(left)}</p>
      <p class="sub">${T(`<b>${eur(Math.max(left, 0) / LEFT_DAYS)}</b> päivässä ${LEFT_DAYS} päivän ajan · käytetty ${eur(sp)} / ${eur(lim, 0)}`,
        `<b>${eur(Math.max(left, 0) / LEFT_DAYS)}</b> a day for ${LEFT_DAYS} days · ${eur(sp)} of ${eur(lim, 0)} spent`)}</p>
      <div class="chips"><span class="chip ok">${T(`${nWithin}/${set.length} budjetin sisällä`, `${nWithin} of ${set.length} within budget`)}</span>
      ${nFast ? warnChip(T(`${nFast}/${set.length} tasaista tahtia nopeammin`, `${nFast} of ${set.length} faster than an even pace`)) : ""}</div>
      <div style="margin-top:12px">${set.map(i => { const over = i.status === "over", f = Math.min(i.spent_eur / i.limit_eur, 1);
        const fill = over || fast(i) ? css("warn") : css("bright");
        return `<div class="row">${icon(i.category)}<div class="mid">
          <div style="display:flex;justify-content:space-between;gap:8px"><span class="nm">${lab(i)}</span><span class="nm" style="font-weight:700;color:var(--brand);white-space:nowrap">${eur(i.left_eur)} <span style="font-weight:400;color:var(--muted);font-size:12.5px">${T("jäljellä", "left")}</span></span></div>
          <div class="track"><i style="width:${(f * 100).toFixed(1)}%;background:${fill}"></i><b style="left:${(paceShare * 100).toFixed(1)}%"></b></div>
          <div style="display:flex;justify-content:space-between;gap:8px;font-size:12.5px;color:var(--muted)"><span class="st ${word(i)[0]}">${word(i)[1]}</span><span style="white-space:nowrap">${eur(i.spent_eur)} ${T("/", "of")} ${eur(i.limit_eur, 0)}</span></div>
        </div></div>`; }).join("")}</div>
      <div class="key"><span><i style="background:var(--strong);width:2px;height:12px;border-radius:1px"></i>${T("Tasainen tahti tähän päivään", "Even pace by today")}</span>${set.some(i => i.spent_eur > 0 && !fast(i)) ? `<span><i style="background:var(--bright)"></i>${T("Tasaisen tahdin sisällä", "Within even pace")}</span>` : ""}${set.some(fast) ? `<span><i style="background:var(--warn)"></i>${T("Tasaista tahtia nopeammin", "Faster than even pace")}</span>` : ""}</div>
      ${unset.length ? `<p class="nob">${T("Ei budjettia", "No budget")}: ${unset.map(i => `${lab(i)} (${T("käytetty", "spent")} ${eur(i.spent_eur, 0)})`).join(" · ")}</p>` : ""}
      <p class="sub">${T("Näin budjetit asetetaan", "How the budgets are set")}: ${budgetRule()}</p>`;
  })();

  /* 4 · where you sit */
  (function () {
    const host = document.getElementById("sit"), F = compare.figures;
    const FIG_FI = {income: "Tulot", rent_share: "Asumisen osuus tuloista", savings_rate: "Tuloista jäi säästöön", restaurants: "Ravintolat",
      shopping: "Ostokset", buffer_months: "Puskuri"};
    const UNIT = {months: T("kk", "mo")};
    const fmt = f => f.unit === "eur" ? eur(f.you, 0) : f.unit === "pct" ? (f.you < 0 ? MINUS : "") + (Math.abs(f.you) < .01 ? Math.abs(f.you * 100).toFixed(1).replace(".", ",") : Math.abs(Math.round(f.you * 100))) + NB + "%" : String(f.you).replace(".", ",") + NB + (UNIT[f.unit] || f.unit);
    // warn only when "better" has a direction and you are outside the middle half on the wrong side
    const worse = f => (f.better === "higher" && f.percentile < 25) || (f.better === "lower" && f.percentile > 75);
    const W = 318, rowH = 40, H = F.length * rowH + 26, x0 = 6, x1 = W - 76, px = p => x0 + p / 100 * (x1 - x0);
    const svg = el("svg", {class: "ch", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": T(`Sijaintisi ryhmässä ${F.length} mittarilla`, `Your percentile in the group for ${F.length} figures`)});
    F.forEach((f, i) => {
      const yb = i * rowH + 30;
      svg.appendChild(el("text", {x: x0, y: yb - 10, style: "font-size:12.5px;fill:" + css("strong")}, FI ? FIG_FI[f.id] || f.label : f.label));
      svg.appendChild(el("line", {x1: px(0), x2: px(100), y1: yb, y2: yb, stroke: css("line"), "stroke-width": 6, "stroke-linecap": "round"}));
      svg.appendChild(el("line", {x1: px(25), x2: px(75), y1: yb, y2: yb, stroke: css("brand"), "stroke-opacity": .4, "stroke-width": 6}));
      const bad = worse(f);
      svg.appendChild(el("circle", {cx: px(f.percentile), cy: yb, r: 6.5, fill: bad ? css("warn") : css("brand"), stroke: css("card"), "stroke-width": 2}));
      svg.appendChild(el("line", {x1: px(50), x2: px(50), y1: yb - 7, y2: yb + 7, stroke: css("strong"), "stroke-width": 1.5}));  // typical, drawn over the dot
      svg.appendChild(el("text", {x: W, y: yb + 4, "text-anchor": "end", style: "font-size:12.5px;font-weight:600;fill:" + (bad ? css("warn") : css("strong"))}, fmt(f)));
    });
    const yb = F.length * rowH + 18;
    [[0, T("alin", "lowest")], [50, T("tyypillinen", "typical")], [100, T("korkein", "highest")]].forEach(([p, t]) =>
      svg.appendChild(el("text", {x: px(p), y: yb, "text-anchor": p ? (p === 100 ? "end" : "middle") : "start"}, t)));
    host.innerHTML = `<p class="h3">${T("Sinä ja kaltaisesi", "Where you sit")}</p><p class="k" style="margin-top:2px">${T(
      `${AGE}-vuotiaat ${PLACE} (${C.n} henkeä), kuukausikeskiarvot <span style="white-space:nowrap">${span(C)}</span>`,
      `Among ${C.n} people aged ${AGE} ${PLACE}, monthly averages <span style="white-space:nowrap">${span(C)}</span>`)}</p>`;
    host.appendChild(svg);
    host.insertAdjacentHTML("beforeend", `<div class="key"><span><i style="background:var(--brand);width:10px;height:10px"></i>${T("Sinä", "You")}</span><span><i style="background:var(--strong);width:2px;height:12px;border-radius:1px"></i>${T("Tyypillinen", "Typical")}</span><span><i style="background:var(--brand);opacity:.4"></i>${T("Keskimmäinen puolikas", "Middle half")}</span>${F.some(worse) ? `<span><i style="background:var(--warn);width:10px;height:10px"></i>${T("Keskimmäisen puolikkaan ulkopuolella, huonommalla puolella", "Outside the middle half, on the worse side")}</span>` : ""}</div>`);
  })();

  /* 5 · one figure: Income */
  (function () {
    const host = document.getElementById("fig"), f = compare.figures.find(f => f.id === "income");
    const lo = f.p25 - (f.p75 - f.p25) * .6, hi = f.p75 + (f.p75 - f.p25) * .6;  // scale only: room either side of the middle half
    const W = 318, H = 126, x0 = 8, x1 = W - 8, px = v => x0 + (Math.min(Math.max(v, lo), hi) - lo) / (hi - lo) * (x1 - x0), yb = 64;
    const svg = el("svg", {class: "ch", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": T(
      `Tulosi ${eur(f.you)}, tyypillinen ${eur(f.median)}, keskimmäinen puolikas ${eur(f.p25, 0)}–${eur(f.p75, 0)}`,
      `Your income ${eur(f.you)}, typical ${eur(f.median)}, middle half ${eur(f.p25, 0)} to ${eur(f.p75, 0)}`)});
    svg.appendChild(el("line", {x1: x0, x2: x1, y1: yb, y2: yb, stroke: css("line"), "stroke-width": 4, "stroke-linecap": "round"}));
    svg.appendChild(el("line", {x1: px(f.p25), x2: px(f.p75), y1: yb, y2: yb, stroke: css("brand"), "stroke-opacity": .4, "stroke-width": 12}));
    svg.appendChild(el("line", {x1: px(f.median), x2: px(f.median), y1: yb - 8, y2: yb + 34, stroke: css("strong"), "stroke-width": 1.5}));
    svg.appendChild(el("text", {x: px(f.median), y: yb + 48, "text-anchor": "middle", style: "font-size:12.5px;font-weight:600;fill:" + css("strong")}, T("Tyypillinen ", "Typical ") + eur(f.median, 0)));
    const ux = px(f.you);
    svg.appendChild(el("line", {x1: ux, x2: ux, y1: yb - 30, y2: yb, stroke: css("brand"), "stroke-width": 1.5}));
    svg.appendChild(el("circle", {cx: ux, cy: yb, r: 7, fill: css("brand"), stroke: css("card"), "stroke-width": 2.5}));
    const bubble = T("Sinä ", "You ") + eur(f.you, 0), bw = bubble.length * 6.8 + 16, bx = Math.min(Math.max(ux - bw / 2, 0), W - bw);
    svg.appendChild(el("rect", {x: bx, y: yb - 54, width: bw, height: 24, rx: 12, fill: css("brand")}));
    svg.appendChild(el("text", {x: bx + bw / 2, y: yb - 38, "text-anchor": "middle", style: "font-size:12.5px;font-weight:700;fill:" + css("brand-ink")}, bubble));
    [[f.p25, eur(f.p25, 0)], [f.p75, eur(f.p75, 0)]].forEach(([v, t]) =>
      svg.appendChild(el("text", {x: px(v), y: yb + 24, "text-anchor": "middle"}, t)));
    host.innerHTML = `<p class="h3">${T("Tulot", f.label)}</p><p class="k" style="margin-top:2px">${T(
      `Palkka ja muut tulot kuukaudessa, ${span(C)}, verrattuna ${PLACE.replace(/n alueella$/, "n alueen")} ${AGE}-vuotiaisiin (${C.n} henkeä)`,
      `Salary and other income a month, ${span(C)}, against ${C.n} people aged ${AGE} ${PLACE}`)}</p>`;
    host.appendChild(svg);
    const wv = web("income_vantaa_all_adults"), wf = web("income_finland_35_49"), sal = eur(by("salary").per_month_eur, 0);
    host.insertAdjacentHTML("beforeend", FI
      ? `<p class="sub">Tulosi ovat suuremmat kuin noin <b>${Math.round(f.percentile / 10)}:llä kymmenestä</b> ryhmän jäsenestä. Paksu palkki on keskimmäinen puolikas.</p>
      <p class="sub">Tilastokeskus, eri mittari (kaikki käytettävissä olevat rahatulot henkeä kohden, ei vain tälle tilille tuleva): kaikkien aikuisten mediaani Vantaalla ${eur(wv.value_eur_year / 12, 0)} kuukaudessa (<a href="${wv.url}">taulukko 14ww</a>); 35–49-vuotiailla koko Suomessa ${eur(wf.value_eur_year / 12, 0)} kuukaudessa (<a href="${wf.url}">taulukko 11py</a>), molemmat vuodelta ${wf.year}, kahdestoistaosa vuoden luvusta. Mikään taulukko ei anna ikää ja aluetta yhdessä. Kortin ”${AFTER}” ${sal} on pelkkä palkka, kolmen viimeisen palkan keskiarvo.</p>`
      : `<p class="sub">Higher than about <b>${Math.round(f.percentile / 10)} in 10</b> people in the group. The thick band is the middle half.</p>
      <p class="sub">Statistics Finland, a different measure (all disposable cash income per person, not only what reaches this account): median for all adults in Vantaa ${eur(wv.value_eur_year / 12, 0)} a month (<a href="${wv.url}">table 14ww</a>); for ages 35–49 in all of Finland ${eur(wf.value_eur_year / 12, 0)} a month (<a href="${wf.url}">table 11py</a>), both ${wf.year}, a twelfth of the yearly figure. No table gives age and area together. The ${sal} in “${AFTER}” is salary only, the average of the last 3 payments.</p>`);
  })();

  /* 6 · where the salary goes */
  (function () {
    const host = document.getElementById("income");
    const rent = by("rent"), u = by("utilities");
    const inc = by("salary").per_month_eur, bills = rec.bills_per_month_eur, inv = by("investing").per_month_eur, rest = inc - bills - inv;
    // the bills are each one's average month (recurring.json's method), so they differ from this month's payments in card 7
    const billSub = T(`vuokra ${eur(rent.per_month_eur)} + asumislaskut ${eur(u.per_month_eur)}`, `rent ${eur(rent.per_month_eur)} + utility bills ${eur(u.per_month_eur)}`);
    const parts = [[T("Laskut: vuokra ja asumislaskut", "Bills: rent and utility bills"), bills, css("c6"), billSub],
      [T("Sijoittaminen", "Investing"), inv, css("c3")], [T("Jää kaikkeen muuhun", "Left for everything else"), rest, css("brand")]];
    const paid = spend.bills.find(b => /rent/i.test(b.name) && dayOf(b.date));
    host.innerHTML = `<p class="h3">${AFTER}</p><p class="k" style="margin-top:2px">${T("Tavallinen kuukausi", "A typical month")}</p><p class="hero">${hero(rest)}</p>
      <p class="sub">${T(`palkastasi <b>${eur(inc)}</b> kuukaudessa (kolmen viimeisen keskiarvo)`, `of your <b>${eur(inc)}</b> salary a month (average of the last 3)`)}</p>
      <div class="seg" style="height:18px" role="img" aria-label="${T("Palkan jakautuminen", "Salary split")}">${parts.map(p => `<i style="flex:${p[1]};background:${p[2]}"></i>`).join("")}</div>
      ${parts.map(p => `<div class="row" style="padding:7px 0"><span style="width:12px;height:12px;border-radius:3px;background:${p[2]};flex:none"></span>
        <div class="mid"><div class="nm">${p[0]}</div>${p[3] ? `<div class="sm">${p[3]}</div>` : ""}</div><div class="amt">${eur(p[1])}<small>${pct(p[1] / inc)}</small></div></div>`).join("")}
      <p class="sub">${T(`Tavallinen kuukausi: palkka ja vuokra ovat kolmen viimeisen maksun keskiarvoja, asumislaskut kolmen viimeisen täyden kuukauden keskiarvo.`,
        `A typical month: salary and rent are the average of their last 3 payments, utility bills of the last 3 full months.`)}${paid ? T(
        ` ${FI_M[0].toUpperCase() + FI_M.slice(1)}n vuokra oli ${eur(paid.amount_eur)} (kortti ”${PAYMENTS}”).`,
        ` ${MONTH_NAME}'s rent was ${eur(paid.amount_eur)} (see “${PAYMENTS}”).`) : ""}</p>`;
  })();

  /* 7 · the month's payments */
  (function () {
    const host = document.getElementById("cal"), u = by("utilities"), rentPaid = spend.bills.find(b => /rent/i.test(b.name) && dayOf(b.date));
    const paidHere = re => spend.bills.filter(b => re.test(b.name) && dayOf(b.date)).map(b => dayOf(b.date));
    const due = p => (p.next_expected && dayOf(p.next_expected)) || paidHere(new RegExp(p.name, "i"))[0] || p.usual_day;
    const lanes = [
      {name: T("Vuokra", "Rent"), sub: rentPaid ? eur(rentPaid.amount_eur) : eur(by("rent").per_month_eur) + T(" kuukaudessa", " a month"), day: due(by("rent"))},  // the amount paid this month, per spend.json bills
      {name: T("Asumislaskut", "Utility bills"), sub: "~" + Math.round(u.bills_per_month) + T(" laskua, ", " bills, ") + eur(u.per_month_eur, 0), from: u.day_range.first, to: u.day_range.last, paidDays: paidHere(/utility/i)},
      {name: T("Palkka", by("salary").name), sub: "+" + eur(by("salary").per_month_eur), day: due(by("salary")), incoming: true},
      {name: T("Sijoittaminen", "Investing"), sub: eur(by("investing").per_month_eur), day: due(by("investing"))}];
    const W = 318, labW = 104, lane = 42, T0 = 30, H = T0 + lanes.length * lane + 18, x0 = labW + 6, x1 = W - 8;
    const x = d => x0 + (d - 1) / (DAYS - 1) * (x1 - x0);
    const svg = el("svg", {class: "ch", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": T(`${PAYMENTS} päivittäin`, `${MONTH_NAME}'s regular payments by day`)});
    for (let d = 1; d <= DAYS; d += 7) if (Math.abs(d - TODAY) > 1) svg.appendChild(el("line", {x1: x(d), x2: x(d), y1: T0 - 4, y2: H - 18, stroke: css("line")}));
    // the dots go in their own layer above the Today line, so the line never cuts into a dot
    const marks = el("g", {});  // lane lines and bands go under the Today line, dots and labels over it
    lanes.forEach((l, i) => {
      const yc = T0 + i * lane + lane / 2;
      marks.appendChild(el("text", {x: 0, y: yc - 2, style: "font-size:12.5px;fill:" + css("strong")}, l.name));
      marks.appendChild(el("text", {x: 0, y: yc + 13, style: "font-size:11.5px;fill:" + (l.incoming ? css("brand") : css("muted")) + (l.incoming ? ";font-weight:600" : "")}, l.sub));
      svg.appendChild(el("line", {x1: x0, x2: x1, y1: yc, y2: yc, stroke: css("line"), "stroke-width": 1}));
      if (l.from) {
        svg.appendChild(el("line", {x1: x(l.from), x2: x(l.to), y1: yc, y2: yc, stroke: css("brand"), "stroke-opacity": .35, "stroke-width": 8, "stroke-linecap": "round"}));
        (l.paidDays || []).forEach(d => { marks.appendChild(el("circle", {cx: x(d), cy: yc, r: 6, fill: css("brand"), stroke: css("card"), "stroke-width": 2}));
          marks.appendChild(el("path", {d: `M${x(d) - 2.6},${yc}l1.8,1.8l3.6,-3.6`, fill: "none", stroke: inkOn(css("brand")), "stroke-width": 1.5, "stroke-linecap": "round"})); });
      } else {
        const past = l.day <= TODAY, fill = l.incoming ? css("bright") : css("brand");
        marks.appendChild(el("circle", {cx: x(l.day), cy: yc, r: 7, fill, stroke: css("card"), "stroke-width": 2}));
        if (past) marks.appendChild(el("path", {d: `M${x(l.day) - 3},${yc}l2,2l4,-4`, fill: "none", stroke: inkOn(fill), "stroke-width": 1.6, "stroke-linecap": "round"}));
        // paid: the date sits above its dot, left of today; still to come: to the right of the dot
        marks.appendChild(el("text", past ? {x: x(l.day), y: yc - 11, "text-anchor": "middle", style: `font-size:11.5px;fill:${css("muted")}`}
          : {x: x(l.day) + 11, y: yc + 4, style: `font-size:11.5px;font-weight:600;paint-order:stroke;stroke:${css("card")};stroke-width:4px;fill:${css("strong")}`}, (past ? T("maksettu ", "paid ") : "") + dm(l.day)));
      }
    });
    // today: over the lane lines and bands, with a card-coloured halo; under the dots, so it never cuts into one
    svg.appendChild(el("line", {x1: x(TODAY), x2: x(TODAY), y1: T0 - 8, y2: H - 18, stroke: css("card"), "stroke-width": 4}));
    svg.appendChild(el("line", {x1: x(TODAY), x2: x(TODAY), y1: T0 - 8, y2: H - 18, stroke: css("strong"), "stroke-width": 1, "stroke-dasharray": "3 3"}));
    svg.appendChild(marks);
    svg.appendChild(el("text", {x: x(TODAY), y: T0 - 12, "text-anchor": "middle", style: "font-size:11.5px;font-weight:600;fill:" + css("strong")}, T("Tänään", "Today")));
    for (let d = 1; d <= DAYS; d += 7) svg.appendChild(el("text", {x: x(d), y: H - 4, "text-anchor": "middle"}, String(d)));
    const soon = lanes.filter(l => !l.from && l.day > TODAY).sort((a, b) => a.day - b.day).map(l => FI ? `${l.name.toLowerCase()} ${dm(l.day)}` : `${l.name.toLowerCase()} on ${dm(l.day)}`);
    if (u.day_range.last > TODAY) soon.push(T(`asumislaskuja milloin tahansa ${dm(u.day_range.last)} asti`, `utility bills any day to ${dm(u.day_range.last)}`));
    host.innerHTML = `<p class="h3">${PAYMENTS}</p><p class="k" style="margin-top:2px">${T("Tulossa", "Coming up")}: ${soon.join(", ")}</p>`;
    host.appendChild(svg);
    // why rent here differs from card 6: this is the payment itself, card 6 the average month (no figure changed, only said)
    const avg = by("rent").per_month_eur;
    if (rentPaid && Math.abs(rentPaid.amount_eur - avg) >= .005) {
      const diff = rentPaid.amount_eur - avg, more = diff > 0;
      host.insertAdjacentHTML("beforeend", `<p class="sub">${T(
        `Vuokra on tässä ${dm(dayOf(rentPaid.date))} maksettu ${eur(rentPaid.amount_eur)}, ${eur(Math.abs(diff))} ${more ? "enemmän" : "vähemmän"} kuin kortin ”${AFTER}” käyttämä keskiarvo ${eur(avg)}. Asumislaskut näkyvät keskimääräisenä kuukautena.`,
        `Rent here is the ${eur(rentPaid.amount_eur)} paid on ${dm(dayOf(rentPaid.date))}, ${eur(Math.abs(diff))} ${more ? "more" : "less"} than the ${eur(avg)} average used in “${AFTER}”. Utility bills show their average month.`)}</p>`);
    }
  })();
  }

  render();
})().catch(err => {
  const p = document.createElement("p"); p.className = "notes";
  p.textContent = (document.documentElement.lang === "fi" ? "Kaavioiden tietoja ei voitu ladata: " : "The charts could not load their data: ") + err.message;
  const h = document.getElementById("money"); if (h) h.prepend(p); console.error(err);
});
