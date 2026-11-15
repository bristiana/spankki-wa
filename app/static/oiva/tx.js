// Transactions: a tapped row opens its detail as a bottom sheet over the phone (the link still works without this),
// and on the full list the search and basket chips filter as you type or tap.
(() => {
  const phone = document.getElementById("phone") || document.body;
  let open = null, back = null;
  const X = '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M9 9l14 14M23 9L9 23"/></svg>';
  const lang = document.documentElement.lang === "en";

  function close() {
    if (!open) return;
    open.remove(); open = null;
    document.removeEventListener("keydown", onKey, true);
    if (back) back.focus({ preventScroll: true });
  }
  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "Tab" || !open) return;  // keep focus inside the sheet
    const f = [...open.querySelectorAll("a[href],button")]; if (!f.length) return;
    const [a, z] = [f[0], f[f.length - 1]];
    if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
    else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
  }
  function show(row) {
    const tpl = document.getElementById(row.dataset.sheet); if (!tpl) return false;
    close(); back = row;
    const wrap = document.createElement("div");
    wrap.className = "txs-wrap"; wrap.id = "tx-sheet";
    wrap.innerHTML = '<div class="txs-scrim"></div><div class="txs txl" role="dialog" aria-modal="true" tabindex="-1">' +
      '<span class="grab" aria-hidden="true"></span><button class="x" type="button" aria-label="' + (lang ? "Close" : "Sulje") + '">' + X + "</button></div>";
    const sheet = wrap.querySelector(".txs");
    sheet.appendChild(tpl.content.cloneNode(true));
    const h = sheet.querySelector(".txs-name"); if (h) { h.id = "txs-title"; sheet.setAttribute("aria-labelledby", "txs-title"); }
    wrap.querySelector(".txs-scrim").addEventListener("click", close);
    wrap.querySelector(".x").addEventListener("click", close);
    // swipe the sheet down to close it
    let y0 = null;
    sheet.addEventListener("touchstart", e => { y0 = sheet.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
    sheet.addEventListener("touchmove", e => { if (y0 === null) return; const d = Math.max(0, e.touches[0].clientY - y0); sheet.style.transform = d ? `translateY(${d}px)` : ""; }, { passive: true });
    sheet.addEventListener("touchend", e => { if (y0 === null) return; const d = e.changedTouches[0].clientY - y0; y0 = null; if (d > 90) close(); else sheet.style.transform = ""; });
    phone.appendChild(wrap); open = wrap;
    document.addEventListener("keydown", onKey, true);
    sheet.focus({ preventScroll: true });
    return true;
  }
  document.addEventListener("click", e => {
    const row = e.target.closest("a.txr[data-sheet]");
    if (!row || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    if (show(row)) e.preventDefault();
  });

  // The full list: filter in place; the GET form and chip links still work without this.
  const form = document.getElementById("tx-filter");
  if (!form) return;
  const q = document.getElementById("tx-q"), sum = document.getElementById("tx-sum"), empty = document.getElementById("tx-empty");
  const chips = [...form.querySelectorAll(".txc a")];
  let kori = (chips.find(c => c.getAttribute("aria-current")) || {}).dataset?.kori || "";
  // Sums are written as the server's money filter writes this page's amounts: same decimal mark, space thousands.
  const shown = (document.querySelector("#all-tx .tam") || {}).textContent || "";
  const dec = (shown.match(/([.,])\d\d\s*€/) || [0, ","])[1];
  const fmt = n => n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0").replace(".", dec) + "\u00a0€";
  const all = document.getElementById("all-tx");
  function apply() {
    if (!all) return;
    const words = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let n = 0, out = 0, inn = 0;
    for (const g of all.querySelectorAll(".txg")) {
      let any = false;
      for (const r of g.querySelectorAll(".txr")) {
        const ok = words.every(w => r.dataset.q.includes(w)) &&
          (!kori || (kori === "in" ? r.dataset.in === "1" : r.dataset.basket === kori));
        r.hidden = !ok;
        if (ok) {
          any = true; n++;
          const a = parseFloat(r.dataset.amt);
          if (r.dataset.in === "1") inn += a; else out += a;
        }
      }
      g.hidden = !any;
    }
    all.hidden = !n; if (empty) empty.hidden = !!n;
    if (sum) {
      const label = lang ? (n === 1 ? "transaction" : "transactions") : (n === 1 ? "tapahtuma" : "tapahtumaa");
      sum.querySelector("b").textContent = `${n} ${label}`;
      const o = document.getElementById("tx-out"), i = document.getElementById("tx-in");
      o.textContent = (lang ? "Out " : "Menot ") + fmt(out); o.hidden = !out;
      i.textContent = (lang ? "In " : "Tulot ") + fmt(inn); i.hidden = !inn;
    }
  }
  q.addEventListener("input", apply);
  chips.forEach(c => c.addEventListener("click", e => {
    e.preventDefault();
    kori = c.dataset.kori;
    chips.forEach(x => x === c ? x.setAttribute("aria-current", "true") : x.removeAttribute("aria-current"));
    const hid = form.querySelector('input[name="kori"]');
    if (kori) { if (hid) hid.value = kori; else form.insertAdjacentHTML("beforeend", `<input type="hidden" name="kori" value="${kori}">`); }
    else if (hid) hid.remove();
    apply();
  }));
})();
