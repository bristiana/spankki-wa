// The chat's Helper (docs/plans/squirrel-helper.md): a big Oiva (oiva.js from the mascot set) that takes the
// pose the server picked for each reply (`mood`, app/mood.py), says one short sentence of the reply in a speech
// bubble (the full reply is in the log) and, with the sound on, reads the whole reply aloud (ElevenLabs through
// /api/voice, or the browser's speechSynthesis in fi-FI or en-GB by the page language when the server has no key).
(function () {
  const fi = (document.documentElement.lang || "fi").startsWith("fi");
  const T = (a, b) => (fi ? a : b);
  const TAGS = {
    compare: T("Muihin verrattuna", "Compared with others"),
    needs_approval: T("Odottaa hyväksyntääsi", "Needs your OK"),
    caution: T("Hetkinen", "Hold on"),
    safe_to_spend: T("Vapaata rahaa", "Free to spend"),
    what_if: T("Entä jos…", "What if…"),
    saving: T("Säästöön", "Saving"),
    teaching: T("Näin se toimii", "How it works"),
    learned: T("Hyvin meni!", "Nailed it"),
    spent: T("Mihin rahat meni", "Where it went"),
    working: T("Sinun lukusi", "Your numbers"),
    error: T("Hups", "Oops"),
    checking: T("Katsoin", "I had a look"),
    listening: T("Kuuntelen", "Listening"),
    thinking: T("Mietin…", "Thinking…"),
  };
  const phone = document.getElementById("phone");
  const stage = document.getElementById("stage"), pal = document.getElementById("pal");
  const say = stage && stage.querySelector(".say"), tag = document.getElementById("say-tag"), line = document.getElementById("say-text");
  const bars = document.getElementById("say-bars");
  const voiceBtn = document.getElementById("voice");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  let oiva = null, mood = "idle", idleTimer = 0, last = "", lastParts = [];

  // the reply states oiva.js has a pose for (app/mood.py); anything else is a plain answer
  const POSES = ["idle", "listening", "thinking", "working", "teaching", "checking", "needs_approval", "success", "error",
    "learned", "saving", "spent", "safe_to_spend", "caution", "what_if", "compare"];

  function plain(s) { return String(s || "").replace(/[*_]/g, "").replace(/\s+/g, " ").trim(); }
  // amounts never break across lines in the bubble: "3 345.06 €" keeps its spaces non-breaking
  function nb(s) { return s.replace(/\bS-(?=[A-Za-zÄÖäö])/g, "S\u2011").replace(/(\d) (?=\d{3}(?!\d))/g, "$1\u00a0").replace(/(\d) €/g, "$1\u00a0€"); }
  // The bubble: one sentence, not a copy of the log. For a comparison, the sentence that holds it (a spender
  // profile opens with the type and compares in its second part).
  const PEER = /\b(people|customers|typical|average|most|others|their|year[- ]olds|asiakk\w*|tyypilli\w*|keskim\w*|muut\w*|muill\w*|kaltais\w*|ikäis\w*|\w*vuotiai\w*)\b|\d+\s*[–-]\s*\d+[- ]?(year|vuot)/i;
  function sentences(s) { return plain(s).split(/(?<=[.!?…])\s+(?=["'(A-ZÄÖÅ0-9])/); }
  function first(parts, m) {
    // a list (one line per basket, say) reads badly as one sentence: the bubble takes the prose around it
    const prose = (parts || []).filter(p => !/\n/.test(String(p || "").trim()));
    const all = (prose.length ? prose : parts || []).flatMap(sentences).filter(Boolean);
    // a comparison: the sentence with both a peer word and an amount, else any peer sentence
    const peer = m === "compare" && (all.find(x => PEER.test(x) && /\d[\d\s.,]*\s?€/.test(x)) || all.find(x => PEER.test(x)));
    let s = peer || all[0] || "";
    // a long sentence keeps its point, the part before the colon ("…is a bit above theirs: 274.80 € …")
    const c = s.indexOf(": ");
    if (s.length > 110 && c > 30) s = s.slice(0, c) + ".";
    if (s.length > 110) {  // or the part before its second clause ("…454.79 € a month on groceries, and the middle half…")
      const m = /,\s(and|but|while|ja|mutta|kun)\s/.exec(s.slice(40));
      if (m) s = s.slice(0, 40 + m.index) + ".";
    }
    return s.length > 150 ? s.slice(0, 146).replace(/\s+\S*$/, "") + " …" : s;
  }
  // Two bars from the reply's own figures (its trace, explain.py), never parsed from the prose: you vs people like
  // you, the price vs what is free until payday, now vs after a cut, this period vs the one before.
  const amt = v => { const x = parseFloat(String(v || "").replace(/[\s\u00a0€]/g, "").replace(",", ".")); return isFinite(x) ? x : null; };
  function pairFor(m, trace, parts) {
    const figs = (trace || []).flatMap(t => (t && t.figures) || []);
    const get = l => { const f = figs.find(x => x.label === l); return f ? amt(f.value) : null; };
    // only figures the reply itself says, and both above zero (no "before: 0 €" when there was no earlier period)
    const said = String((parts || []).join(" ")).replace(/[\s\u00a0]/g, "").replace(/,/g, ".");
    const inReply = x => said.includes(x.toFixed(2));
    const two = (a, b, la, lb, over, zeroOk) => (a > 0 && b != null && (b > 0 || zeroOk) && (inReply(a) || inReply(b)))
      ? { a, b, la, lb, over } : null;
    if (m === "compare") {
      const you = get("Your month"), typ = get("Typical month");  // above typical is not drawn as "good" green
      const p = two(you, typ, T("Sinä", "You"), T("Tyypillinen", "Typical"), you > typ * 1.1);
      if (p) return p;  // a single category (spender profile) has no label in the page language: no bars
    }
    if (m === "caution" || m === "safe_to_spend") {
      const price = get("Price"), free = get("Safe until payday");
      if (price != null) return two(price, free, T("Hinta", "Price"), T("Vapaana", "Free now"), price > free, true);
    }
    if (m === "what_if") return two(get("Now per month"), get("After the cut"), T("Nyt", "Now"), T("Jälkeen", "After"));
    if (m === "spent") return two(get("Total"), get("Period before"), T("Tämä", "This time"), T("Edellinen", "Before"));
    return null;
  }
  function eur(x) {  // as the reply writes it: cents under 1 000 €, whole euros above
    const d = Math.abs(x) < 1000 ? 2 : 0;
    const s = x.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/,/g, " ");
    return nb((fi ? s.replace(".", ",") : s) + " €");
  }
  function drawBars(p) {
    if (!bars) return false;
    bars.textContent = "";
    if (!p) return false;
    const top = Math.max(p.a, p.b) || 1;
    [[p.la, p.a, p.over ? "over" : "you"], [p.lb, p.b, ""]].forEach(([l, v, c]) => {
      const s = document.createElement("span"); s.textContent = l;
      const i = document.createElement("i"); if (c) i.className = c;
      const b = document.createElement("b"); i.appendChild(b); b.dataset.w = Math.max(4, 100 * v / top).toFixed(1) + "%";
      const e = document.createElement("em"); e.textContent = eur(v);
      bars.append(s, i, e);
    });
    requestAnimationFrame(() => requestAnimationFrame(() => bars.querySelectorAll("b").forEach(b => b.style.width = b.dataset.w)));
    return true;
  }
  function pose(name) {
    mood = name;
    if (oiva) { oiva.setState(name); oiva.setFps(name === "idle" ? 20 : 30); }
  }
  function show(name, text, thinking, pair) {
    if (!say) return;
    const withBars = !thinking && drawBars(pair);
    say.classList.toggle("has-bars", withBars);
    if (withBars && text.length > 80) text = "";  // the bars already say it; no clipped sentence under them
    say.dataset.mood = name;
    say.classList.remove("rest");
    tag.textContent = TAGS[name] || "";
    line.textContent = nb(text);
    say.classList.toggle("thinking", !!thinking);
    say.classList.remove("pop"); void say.offsetWidth; say.classList.add("pop");
  }
  function restLater(ms) {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(function rest() {
      if (speaking()) { idleTimer = setTimeout(rest, 2000); return; }
      pose("idle");
      if (say) say.classList.add("rest");  // the tag fades with the pose; the sentence stays readable
    }, ms);
  }

  // ---- voice ----
  // Oiva's own voice from the server (/api/voice, ElevenLabs; the key stays there). With no key on the server
  // (204) or when a call fails, the browser's speechSynthesis reads it instead. Spoken only after a reply arrives.
  const canSynth = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  const canSpeak = canSynth || typeof Audio !== "undefined";
  let voiceOn = store.get("oiva-voice") === "on";
  let serverVoice = true;        // false after a 204: no key on the server, don't ask again this visit
  const clips = new Map();       // text -> blob URL, so a repeated sentence isn't fetched twice
  const player = typeof Audio !== "undefined" ? new Audio() : null;
  let pending = null, seq = 0;
  const LANG = fi ? "fi-FI" : "en-GB";
  const SILENT = "data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA==";  // 10 ms of silence
  function voiceFor() {
    const vs = speechSynthesis.getVoices(), norm = x => (x.lang || "").replace("_", "-").toLowerCase();
    return vs.find(x => norm(x) === LANG.toLowerCase()) || vs.find(x => norm(x).startsWith(LANG.slice(0, 2).toLowerCase()));
  }
  function speaking() { return (canSynth && speechSynthesis.speaking) || !!(player && !player.paused && !player.ended) || !!pending; }
  function hush() {
    seq++;
    if (pending) { pending.abort(); pending = null; }
    if (player) player.pause();
    if (canSynth) speechSynthesis.cancel();
  }
  function synth(text) {
    if (!canSynth) return;
    const u = new SpeechSynthesisUtterance(text.replace(/€/g, fi ? " euroa" : " euros"));
    u.lang = LANG;
    const v = voiceFor();
    if (v) u.voice = v;
    u.rate = 1.02;
    speechSynthesis.speak(u);
  }
  function play(url, text, my) {
    player.src = url;
    player.play().catch(() => { if (my === seq) synth(text); });  // autoplay refused: the browser's voice
  }
  async function speak(parts) {
    if (!canSpeak || !voiceOn) return;
    const text = plain((parts || []).join(". "));
    if (!text) return;
    hush();
    const my = seq;
    if (!player || !serverVoice) return synth(text);
    if (clips.has(text)) { const u = clips.get(text); clips.delete(text); clips.set(text, u); return play(u, text, my); }
    const ctl = new AbortController(); pending = ctl;
    try {
      const r = await fetch("/api/voice", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, lang: fi ? "fi" : "en" }), signal: ctl.signal });
      if (pending === ctl) pending = null;
      if (my !== seq) return;  // a newer reply or mute came first
      if (r.status === 204 || r.status === 429) { serverVoice = false; return synth(text); }  // no key, or the day's budget used
      if (!r.ok) return synth(text);
      const url = URL.createObjectURL(await r.blob());
      if (clips.size >= 30) { const [k, u] = clips.entries().next().value; if (player.src !== u) URL.revokeObjectURL(u); clips.delete(k); }
      clips.set(text, url);
      if (my === seq) play(url, text, my);
    } catch (e) {
      if (pending === ctl) pending = null;
      if (my === seq && e.name !== "AbortError") synth(text);
    }
  }
  function setVoice(v, sayNow) {
    voiceOn = v; store.set("oiva-voice", v ? "on" : "off");
    if (voiceBtn) { voiceBtn.setAttribute("aria-pressed", v); voiceBtn.querySelector("span").textContent = v ? T("Ääni", "Sound") : T("Mykistetty", "Muted"); }
    if (!v) hush();
    if (v && sayNow && lastParts.length) speak(lastParts);
  }
  if (voiceBtn && canSpeak) {
    voiceBtn.classList.add("can");
    voiceBtn.onclick = () => {
      // iOS lets an <audio> play later only if it started inside a tap: unlock it here with a silent clip
      if (!voiceOn && player) { player.src = SILENT; player.play().catch(() => {}); }
      setVoice(!voiceOn, true);
    };
    setVoice(voiceOn, false);  // remembered per browser; off until turned on
    // Sound already on from an earlier visit: the first tap or key unlocks playback (iOS needs a gesture)
    const unlock = () => {
      if (!voiceOn) return;
      if (player && player.paused) { player.src = SILENT; player.play().catch(() => {}); }
      if (canSynth && !speechSynthesis.speaking) speechSynthesis.speak(new SpeechSynthesisUtterance(""));
    };
    document.addEventListener("pointerdown", unlock, { once: true, capture: true });
    document.addEventListener("keydown", unlock, { once: true, capture: true });
  }

  // ---- the big Oiva (oiva.js); a tap pokes him ----
  if (pal && window.Oiva) oiva = window.Oiva.create(pal, { palette: "red", state: mood, hoverReact: true, fps: 20 });
  const log = document.getElementById("log");
  if (log) log.scrollTop = log.scrollHeight;

  // ---- keyboard: a smaller stage when the visual viewport is short ----
  if (window.visualViewport) {
    const fit = () => { phone.classList.toggle("kb", visualViewport.height < 560); };
    visualViewport.addEventListener("resize", fit); fit();
  }

  // ---- where he looks: at what you type, then at the bubble he answers in ----
  let lookTimer = 0;
  function glance(el, ms) {
    if (!oiva || !el) return;
    const r = el.getBoundingClientRect();
    oiva.lookAt(r.left + Math.min(r.width / 2, 120), r.top + r.height / 2);
    clearTimeout(lookTimer);
    lookTimer = setTimeout(() => oiva && oiva.lookAt(null), ms);
  }

  // ---- what the chat tells us ----
  window.oivaHelper = {
    listening() {  // only the pose: the last reply's sentence stays in the bubble while you type
      if (mood === "thinking") return;
      clearTimeout(idleTimer);
      if (mood !== "listening") pose("listening");
      glance(document.getElementById("q"), 4000);
      restLater(8000);
    },
    thinking() {
      clearTimeout(idleTimer); pose("thinking");
      hush();
      show("thinking", "", true);
    },
    reply(name, parts, trace) {
      const m = POSES.includes(name) ? name : "success";
      last = first(parts, m); lastParts = parts || [];
      pose(m);
      show(m, last || T("Tässä.", "Here you go."), false, pairFor(m, trace, parts));
      glance(say, 2500);
      speak(parts);
      restLater(12000);
    },
    mood: () => mood,
  };
  const hello = document.getElementById("hello");
  if (hello) {  // the greeting: its first two sentences ("Hi Antti! I'm Oiva, your S-Pankki money coach.")
    last = plain(hello.textContent); lastParts = [hello.textContent]; show("", last);
  }
  pose("idle");
  const q = document.getElementById("q");
  if (q) q.addEventListener("input", () => { if (q.value.trim()) window.oivaHelper.listening(); });
})();
