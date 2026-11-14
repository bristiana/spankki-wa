/* Oiva's screens in the S-mobiili mock. Each screen draws itself from OivaData (data.js).
   Renderers are added task by task: docs/superpowers/plans/2026-11-14-smobiili-oiva-screens.md */
(function () {
  "use strict";
  const renderers = {};
  let host = null;

  async function render(name, el) {
    host = el || host;
    if (!host) return;
    const fn = renderers[name];
    if (!fn) { host.innerHTML = '<p class="muted">This screen is coming soon.</p>'; return; }
    await fn(host);
  }

  window.OivaScreens = {render, show: name => render(name), register: (name, fn) => { renderers[name] = fn; }};
})();
