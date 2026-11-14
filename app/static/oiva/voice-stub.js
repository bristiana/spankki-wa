/* Demo playback for the Talk to Oiva call until Christiana's voice.js (window.OivaVoice) is connected.
   Same contract as OivaVoice: start(), stop(), on(event, fn) with state, caption, screen, mood and summary.
   It plays a scripted call over the sample data and is always labelled "Demo playback · voice not connected".
   Spec: docs/superpowers/specs/2026-11-14-oiva-wealth-and-voice-design.md (Talking to Oiva) */
(function () {
  "use strict";
  // [delay before this step in ms, event, value]. Figures come from the sample data the screens show.
  const SCRIPT = [
    [600, "state", "speaking"],
    [0, "caption", {who: "oiva", text: "Hi, I'm Oiva. I keep an eye on your vault. Shall we look at October together?"}],
    [3200, "state", "listening"],
    [1600, "caption", {who: "you", text: "Yes. Where is my money going?"}],
    [900, "state", "thinking"],
    [700, "screen", "spend"],
    [500, "state", "speaking"],
    [0, "caption", {who: "oiva", text: "First, a guess: how much went on everyday things so far this month?"}],
    [3000, "state", "listening"],
    [1500, "caption", {who: "you", text: "Maybe three hundred?"}],
    [800, "state", "speaking"],
    [0, "mood", "learned"],
    [0, "caption", {who: "oiva", text: "Close! 412 euros 30. Groceries are the biggest slice, then restaurants."}],
    [3600, "state", "listening"],
    [1400, "caption", {who: "you", text: "Is that a lot for someone like me?"}],
    [800, "state", "thinking"],
    [600, "screen", "compare"],
    [500, "state", "speaking"],
    [0, "caption", {who: "oiva", text: "Here are 761 people aged 35 to 44 in Helsinki. They typically keep 7 percent of what comes in."}],
    [3800, "mood", "caution"],
    [0, "caption", {who: "oiva", text: "Over the last six months, you spent 4 percent more than came in."}],
    [3400, "state", "listening"],
    [1400, "caption", {who: "you", text: "Okay. Can I set a restaurant budget?"}],
    [800, "state", "thinking"],
    [600, "screen", "budget"],
    [500, "state", "speaking"],
    [0, "mood", "saving"],
    [0, "caption", {who: "oiva", text: "People like you typically spend 296 euros a month on restaurants. Tap Restaurants to pick it, or type your own."}],
    [4200, "screen", "bills"],
    [0, "caption", {who: "oiva", text: "And your rent, 1 065 euros 17, comes on the 3rd. I can remind you the day before."}],
    [3800, "state", "listening"],
    [0, "summary", {title: "Your call with Oiva", lines: ["Spent so far in October: 412,30 €", "People like you keep 7 % of income; you spent 4 % more than came in", "Typical restaurant budget for people like you: 296,00 €"], unbacked_numbers: []}]
  ];
  const handlers = {};
  let timer = null, running = false;
  const emit = (ev, v) => (handlers[ev] || []).forEach(fn => { try { fn(v); } catch (e) { console.error(e); } });
  const speed = () => Math.max(1, Number(window.OIVA_DEMO_SPEED) || 1);

  function play(i) {
    if (!running || i >= SCRIPT.length) return;
    const [wait, ev, v] = SCRIPT[i];
    timer = setTimeout(() => { if (!running) return; emit(ev, v); play(i + 1); }, wait / speed());
  }

  window.OivaVoiceDemo = {
    demo: true,
    on(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); },
    off(ev) { delete handlers[ev]; },
    start() { running = true; emit("state", "connecting"); play(0); return Promise.resolve(); },
    stop() { if (!running) return; running = false; clearTimeout(timer); emit("state", "ended"); },
    mute() {}
  };
})();
