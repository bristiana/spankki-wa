/* Oiva's screens in the S-mobiili mock. Each screen draws itself from OivaData (data.js): the live API when it
   exists, otherwise labelled sample data. Plan: docs/superpowers/plans/2026-11-14-smobiili-oiva-screens.md */
(function () {
  "use strict";
  const renderers = {};
  let host = null;

  // ---------- helpers ----------
  const NB = " ", MINUS = "−";
  const isNum = v => typeof v === "number" && isFinite(v);
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  }
  function money(v, decimals) {  // 1 234.56 € with spaces that never break, a real minus sign
    if (!isNum(v)) return "–";
    const d = decimals == null ? 2 : decimals;
    const s = Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d}).replace(/,/g, NB);
    return (v < 0 ? MINUS : "") + s + NB + "€";
  }
  function pct(v) { return isNum(v) ? (v < 0 ? MINUS : "") + Math.round(Math.abs(v) * 100) + NB + "%" : "–"; }
  function months(v) { return isNum(v) ? v.toFixed(1) + NB + "months" : "–"; }
  function monthName(ym) {
    const m = /^(\d{4})-(\d{2})$/.exec(ym || "");
    return m ? new Date(Date.UTC(+m[1], +m[2] - 1, 1)).toLocaleString("en-GB", {month: "long", year: "numeric", timeZone: "UTC"}) : "";
  }
  function shiftMonth(ym, by) {
    const [y, m] = ym.split("-").map(Number), d = new Date(Date.UTC(y, m - 1 + by, 1));
    return d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0");
  }
  function daysIn(ym) { const [y, m] = ym.split("-").map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); }
  function niceDate(iso) {
    const d = new Date(iso + "T00:00:00Z");
    return isNaN(d) ? "" : d.toLocaleString("en-GB", {weekday: "short", day: "numeric", month: "short", timeZone: "UTC"});
  }
  function chatLink(q) {
    const web = new URLSearchParams(location.search).get("web");
    return "/chat?q=" + encodeURIComponent(q) + (web ? "&embed=1" : "");
  }
  function sampleNote(res) {
    return res.live ? "" : '<p class="sample-note">Sample data until the live service is connected.</p>';
  }
  function errorCard(what, retry) {
    host.innerHTML = '<div class="card oiva-error" role="alert"><p><b>Couldn\'t load your ' + esc(what) + '.</b></p>'
      + '<p class="muted">Try again, or ask Oiva in the chat.</p>'
      + '<button class="btn ghost" type="button" id="oiva-retry">Try again</button>'
      + '<a class="btn ghost" href="' + chatLink("") + '">Open the chat</a></div>';
    host.querySelector("#oiva-retry").addEventListener("click", retry);
  }
  function chart(el, spec) { if (window.birkyChart) window.birkyChart(el, spec); }
  const BASKET = {regular: "Mandatory & regular", irregular: "Mandatory & irregular", everyday: "Slips through your fingers"};

  // ---------- Spending ----------
  renderers.spend = async function (el, params) {
    const res = await OivaData.get("spend", params);
    const d = res.data;
    if (!d || !d.month || !isNum(d.total_eur) || !Array.isArray(d.categories)) {
      return errorCard("spending", () => renderers.spend(el, params));
    }
    const cats = d.categories.filter(c => c && c.label).slice().sort((a, b) => (b.spent_eur || 0) - (a.spent_eur || 0));
    const daily = (d.daily || []).filter(x => isNum(x.cumulative_eur));
    const thisMonth = d.as_of ? d.as_of.slice(0, 7) : d.month;
    let h = sampleNote(res);
    h += '<section class="card sp-head">';
    if (res.live) {
      h += '<div class="sp-switch"><button type="button" class="sp-prev" aria-label="Previous month">‹</button>'
        + '<b>' + esc(monthName(d.month)) + '</b>'
        + '<button type="button" class="sp-next" aria-label="Next month"' + (d.month >= thisMonth ? " disabled" : "") + '>›</button></div>';
    } else h += '<p class="kicker">' + esc(monthName(d.month)) + '</p>';
    h += '<div class="sp-total amount">' + money(d.total_eur) + '</div>'
      + '<p class="muted">spent on everyday things' + (d.month === thisMonth ? " so far" : "") + '</p>'
      + '<div class="sp-facts">'
      + (isNum(d.prev_total_eur) ? '<span>' + esc(monthName(shiftMonth(d.month, -1)).split(" ")[0]) + ': <b>' + money(d.prev_total_eur) + '</b></span>' : "")
      + (isNum(d.income_eur) ? '<span>Income: <b>' + money(d.income_eur) + '</b></span>' : "")
      + '</div></section>';

    // pace against the budget
    h += '<section class="card"><h3 class="sc-h">Your pace</h3>';
    if (isNum(d.budget_total_eur) && d.budget_total_eur > 0 && daily.length) {
      const p = d.pace || {}, over = p.status === "over";
      h += '<p class="sp-pace ' + (over ? "is-over" : "is-ok") + '">' + (over
          ? "Ahead of pace: " + money(p.spent_by_today_eur) + " spent, an even pace would be " + money(p.expected_by_today_eur) + " by today."
          : "On track: " + money(p.spent_by_today_eur) + " spent of a " + money(d.budget_total_eur) + " budget.") + '</p>'
        + '<div class="sp-chart" id="sp-pace"></div>';
    } else {
      h += '<p class="muted">No budget yet. <a href="/s-mobiili/budget' + esc(location.search) + '">Set one</a> and Oiva shows your pace here.</p>';
    }
    h += '</section>';

    // where it went
    if (cats.some(c => c.spent_eur > 0)) h += '<section class="card"><h3 class="sc-h">Where it went</h3><div class="sp-chart" id="sp-donut"></div></section>';

    // categories
    h += '<section class="card sp-cats"><h3 class="sc-h">By category</h3>';
    for (const c of cats) {
      const bud = isNum(c.budget_eur) ? c.budget_eur : null, spent = isNum(c.spent_eur) ? c.spent_eur : 0;
      const share = bud ? Math.min(100, Math.round(spent / bud * 100)) : 0;
      const change = isNum(c.prev_eur) ? spent - c.prev_eur : null;
      h += '<a class="sp-cat" href="' + chatLink("Show my " + c.label + " spending by month") + '">'
        + '<span class="sp-row"><span><i class="dot b-' + esc(c.basket) + '" title="' + esc(BASKET[c.basket] || "") + '"></i>' + esc(c.label) + '</span><b>' + money(spent) + '</b></span>'
        + (bud != null
          ? '<span class="bar' + (spent > bud ? " over" : "") + '"><i style="width:' + share + '%"></i></span><small>' + (spent > bud ? "Over by " + money(spent - bud) : money(bud - spent) + " left of " + money(bud)) + '</small>'
          : '<small>No budget yet</small>')
        + (change != null && c.prev_eur > 0 ? '<small class="sp-prev-m">Last month ' + money(c.prev_eur) + '</small>' : "")
        + '</a>';
    }
    h += '<p class="legend"><i class="dot b-regular"></i>' + BASKET.regular + ' <i class="dot b-irregular"></i>' + BASKET.irregular
      + ' <i class="dot b-everyday"></i>' + BASKET.everyday + '</p></section>';

    // bills this month
    const bills = (d.bills || []).filter(b => b && b.name);
    if (bills.length) {
      h += '<section class="card"><h3 class="sc-h">Bills this month</h3><div class="rows">'
        + bills.map(b => '<div class="r"><span>' + esc(niceDate(b.date)) + '</span><span>' + esc(b.name) + ' · <b>' + money(b.amount_eur) + '</b></span></div>').join("")
        + '</div><a class="more" href="/s-mobiili/bills' + esc(location.search) + '">All regular payments</a></section>';
    }
    el.innerHTML = h;

    if (el.querySelector("#sp-pace")) {
      chart(el.querySelector("#sp-pace"), {type: "pace", title: "Your pace", subtitle: monthName(d.month), unit: "€",
        labels: daily.map(x => String(x.day)), values: daily.map(x => x.cumulative_eur), budget: d.budget_total_eur,
        days: daysIn(d.month), today: daily.length - 1});
    }
    if (el.querySelector("#sp-donut")) {
      const top = cats.filter(c => c.spent_eur > 0), main = top.slice(0, 4), rest = top.slice(4).reduce((a, c) => a + c.spent_eur, 0);
      chart(el.querySelector("#sp-donut"), {type: "donut", title: "Where it went", subtitle: monthName(d.month), unit: "€",
        labels: main.map(c => c.label).concat(rest > 0 ? ["Other"] : []), values: main.map(c => c.spent_eur).concat(rest > 0 ? [rest] : [])});
    }
    const prev = el.querySelector(".sp-prev"), next = el.querySelector(".sp-next");
    if (prev) prev.addEventListener("click", () => renderers.spend(el, {month: shiftMonth(d.month, -1)}));
    if (next) next.addEventListener("click", () => renderers.spend(el, {month: shiftMonth(d.month, 1)}));
  };

  // ---------- Budget ----------
  const SOURCE = {own: "Your own", peers: "People like me", helsinki: "Helsinki area", finland: "Finland"};
  let toastTimer = null;

  renderers.budget = async function (el, given) {
    const res = given || await OivaData.get("budget");
    const d = res.data;
    if (!d || !Array.isArray(d.items) || !d.presets) return errorCard("budget", () => renderers.budget(el));
    const peers = d.presets.peers || {}, co = peers.cohort || {};
    const cohortText = co.age_band ? "people aged " + co.age_band.replace("-", "–") + " in " + co.city + ", " + co.n + " people" : "";
    let h = sampleNote(res)
      + '<section class="card"><p class="muted bd-intro">Tap a category to set its monthly budget: your own amount, or what is typical for people like you.</p>'
      + '<button class="btn ghost" type="button" id="bd-all">Set all to people like me</button>'
      + (cohortText ? '<p class="fi bd-cohort">Typical (median) spend for ' + esc(cohortText) + '. S-Pankki data, Apr–Sep 2026.</p>' : "")
      + '</section><section class="card bd-list">';
    for (const it of d.items) {
      const lim = isNum(it.limit_eur) ? it.limit_eur : null, spent = isNum(it.spent_eur) ? it.spent_eur : 0;
      const over = lim != null && spent > lim;
      h += '<button type="button" class="bd-item" data-cat="' + esc(it.category) + '">'
        + '<span class="sp-row"><span>' + esc(it.label || it.category) + '</span><b>' + (lim != null ? money(lim) : "") + '</b></span>'
        + (lim != null
          ? '<span class="bar' + (over ? " over" : "") + '"><i style="width:' + (lim > 0 ? Math.min(100, Math.round(spent / lim * 100)) : (spent > 0 ? 100 : 0)) + '%"></i></span>'
            + '<small>' + (over ? "Over by " + money(spent - lim) : money(spent) + " spent · " + money(lim - spent) + " left"
              + (isNum(it.left_per_day_eur) ? ", " + money(it.left_per_day_eur) + " a day" : "")) + ' · ' + esc(SOURCE[it.source] || "Your own") + '</small>'
          : '<small>No budget yet · ' + money(spent) + ' spent this month</small>')
        + '</button>';
    }
    h += '</section><p class="fi bd-foot">A budget is a setting, not a payment: no PIN needed, and you can undo it.</p>';
    el.innerHTML = h;
    el.querySelectorAll(".bd-item").forEach(b => b.addEventListener("click", () => openSheet(el, d, b.dataset.cat, b)));
    el.querySelector("#bd-all").addEventListener("click", () => apply(el, d, {category: "all", mode: "peers"}, "All budgets set to people like me"));
  };

  function phoneEl() { return document.getElementById("phone") || document.body; }

  function openSheet(el, d, cat, opener) {
    const it = d.items.find(i => i.category === cat); if (!it) return;
    const label = it.label || cat, P = d.presets;
    let sheet = phoneEl().querySelector(".bd-sheet");
    if (!sheet) { sheet = document.createElement("div"); sheet.className = "bd-sheet"; phoneEl().appendChild(sheet); }
    const choice = (mode, amount, note) => isNum(amount)
      ? '<button type="button" class="bd-choice" data-mode="' + mode + '"><span class="sp-row"><span>' + SOURCE[mode] + '</span><b>' + money(amount) + '</b></span><small>' + esc(note) + '</small></button>' : "";
    const co = (P.peers && P.peers.cohort) || {};
    sheet.innerHTML = '<div class="bd-panel" role="dialog" aria-modal="true" aria-labelledby="bd-title">'
      + '<h2 id="bd-title">' + esc(label) + ' budget</h2><p class="muted">A month. Spent so far: ' + money(it.spent_eur) + '</p>'
      + choice("peers", P.peers && P.peers.per_category[cat], co.age_band ? "Typical for people aged " + co.age_band.replace("-", "–") + " in " + co.city + " (" + co.n + " people)" : "")
      + choice("helsinki", P.helsinki && P.helsinki.per_category[cat], "Typical in Helsinki, Espoo and Vantaa" + (P.helsinki && P.helsinki.n ? " (" + P.helsinki.n + " people)" : ""))
      + choice("finland", P.finland && P.finland.per_category[cat], "Statistics Finland " + (P.finland && P.finland.year || "") + ", " + (P.finland && P.finland.per_person ? "per person" : "per household") + ", your age group")
      + '<form class="bd-own"><label for="bd-own-input">Your own amount</label><div><input id="bd-own-input" type="number" inputmode="decimal" min="0" step="1" placeholder="'
      + (isNum(it.limit_eur) ? it.limit_eur : "") + '"><button class="btn" type="submit">Save</button></div><p class="bd-err" role="alert" hidden></p></form>'
      + '<button type="button" class="btn ghost bd-cancel">Cancel</button></div>';
    sheet.hidden = false;
    const close = () => { sheet.hidden = true; document.removeEventListener("keydown", onKey); if (opener) opener.focus(); };
    const onKey = e => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    sheet.onclick = e => { if (e.target === sheet) close(); };
    sheet.querySelector(".bd-cancel").addEventListener("click", close);
    sheet.querySelectorAll(".bd-choice").forEach(b => b.addEventListener("click", () => {
      close(); apply(el, d, {category: cat, mode: b.dataset.mode}, label + " budget set to " + SOURCE[b.dataset.mode].toLowerCase());
    }));
    sheet.querySelector(".bd-own").addEventListener("submit", e => {
      e.preventDefault();
      const v = Number(sheet.querySelector("#bd-own-input").value), err = sheet.querySelector(".bd-err");
      if (sheet.querySelector("#bd-own-input").value === "" || !isFinite(v) || v < 0) { err.textContent = "Enter an amount of 0 € or more."; err.hidden = false; return; }
      close(); apply(el, d, {category: cat, mode: "own", amount_eur: Math.round(v * 100) / 100}, label + " budget set");
    });
    sheet.querySelector(".bd-choice, #bd-own-input").focus();
  }

  async function apply(el, d, change, message) {
    const before = d.items.map(i => ({category: i.category, limit_eur: isNum(i.limit_eur) ? i.limit_eur : null, source: i.source}))
      .filter(i => change.category === "all" || i.category === change.category);
    let res;
    try { res = await OivaData.setBudget(change); } catch (err) { return toast(String(err.message || err)); }
    await renderers.budget(el, res);
    toast(message + ".", async () => {
      let last = null;
      for (const b of before) last = await OivaData.setBudget({category: b.category, mode: "restore", amount_eur: b.limit_eur, source: b.source});
      if (last) await renderers.budget(el, last);
      toast("Undone.");
    });
  }

  function toast(text, undo) {
    let t = phoneEl().querySelector(".bd-toast");
    if (!t) { t = document.createElement("div"); t.className = "bd-toast"; t.setAttribute("role", "status"); phoneEl().appendChild(t); }
    t.innerHTML = '<span>' + esc(text) + '</span>' + (undo ? '<button type="button">Undo</button>' : "");
    t.hidden = false;
    if (undo) t.querySelector("button").addEventListener("click", () => { clearTimeout(toastTimer); t.hidden = true; undo(); });
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, undo ? 10000 : 3000);
  }

  // ---------- plumbing ----------
  async function render(name, el) {
    host = el || host;
    if (!host) return;
    const fn = renderers[name];
    if (!fn) { host.innerHTML = '<p class="muted">This screen is coming soon.</p>'; return; }
    try { await fn(host); } catch (err) { errorCard(name === "spend" ? "spending" : "data", () => render(name)); }
  }

  window.OivaScreens = {render, show: name => render(name), register: (name, fn) => { renderers[name] = fn; },
                        _fmt: {money, pct, months, esc, chatLink, sampleNote, errorCard: (w, r) => errorCard(w, r), isNum, monthName, niceDate}};
})();
