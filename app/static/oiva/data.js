/* Oiva's data for the S-mobiili screens.
   get(name, params) asks the live API (/api/<name>) first. If it isn't there yet, answers with an error or sends
   something that isn't JSON, it falls back to the sample in /static/fixtures/<name>.json and says so (live: false),
   so the screens can label sample data. Contracts: docs/superpowers/specs/2026-11-14-oiva-wealth-and-voice-design.md */
(function () {
  "use strict";
  const NAMES = ["spend", "budget", "compare", "recurring"];
  const samples = {};  // sample data, kept so budget changes made before the API exists stick for the visit

  async function json(url, opts) {
    const r = await fetch(url, Object.assign({headers: {"Accept": "application/json"}}, opts || {}));
    if (!r.ok) throw new Error(url + " answered " + r.status);
    const d = await r.json();
    if (!d || typeof d !== "object") throw new Error(url + " sent no object");
    return d;
  }

  function query(params) {
    const q = new URLSearchParams();
    const persona = new URLSearchParams(location.search).get("persona");
    if (persona) q.set("as", persona);
    for (const [k, v] of Object.entries(params || {})) if (v != null && v !== "") q.set(k, v);
    const s = q.toString();
    return s ? "?" + s : "";
  }

  async function sample(name) {
    if (!samples[name]) samples[name] = await json("/static/fixtures/" + name + ".json");
    return samples[name];
  }

  async function get(name, params) {
    if (!NAMES.includes(name)) throw new Error("unknown data: " + name);
    try {
      return {data: await json("/api/" + name + query(params)), live: true};
    } catch (err) {
      try {
        return {data: await sample(name), live: false, error: String(err.message || err)};
      } catch (err2) {
        return {data: null, live: false, error: String(err2.message || err2)};
      }
    }
  }

  // change = {category: "Restaurants" | "all", mode: "own" | "peers" | "helsinki" | "finland", amount_eur?}
  async function setBudget(change) {
    try {
      return {data: await json("/api/budget" + query(), {method: "POST", headers: {"Content-Type": "application/json", "Accept": "application/json"},
                                                         body: JSON.stringify(change)}), live: true};
    } catch (err) {
      const d = await sample("budget");
      const previous = {};
      for (const item of d.items) {
        if (change.category !== "all" && item.category !== change.category) continue;
        previous[item.category] = {limit_eur: item.limit_eur, source: item.source};
        let limit = null;
        if (change.mode === "own") limit = Number(change.amount_eur);
        else if (change.mode === "restore") limit = change.amount_eur == null ? null : Number(change.amount_eur);
        else { const p = d.presets[change.mode]; limit = p && p.per_category ? p.per_category[item.category] : null; }
        if (limit != null && !(isFinite(limit) && limit >= 0)) throw new Error("A budget must be 0 € or more.");
        item.limit_eur = limit == null ? null : Math.round(limit * 100) / 100;
        item.source = limit == null ? null : (change.mode === "restore" ? change.source || "own" : change.mode);
        if (item.limit_eur == null) Object.assign(item, {left_eur: null, left_per_day_eur: null, status: "none"});
        else {
          item.left_eur = Math.round((item.limit_eur - item.spent_eur) * 100) / 100;
          item.left_per_day_eur = Math.round(Math.max(item.left_eur, 0) / 26 * 100) / 100;
          item.status = item.left_eur < 0 ? "over" : "on_track";
        }
      }
      d.undo = {change, previous};
      return {data: d, live: false, error: String(err.message || err)};
    }
  }

  window.OivaData = {get, setBudget};
})();
