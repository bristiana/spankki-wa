// The Helper's mic: tap, speak your question, and it lands in the message box to check and send.
// With a key on the server (data-server="1") the clip is recorded here (MediaRecorder: audio/webm on Chrome and
// Android, audio/mp4 on Safari and iPhone) and written down by ElevenLabs Speech-to-Text through /api/listen
// (app/stt.py; the key stays on the server). Without one, the browser's own speech recognition, where it has one.
// Neither: no mic button. Tap again to stop; it also stops by itself after a pause in speech, or at 30 s.
// While you speak, Oiva stops talking and takes his listening pose (helper.js hearStart / hearWait / hearEnd).
(function () {
  const btn = document.getElementById("mic"), q = document.getElementById("q");
  const note = document.getElementById("mic-note"), sendBtn = document.getElementById("send");
  if (!btn || !q) return;
  const fi = (document.documentElement.lang || "fi").startsWith("fi");
  const T = (a, b) => (fi ? a : b);
  const H = () => window.oivaHelper || {};
  const MAX_MS = 30000, MAX_BYTES = 1400000;  // app/stt.py MAX_BYTES is a little more
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  let mode = btn.dataset.server === "1" && canRecord ? "server" : SR ? "browser" : null;
  if (!mode) return;  // no way to hear you here: the button stays hidden
  btn.hidden = false;

  const SAY = {
    tap: T("Kuuntelen… napauta ■, kun olet valmis", "Listening… tap ■ when you're done"),
    wait: T("Kirjoitan ylös…", "Writing it down…"),
    check: T("Tarkista viesti ja paina Lähetä", "Check it, then tap Send"),
    denied: T("Mikrofoni on estetty. Salli se selaimen asetuksista, tai kirjoita kysymys.",
      "The microphone is blocked. Allow it in your browser settings, or type your question."),
    nomic: T("Mikrofonia ei löytynyt. Kirjoita kysymys tähän.", "No microphone found. Type your question here."),
    none: T("En kuullut mitään. Yritä uudelleen hieman lähempänä.", "I didn't hear anything. Try again a bit closer."),
    long: T("Se oli vähän pitkä. Kokeile lyhyempää kysymystä.", "That was a bit long for me. Try a shorter question."),
    much: T("Puhetta on tältä erää tarpeeksi. Kirjoita kysymys tähän.", "That's enough voice for now. Type your question instead."),
    fail: T("Puheen tunnistus ei nyt toiminut. Yritä uudelleen tai kirjoita.", "Voice didn't work just now. Try again, or type it."),
  };
  let state = "idle";  // idle | starting | rec | wait
  let noteTimer = 0;
  function tell(text, cls, ms) {
    if (!note) return;
    clearTimeout(noteTimer);
    note.className = cls || ""; note.textContent = text || ""; note.hidden = !text;
    if (text && ms) noteTimer = setTimeout(() => { note.hidden = true; }, ms);
  }
  // with Oiva on the stage he says what went wrong in his bubble; the line above the box then only tells screen readers
  const staged = () => !!(window.oivaHelper && document.getElementById("stage"));
  function set(s) {
    state = s;
    btn.classList.toggle("rec", s === "rec" || s === "starting");
    btn.classList.toggle("wait", s === "wait");
    btn.disabled = s === "wait";
    btn.setAttribute("aria-pressed", s === "rec" || s === "starting" ? "true" : "false");
    btn.setAttribute("aria-label", s === "rec" ? T("Lopeta ja kirjoita ylös", "Stop and write it down") : T("Puhu viesti", "Speak your message"));
  }
  function landed(text) {  // into the box, after anything already typed; you check it and send
    text = String(text || "").trim();
    if (!text) return false;
    const had = q.value.trim();
    q.value = had ? had + " " + text : text;
    if (sendBtn) { sendBtn.classList.add("ready"); }
    tell(SAY.check, "", 4000);
    H().hearEnd && H().hearEnd("", text);
    return true;
  }
  q.addEventListener("input", () => sendBtn && sendBtn.classList.remove("ready"));
  document.getElementById("f") && document.getElementById("f").addEventListener("submit", () => {
    if (sendBtn) sendBtn.classList.remove("ready");
    if (state === "rec" || state === "starting") cancel();
    tell("");
  });
  function fail(text) { tell(text, staged() ? "err sr" : "err", 7000); H().hearEnd && H().hearEnd(text); set("idle"); }

  // ---- the server way: record, then ElevenLabs writes it down ----
  let stream = null, rec = null, chunks = [], ac = null, meter = 0, stopAt = 0, startedAt = 0, heard = false, dropped = false;
  const TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  function pick() {
    try { return TYPES.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || ""; } catch (e) { return ""; }
  }
  function release() {
    clearInterval(meter); clearTimeout(stopAt); meter = 0;
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
    if (ac) { try { ac.close(); } catch (e) {} ac = null; }
  }
  // a pause after you spoke ends it: loudness from an analyser on the mic (it never plays anything), against the
  // room's own noise level measured in the first moments
  function watch(s) {
    const AC = window.AudioContext || window.webkitAudioContext;
    let lv = null;
    if (note) { lv = document.createElement("span"); lv.className = "lv"; lv.innerHTML = "<i></i>"; note.appendChild(lv); lv = lv.firstChild; }
    if (!AC) return;
    try {
      ac = new AC();
      if (ac.state === "suspended" && ac.resume) ac.resume().catch(() => {});
      const an = ac.createAnalyser(); an.fftSize = 1024;
      ac.createMediaStreamSource(s).connect(an);
      const buf = new Uint8Array(an.fftSize);
      let floor = 0, n = 0, quietSince = 0;
      meter = setInterval(() => {
        an.getByteTimeDomainData(buf);
        let sum = 0; for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
        const rms = Math.sqrt(sum / buf.length), t = Date.now() - startedAt;
        if (lv) lv.style.width = Math.min(100, rms * 900).toFixed(0) + "%";
        if (t < 400) { floor = (floor * n + rms) / ++n; return; }
        const loud = rms > Math.max(0.025, floor * 3);
        if (loud) { heard = true; quietSince = 0; return; }
        if (!quietSince) quietSince = Date.now();
        if (heard && Date.now() - quietSince > 1600) stop();       // you finished
        else if (!heard && t > 8000) { dropped = true; stop(); }  // nothing said at all
      }, 100);
    } catch (e) { ac = null; }  // no analyser: you stop it, or the 30 s do
  }
  async function startServer() {
    set("starting"); H().hearStart && H().hearStart();
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      release();
      return fail(e && (e.name === "NotAllowedError" || e.name === "SecurityError") ? SAY.denied
        : e && (e.name === "NotFoundError" || e.name === "OverconstrainedError") ? SAY.nomic : SAY.fail);
    }
    if (state !== "starting") { release(); return; }  // cancelled while asking
    const type = pick();
    try { rec = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 32000 } : undefined); }
    catch (e) { try { rec = new MediaRecorder(stream); } catch (e2) { release(); return fail(SAY.fail); } }
    chunks = []; heard = false; dropped = false;
    rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => done(type);
    rec.onerror = () => { release(); fail(SAY.fail); };
    startedAt = Date.now();
    rec.start(1000);
    set("rec");
    tell(SAY.tap, "rec");
    watch(stream);
    stopAt = setTimeout(stop, MAX_MS);
  }
  function stop() {
    clearInterval(meter); clearTimeout(stopAt); meter = 0;
    if (rec && rec.state !== "inactive") { try { rec.stop(); } catch (e) { release(); set("idle"); } }
  }
  function cancel() { dropped = true; if (rec && rec.state !== "inactive") { rec.onstop = () => { release(); }; try { rec.stop(); } catch (e) {} } release(); set("idle"); H().hearEnd && H().hearEnd(); }
  async function done(type) {
    const len = Date.now() - startedAt;
    release();
    const blob = new Blob(chunks, { type: (rec && rec.mimeType) || type || "audio/mp4" });
    chunks = []; rec = null;
    if (dropped || len < 500 || blob.size < 1000) return fail(SAY.none);
    if (blob.size > MAX_BYTES) return fail(SAY.long);
    set("wait"); tell(SAY.wait); H().hearWait && H().hearWait();
    let r = null, j = null;
    try {
      const c = new AbortController(), t = setTimeout(() => c.abort(), 30000);
      r = await fetch("/api/listen?lang=" + (fi ? "fi" : "en"), { method: "POST", body: blob, signal: c.signal,
        headers: { "Content-Type": (blob.type || "audio/mp4").split(";")[0] } });
      clearTimeout(t);
      j = r.status === 200 ? await r.json() : null;
    } catch (e) { return fail(SAY.fail); }
    if (r.status === 204) {  // the server has no key after all: the browser's recognition, or no mic
      mode = SR ? "browser" : null;
      if (!mode) btn.hidden = true;
      return fail(SAY.fail);
    }
    if (j && landed(j.text)) { set("idle"); return; }
    fail(r.status === 422 || r.status === 200 ? SAY.none : r.status === 413 ? SAY.long : r.status === 429 ? SAY.much : SAY.fail);
  }

  // ---- the browser way (no key on the server): its own speech recognition ----
  let sr = null, srText = "";
  function startBrowser() {
    try {
      sr = new SR();
    } catch (e) { return fail(SAY.fail); }
    sr.lang = fi ? "fi-FI" : "en-GB"; sr.interimResults = true; sr.continuous = false; sr.maxAlternatives = 1;
    srText = ""; let err = "";
    const had = q.value.trim();
    sr.onresult = e => {
      srText = Array.from(e.results).map(x => x[0].transcript).join(" ").trim();
      q.value = had ? had + " " + srText : srText;  // you see it being written
    };
    sr.onerror = e => { err = e.error; };
    sr.onend = () => {
      sr = null; clearTimeout(stopAt);
      if (err === "not-allowed" || err === "service-not-allowed") return fail(SAY.denied);
      if (err === "audio-capture") return fail(SAY.nomic);
      if (!srText) return fail(err && err !== "no-speech" && err !== "aborted" ? SAY.fail : SAY.none);
      q.value = had; landed(srText); set("idle");
    };
    set("rec"); H().hearStart && H().hearStart(); tell(SAY.tap, "rec");
    try { sr.start(); } catch (e) { sr = null; return fail(SAY.fail); }
    stopAt = setTimeout(() => sr && sr.stop(), MAX_MS);
  }

  btn.addEventListener("click", () => {
    if (state === "wait") return;
    if (state === "rec" || state === "starting") {
      if (mode === "browser") { if (sr) sr.stop(); return; }
      if (state === "starting") return cancel();
      return stop();
    }
    if (sendBtn) sendBtn.classList.remove("ready");
    (mode === "server" ? startServer : startBrowser)();
  });
  // the page goes to the background (a call, the lock screen) while recording: drop it rather than send half
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && (state === "rec" || state === "starting")) {
      if (mode === "browser") { if (sr) sr.abort(); } else cancel();
      tell("");
    }
  });
})();
