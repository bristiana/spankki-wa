/* The Talk to Oiva call view in the S-mobiili mock. It talks only to window.OivaVoice (Christiana's voice.js, loaded
   when /s-mobiili/voice-status says it exists); until then it plays the labelled demo in voice-stub.js.
   Oiva's animation follows the call, captions keep the last 2 lines, a call stops at 5 minutes.
   Plan: docs/superpowers/plans/2026-11-14-smobiili-oiva-screens.md (Task 8) */
(function () {
  "use strict";
  const STATE_TO_OIVA = {connecting: "thinking", listening: "listening", thinking: "thinking", speaking: "teaching", ended: "idle"};
  const STATE_TEXT = {connecting: "Connecting…", listening: "Listening", thinking: "Thinking…", speaking: "Oiva is talking", ended: "Call ended"};
  const TITLES = {spend: "Spending", budget: "Budget", compare: "People like me", bills: "Bills"};
  const $ = id => document.getElementById(id);
  let voice = null, oiva = null, wired = false, live = false, startedAt = 0, tick = null, muted = false, capped = false;
  const CONNECT_MS = 12000;  // never an endless "connecting"
  const withTimeout = (pr, ms) => Promise.race([pr, new Promise((_, no) => setTimeout(() => no(new Error("timeout")), ms))]);

  const maxMs = () => Number(window.OIVA_CALL_MAX_MS) || 5 * 60 * 1000;

  function loadScript(src) {
    return new Promise((ok, fail) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = fail; document.head.appendChild(s); });
  }

  async function pickVoice() {
    if (window.OivaVoice) return window.OivaVoice;
    try {
      const st = await (await fetch("/s-mobiili/voice-status")).json();
      if (st.voice_js) { await loadScript("/static/oiva/voice.js"); if (window.OivaVoice) return window.OivaVoice; }
    } catch (e) { /* no voice.js: the demo plays */ }
    return window.OivaVoiceDemo;
  }

  // Talk to Oiva: the home has its own button; every Oiva screen gets a round one above the tabs
  function fab() {
    let b = $("talk-oiva");
    if (!b) {
      b = document.createElement("button");
      b.type = "button"; b.id = "talk-oiva"; b.className = "talk-fab"; b.setAttribute("aria-label", "Talk to Oiva");
      b.innerHTML = '<img src="/static/oiva/idle.svg" alt="" width="36" height="36"><span>Talk to Oiva</span>';
      $("phone").appendChild(b);
    }
    if (!b.dataset.wired) { b.dataset.wired = "1"; b.addEventListener("click", open); }
  }

  function setOiva(name) {
    const host = $("call-oiva");
    if (!oiva && window.Oiva && host) oiva = window.Oiva.create(host, {palette: "red", state: name, trackPointer: false});
    if (oiva && window.Oiva.STATES.includes(name)) { oiva.setState(name); host.dataset.state = name; }
  }

  function caption(c) {
    if (!c || !c.text) return;
    const box = $("call-captions"), p = document.createElement("p");
    p.className = c.who === "you" ? "you" : "oiva";
    const who = document.createElement("b"); who.textContent = c.who === "you" ? "You: " : "Oiva: ";
    p.append(who, document.createTextNode(c.text));
    box.appendChild(p);
    while (box.children.length > 2) box.removeChild(box.firstChild);
  }

  function showScreen(name) {
    if (!TITLES[name] || !window.OivaScreens) return;
    let host = $("oiva-screen");
    if (!host) {  // on the home screen: the screen behind the call takes the home's place, with a way back
      $("screen").innerHTML = '<div id="oiva-screen" aria-live="polite"></div>';
      host = $("oiva-screen");
      const top = document.querySelector(".top");
      if (top && !top.querySelector(".call-back")) {
        const a = document.createElement("a");
        a.className = "call-back"; a.href = location.pathname + location.search; a.setAttribute("aria-label", "Back to S-Pankki home");
        a.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
        top.insertBefore(a, top.firstChild);
      }
      fab();
    }
    host.dataset.screen = name;
    const h1 = document.querySelector(".top h1"); if (h1) h1.textContent = TITLES[name];
    $("screen").scrollTop = 0;
    window.OivaScreens.render(name, host);
  }

  function summary(card) {
    if (!card) return;
    try { sessionStorage.setItem("oiva-call-summary", JSON.stringify(card)); } catch (e) { /* private mode */ }
    const box = $("call-summary");
    box.textContent = "";
    const h = document.createElement("b"); h.textContent = card.title || "Your call with Oiva"; box.appendChild(h);
    for (const line of card.lines || []) { const p = document.createElement("p"); p.textContent = line; box.appendChild(p); }
    if (card.unbacked_numbers && card.unbacked_numbers.length) {
      const p = document.createElement("p"); p.className = "warn";
      p.textContent = "Check these numbers: Oiva said them without a source: " + card.unbacked_numbers.join(", ");
      box.appendChild(p);
    }
    box.hidden = false;
  }

  function onState(s) {
    if (!(capped && s === "ended")) $("call-state").textContent = STATE_TEXT[s] || s;
    if (STATE_TO_OIVA[s]) setOiva(STATE_TO_OIVA[s]);
    if (s === "ended") { live = false; clearInterval(tick); $("call-end").textContent = "Close"; }
  }

  function wire(v) {
    if (wired === v) return;
    wired = v;
    v.on("state", onState);
    v.on("caption", caption);
    v.on("screen", showScreen);
    v.on("mood", m => { if (live && window.Oiva && window.Oiva.STATES.includes(m)) setOiva(m); });
    v.on("summary", summary);
  }

  function clock() {
    const ms = Date.now() - startedAt, s = Math.floor(ms / 1000);
    $("call-timer").textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") + " / 5:00";
    if (ms >= maxMs()) {
      capped = true; stop();
      $("call-state").textContent = "Calls stop at 5 minutes. Start a new one any time.";
    }
  }

  async function open() {
    const box = $("oiva-call"); if (!box) return;
    box.hidden = false;
    $("call-captions").textContent = ""; $("call-summary").hidden = true; $("call-error").hidden = true;
    $("call-end").textContent = "End"; $("call-timer").textContent = "0:00 / 5:00"; capped = false; $("call-mute").hidden = false;
    let first = true;
    try { first = !sessionStorage.getItem("oiva-provider-seen"); sessionStorage.setItem("oiva-provider-seen", "1"); } catch (e) { /* private mode */ }
    $("call-provider").hidden = !first;
    setOiva("thinking"); onState("connecting");
    $("call-end").focus();
    try { voice = await withTimeout(pickVoice(), CONNECT_MS); } catch (e) { voice = null; }
    $("call-demo").hidden = !(voice && voice.demo);
    if (!voice) return failed("Voice isn't available right now.");
    wire(voice);
    try {
      await withTimeout(voice.start(), CONNECT_MS);
    } catch (err) {
      const why = String((err && (err.name + " " + err.message)) || "");
      return failed(/NotAllowed|Permission|microphone/i.test(why) ? "The microphone is blocked. Allow it in the browser to talk."
        : /timeout/.test(why) ? "Oiva didn't answer in time." : "Voice isn't available right now.");
    }
    live = true; startedAt = Date.now(); clearInterval(tick); tick = setInterval(clock, 250);
  }

  function failed(why) {
    live = false; clearInterval(tick);
    setOiva("error");
    $("call-state").textContent = "Not connected";
    $("call-error").hidden = false;
    $("call-why").textContent = why || "";
    $("call-mute").hidden = true; $("call-end").textContent = "Close";
  }

  function stop() {
    clearInterval(tick);
    if (voice && live) { live = false; try { voice.stop(); } catch (e) { /* already stopped */ } }
    live = false;
  }

  function close() {
    stop();
    $("oiva-call").hidden = true;
    const talk = $("talk-oiva"); if (talk) talk.focus();
  }

  function toggleMute() {
    muted = !muted;
    const b = $("call-mute");
    b.setAttribute("aria-pressed", String(muted)); b.textContent = muted ? "Unmute" : "Mute";
    if (voice && typeof voice.mute === "function") voice.mute(muted);
  }

  document.addEventListener("DOMContentLoaded", init);
  if (document.readyState !== "loading") init();
  let inited = false;
  function init() {
    if (inited || !$("oiva-call")) return;
    inited = true;
    fab();
    $("call-end").addEventListener("click", () => ($("call-end").textContent === "Close" || !live ? close() : (stop(), close())));
    $("call-mute").addEventListener("click", toggleMute);
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("oiva-call").hidden) close(); });
  }

  window.OivaCall = {open, close, stop};
})();
