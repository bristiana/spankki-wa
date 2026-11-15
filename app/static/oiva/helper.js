// The chat's Helper (docs/plans/squirrel-helper.md): a big Oiva (oiva.js from the mascot set) that takes the
// pose the server picked for each reply (`mood`, app/mood.py), says one short sentence of the reply in a speech
// bubble (the full reply is in the log) and reads the whole reply aloud unless muted (ElevenLabs through /api/voice
// in an English or Finnish voice by the page language, or the browser's speechSynthesis when the server has no key).
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
    heard: T("Kuulin näin, tarkista ja lähetä", "I heard this, check and send"),
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
  let oiva = null, mood = "idle", idleTimer = 0, last = "", lastParts = [], hearing = false;

  // the reply states oiva.js has a pose for (app/mood.py); anything else is a plain answer
  const POSES = ["idle", "listening", "thinking", "working", "teaching", "checking", "needs_approval", "success", "error",
    "learned", "saving", "spent", "safe_to_spend", "caution", "what_if", "compare"];

  function plain(s) { return String(s || "").replace(/[*_]/g, "").replace(/\s+/g, " ").trim(); }
  // amounts never break across lines in the bubble: "3 345.06 €" keeps its spaces non-breaking
  function nb(s) { return s.replace(/\bS-(?=[A-Za-zÄÖäö])/g, "S\u2011").replace(/(\d) (?=\d{3}(?!\d))/g, "$1\u00a0").replace(/(\d) €/g, "$1\u00a0€"); }
  // Finnish writes money with a decimal comma ("235,68 €", "3 345,06 €"), as the rest of the app does; the reply's
  // own text says "235.68 €", so the bubble turns the point of an amount or a percentage into a comma on FI. Only a
  // number next to €, %, "euroa" (or the end of a range before one) changes: a date (31.10.2026) or a time (14.30) stays.
  const UNIT = "[\\s\\u00a0]?(?:€|%|euro)";
  const COMMA_K = new RegExp("(\\d),(?=\\d{3}(?:,\\d{3})*\\.\\d+" + UNIT + ")", "g");          // 3,345.06 € -> 3 345.06 €
  const POINT = new RegExp("(\\d)\\.(\\d+)(?=(?:[–-]\\d+(?:\\.\\d+)?)?" + UNIT + ")", "g");  // 12.50 €, 1.5–2.5 %, 12.50 euroa
  function fiNum(s) {
    return s.replace(COMMA_K, "$1 ").replace(POINT, "$1,$2").replace(/(€[\s\u00a0]?-?\d+)\.(\d+)/g, "$1,$2");  // and € 12.50
  }
  const bubble = s => nb(fi ? fiNum(String(s || "")) : String(s || ""));
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
    if (m === "spent") {  // more spent this time than before is not drawn as "good" green; less or the same is
      const now = get("Total"), before = get("Period before");
      return two(now, before, T("Tämä", "This time"), T("Edellinen", "Before"), now > before);
    }
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
    line.textContent = bubble(text);
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
  // Oiva reads his whole reply aloud after each question: ElevenLabs through /api/voice (the key stays on the
  // server; an English voice on EN, a Finnish one on FI), a few sentences per request so he starts talking
  // quickly, played back to back. With no key on the server (204), the day's budget used (429) or a failed call,
  // the browser's speechSynthesis reads it instead. On by default; the sound button mutes him (remembered).
  // A new question, a new reply or mute stops what he is saying.
  const canSynth = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  const canSpeak = canSynth || typeof Audio !== "undefined";
  let voiceOn = store.get("oiva-voice") !== "off";
  let serverVoice = true;        // false after a 204 or 429: don't ask the server again this visit
  const clips = new Map();       // text -> blob URL, so a repeated piece isn't fetched twice
  const player = typeof Audio !== "undefined" ? new Audio() : null;
  let ctl = null, seq = 0, talking = false, stopClip = null;
  const LANG = fi ? "fi-FI" : "en-GB";
  const SILENT = "data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA==";  // 10 ms of silence
  function voiceFor() {
    const vs = speechSynthesis.getVoices(), norm = x => (x.lang || "").replace("_", "-").toLowerCase();
    return vs.find(x => norm(x) === LANG.toLowerCase()) || vs.find(x => norm(x).startsWith(LANG.slice(0, 2).toLowerCase()));
  }
  // ---- his mouth while the voice plays (oiva.js v3 talking layer) ----
  // He talks only while sound actually comes out: the clip's own 'playing' event opens his mouth; ended, waiting,
  // pause, error, a new question or mute close it. The opening follows the clip's loudness: each clip is measured
  // once when it arrives (decoded offline into one loudness value per 20 ms, never routed through Web Audio, so the
  // voice plays exactly as before, also on an iPhone on silent), and read at the clip's current time. While a clip
  // is still being measured he babbles. The browser voice has no sound to measure: he babbles while it speaks.
  const STEP = 0.02, envs = new Map();  // blob URL -> loudness 0..1 per 20 ms
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  let decoder = null;
  async function measure(url, blob) {
    try {
      if (!OAC) return;
      decoder = decoder || new OAC(1, 1, 22050);
      const raw = await blob.arrayBuffer();
      const buf = await new Promise((ok, no) => { const r = decoder.decodeAudioData(raw, ok, no); if (r && r.then) r.then(ok, no); });
      const d = buf.getChannelData(0), n = Math.max(1, Math.round(buf.sampleRate * STEP));
      const env = new Float32Array(Math.ceil(d.length / n));
      let peak = 0;
      for (let i = 0; i < env.length; i++) {
        let sum = 0; const end = Math.min(d.length, (i + 1) * n);
        for (let j = i * n; j < end; j++) sum += d[j] * d[j];
        env[i] = Math.sqrt(sum / Math.max(1, end - i * n)); if (env[i] > peak) peak = env[i];
      }
      if (peak > 0) for (let i = 0; i < env.length; i++) { const v = env[i] / peak; env[i] = v < 0.12 ? 0 : Math.min(1, (v - 0.12) * 1.6); }
      if ([...clips.values()].includes(url)) envs.set(url, env);  // still cached (not evicted meanwhile)
    } catch (e) {}  // not measurable: he babbles while it plays
  }
  function level() {  // 0..1 mouth opening from the clip right now; null = not measured yet (oiva.js babbles)
    if (!player || player.paused || player.ended) return 0;
    const env = envs.get(player.src);
    return env ? env[Math.floor(player.currentTime / STEP)] || 0 : null;
  }
  let synthDog = 0;
  function synthLevel() { return speechSynthesis.speaking && !speechSynthesis.paused ? null : 0; }  // shut if it stalls
  let mouthOn = false;
  function mouth(on, how) {
    if (on === mouthOn && !how) return;
    mouthOn = on;
    if (!oiva) return;
    if (on) oiva.talk(null, { level: how === "synth" ? synthLevel : level });
    else oiva.stopTalking();
  }
  function doneTalking() {  // the reply was read to the end: he stops and holds the reply's pose a while
    mouth(false);
    if (mood !== "idle" && mood !== "thinking") restLater(8000);
  }
  if (player) {
    player.addEventListener("playing", () => { if (talking && !player.paused && player.src !== SILENT) mouth(true); });
    player.addEventListener("waiting", () => mouth(false));  // buffering: quiet until 'playing' again
  }

  function speaking() { return talking || (canSynth && speechSynthesis.speaking) || !!(player && !player.paused && !player.ended); }
  function hush() {
    seq++; talking = false; mouth(false);
    if (ctl) { ctl.abort(); ctl = null; }
    if (stopClip) { stopClip(); stopClip = null; }
    if (player) player.pause();
    if (canSynth) speechSynthesis.cancel();
  }
  // What is read: the reply's parts, list lines as sentences, without markdown, links, emoji or list markers
  // (the server also says amounts in words). Buttons, charts and "How I worked this out" are not in the parts.
  function spoken(parts) {
    const lines = [];
    for (const p of parts || []) for (let l of String(p || "").split(/\n+/)) {
      l = l.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/https?:\/\/\S+/g, "")
        .replace(/^\s*(?:[-•*·]|\d+[.)])\s+/, "").replace(/[*_`#>~]/g, "")
        .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}]/gu, "").replace(/\s+/g, " ").trim();
      if (l) lines.push(/[.!?:;…]$/.test(l) ? l : l + ".");
    }
    return lines.join(" ");
  }
  // pieces of whole sentences: a short first one so he starts at once, then up to ~350 characters each
  function pieces(text) {
    const out = []; let cur = "";
    for (const s of text.split(/(?<=[.!?…])\s+(?=["'(A-ZÄÖÅ0-9])/)) {
      const max = out.length ? 350 : 160;
      if (cur && (cur + " " + s).length > max) { out.push(cur); cur = s; } else cur = cur ? cur + " " + s : s;
    }
    if (cur) out.push(cur);
    return out;
  }
  function synth(chunks) {
    if (!canSynth) { doneTalking(); return; }
    const my = seq;
    chunks.forEach((c, n) => {  // one utterance per piece: some browsers cut a long one off
      const u = new SpeechSynthesisUtterance(c.replace(/€/g, fi ? " euroa" : " euros"));
      u.lang = LANG;
      const v = voiceFor();
      if (v) u.voice = v;
      u.rate = 1.02;
      u.onstart = () => {  // a watchdog: a stalled browser voice (no onend) can't leave him babbling
        if (my !== seq) return;
        mouth(true, "synth"); clearTimeout(synthDog);
        synthDog = setTimeout(() => { if (my === seq) mouth(false); }, 3000 + c.length * 160);
      };
      if (n === chunks.length - 1) u.onend = () => { if (my === seq) doneTalking(); };
      u.onerror = () => { if (my === seq) (n === chunks.length - 1 ? doneTalking() : mouth(false)); };  // the last one: done all the same
      speechSynthesis.speak(u);
    });
  }
  async function clip(text, signal) {  // a blob URL, or the HTTP status that says why not
    if (clips.has(text)) { const u = clips.get(text); clips.delete(text); clips.set(text, u); return u; }
    const r = await fetch("/api/voice", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, lang: fi ? "fi" : "en" }), signal });
    if (!r.ok || r.status === 204) return r.status;
    const blob = await r.blob(), url = URL.createObjectURL(blob);
    if (clips.size >= 40) { const [k, u] = clips.entries().next().value; if (player.src !== u) URL.revokeObjectURL(u); clips.delete(k); envs.delete(u); }
    clips.set(text, url);
    measure(url, blob);
    return url;
  }
  function playClip(url) {  // resolves when it ends (true) or is stopped (false); rejects if playback is refused
    return new Promise((ok, no) => {
      let over = false;
      const mine = () => !over && stopClip === stop;
      const end = () => { over = true; player.onended = player.onerror = player.onpause = null; stopClip = null; };
      const stop = () => { if (mine()) { end(); ok(false); } };
      stopClip = stop;
      player.onended = () => { mouth(false); if (mine()) { end(); ok(true); } };  // quiet until the next piece plays
      player.onerror = () => { mouth(false); if (mine()) { end(); ok(true); } };  // a broken clip: go on with the next one
      player.onpause = () => { if (!player.ended) stop(); };  // paused from outside (lock screen, a call): he stops
      player.src = url;
      player.play().catch(e => { if (mine()) { end(); no(e); } });  // a late refusal of an old clip touches nothing
    });
  }
  async function speak(parts) {
    if (!canSpeak || !voiceOn) return;
    const text = spoken(parts);
    if (!text) return;
    hush();
    const my = seq, chunks = pieces(text);
    if (!player || !serverVoice) return synth(chunks);
    const c = new AbortController(); ctl = c; talking = true;
    // ask for every piece at once (ElevenLabs works on the next while this one plays), play them in order
    const asks = chunks.map(t => clip(t, c.signal).catch(e => (e.name === "AbortError" ? 0 : -1)));
    let i = 0;
    try {
      for (; i < chunks.length; i++) {
        const got = await asks[i];
        if (my !== seq) return;
        if (typeof got !== "string") {  // no key / budget used: browser voice from here on, this visit
          if (got === 204 || got === 429) serverVoice = false;
          mouth(false);
          return synth(chunks.slice(i));
        }
        const played = await playClip(got);
        if (my !== seq || !played) { if (my === seq) mouth(false); return; }  // replaced, muted or paused from outside
      }
      if (my === seq) doneTalking();
    } catch (e) {  // autoplay refused (no tap yet on this page): the browser's voice
      if (my === seq) { mouth(false); synth(chunks.slice(i)); }  // from the piece that was refused, not again from the start
    } finally {
      if (my === seq) { talking = false; if (ctl === c) ctl = null; }
    }
  }
  function unlock() {  // iOS lets an <audio> play later only if it started inside a tap: a silent clip does that
    if (!voiceOn || !player || !player.paused || speaking()) return;
    player.src = SILENT; player.play().catch(() => {});
  }
  function setVoice(v, sayNow) {
    voiceOn = v; store.set("oiva-voice", v ? "on" : "off");
    if (voiceBtn) { voiceBtn.setAttribute("aria-pressed", v); voiceBtn.querySelector("span").textContent = v ? T("Ääni", "Sound") : T("Mykistetty", "Muted"); }
    if (!v) hush();
    if (v && sayNow && lastParts.length) speak(lastParts);
  }
  if (voiceBtn && canSpeak) {
    voiceBtn.classList.add("can");
    voiceBtn.onclick = () => {  // unlock inside the tap, before speak() makes him "talking"
      const on = !voiceOn;
      if (on) { voiceOn = true; unlock(); }
      setVoice(on, true);
    };
    setVoice(voiceOn, false);  // on unless muted earlier in this browser
    // the first tap or key on the page unlocks playback, so the reply to the first question is heard
    const first = () => { unlock(); if (voiceOn && canSynth && !speechSynthesis.speaking) speechSynthesis.speak(new SpeechSynthesisUtterance("")); };
    document.addEventListener("pointerdown", first, { once: true, capture: true });
    document.addEventListener("keydown", first, { once: true, capture: true });
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
      if (mood === "thinking" || hearing) return;
      clearTimeout(idleTimer);
      if (mood !== "listening") pose("listening");
      glance(document.getElementById("q"), 4000);
      restLater(8000);
    },
    thinking() {  // a new question: he stops talking; the send tap keeps the next reply's audio allowed
      hearing = false;
      clearTimeout(idleTimer); pose("thinking");
      hush(); unlock();
      show("thinking", "", true);
    },
    reply(name, parts, trace) {
      const m = POSES.includes(name) ? name : "success";
      last = first(parts, m); lastParts = parts || [];
      pose(m);
      const pair = pairFor(m, trace, parts);
      // with bars, a sentence about another figure (last month vs the bars' three months) would contradict them
      const says = x => plain(last).replace(/[\s\u00a0]/g, "").replace(/,/g, ".").includes(x.toFixed(2));
      show(m, pair && !says(pair.a) && !says(pair.b) ? "" : (last || T("Tässä.", "Here you go.")), false, pair);
      glance(say, 2500);
      if (hearing) return;  // you are speaking to him (the mic): he doesn't talk over you; the sound button repeats it
      speak(parts);
      restLater(12000);
    },
    // the mic (listen.js): he stops talking and listens while you speak, waits while it is written down, then
    // shows the last thing he said again (or why it didn't work)
    hearStart() {
      hearing = true;
      clearTimeout(idleTimer); hush();
      pose("listening");
      show("listening", T("Kerro vain, kuuntelen!", "Go ahead, I'm all ears!"));
    },
    hearWait() { if (hearing) show("listening", "", true); },
    hearEnd(note, heard) {  // why it didn't work, or what he heard (it is in the message box to check and send)
      if (!hearing) return;
      hearing = false;
      if (note) { pose("error"); show("error", note); restLater(6000); return; }
      if (heard) { pose("success"); show("heard", "“" + heard + "”"); restLater(8000); return; }
      show("listening", last || ""); if (say) say.classList.add("rest");
      restLater(6000);
    },
    mood: () => mood,
    _test: { bubble, pairFor },  // tests/test_mood.py runs these in node
  };
  const hello = document.getElementById("hello");
  if (hello) {  // the greeting: its first two sentences ("Hi Antti! I'm Oiva, your S-Pankki money coach.")
    last = plain(hello.textContent); lastParts = [hello.textContent]; show("", last);
  }
  pose("idle");
  const q = document.getElementById("q");
  if (q) q.addEventListener("input", () => { if (q.value.trim()) window.oivaHelper.listening(); });
})();
