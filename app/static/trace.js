/* "How I worked it out" (FI "Näin laskin"): a reply's trace from /api/chat as a collapsed <details> panel.
   trace = [{tool, args, step, figures: [{label, value}], sources: [...]}, ..., {check, checked, matched, rewritten}]
   Everything from the trace is escaped: tool arguments can echo what the customer typed. */
(function (root) {
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  }
  // Amounts never break across lines: "1 313.89 €" keeps its spaces non-breaking.
  function nb(s) { return s.replace(/(\d) (?=\d{3}(?!\d))/g, "$1 ").replace(/(\d) €/g, "$1 €"); }
  function source(s) {  // an official guide's link is clickable; anything else is plain text
    const m = /^official guide: (https:\/\/[^\s"<>]+)$/.exec(s);
    return m ? 'official guide: <a href="' + esc(m[1]) + '" target="_blank" rel="noopener">' +
               esc(m[1].replace(/^https:\/\/(www\.)?/, "").replace(/\/$/, "")) + "</a>" : esc(s);
  }

  function traceHtml(trace) {
    const steps = (trace || []).filter(t => t && t.tool), check = (trace || []).find(t => t && t.check);
    if (!steps.length) return "";
    // the summary carries the number guard's verdict, so it shows under every answer without opening it
    const fi = typeof document !== "undefined" && document.documentElement.lang === "fi";
    const badge = check && check.checked ? '<span class="how-ok' + (check.matched < check.checked ? " warn" : "") + '">' +
      (check.matched < check.checked ? "! " : "✓ ") + check.matched + "/" + check.checked +
      (fi ? " lukua tarkistettu" : " figures checked") + "</span>" : "";
    let h = '<details class="how"><summary>' + (fi ? "Näin laskin" : "How I worked it out") + badge + '</summary>' +
            '<div class="how-body"><ol class="how-steps">';
    for (const s of steps) {
      h += "<li><p>" + nb(esc(s.step)) + "</p>";
      if ((s.figures || []).length) {
        h += "<dl>" + s.figures.map(f => "<dt>" + nb(esc(f.label)) + "</dt><dd>" + nb(esc(f.value)) + "</dd>").join("") + "</dl>";
      }
      h += "</li>";
    }
    h += "</ol>";
    const sources = [...new Set(steps.flatMap(s => s.sources || []))];
    if (sources.length) h += '<p class="how-src"><b>' + (fi ? "Lähteet:" : "Sources:") + '</b> ' + sources.map(source).join(" · ") + "</p>";
    if (check) h += '<p class="how-check' + (check.matched < check.checked ? " warn" : "") + '">' + esc(fi ? check.matched + "/" + check.checked + " lukua täsmäsi laskelmiin" + (check.rewritten ? " (ensimmäinen luonnos korjattiin)" : "") : check.check) + "</p>";
    return h + "</div></details>";
  }

  root.oivaTrace = traceHtml;
  if (typeof module !== "undefined") module.exports = traceHtml;
})(typeof window !== "undefined" ? window : globalThis);
