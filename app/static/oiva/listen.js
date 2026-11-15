// The Helper's mic: tap, speak your question, and it lands in the message box to check and send.
// Tapping Send while it records stops it, writes it down and sends it.
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
  const MAX_MS = 30000, MAX_BYTES = 590000;  // app/stt.py MAX_BYTES is a little more
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
    again: T("Napauta mikrofonia uudelleen ja salli mikrofoni.", "Tap the mic again and allow the microphone."),
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
    if (sendAfter) { sendAfter = false; tell(""); H().hearEnd && H().hearEnd("", text); setTimeout(sendNow); return true; }
    tell(SAY.check, "", 4000);
    H().hearEnd && H().hearEnd("", text);
    return true;
  }
  q.addEventListener("input", () => sendBtn && sendBtn.classList.remove("ready"));
  // Send tapped while recording (or while it is being written down): stop, write it down, then send it, rather
  // than throwing the recording away. This listener comes before the chat's own onsubmit, so it can hold that back.
  let sendAfter = false;
  function sendNow() { const f = document.getElementById("f"); if (f && f.requestSubmit) f.requestSubmit(); else if (sendBtn) sendBtn.click(); }
  document.getElementById("f") && document.getElementById("f").addEventListener("submit", e => {
    if (state === "rec" || state === "wait") {
      e.preventDefault(); e.stopImmediatePropagation();
      sendAfter = true;
      if (state === "rec") { if (mode === "browser") { if (sr) sr.stop(); } else stop(); }
      return;
    }
    if (sendBtn) sendBtn.classList.remove("ready");
    if (state === "rec" || state === "starting") cancel();
    tell("");
  });
  function fail(text) {
    tell(text, staged() ? "err sr" : "err", 7000); H().hearEnd && H().hearEnd(text); set("idle");
    if (sendAfter) { sendAfter = false; if (q.value.trim()) setTimeout(sendNow); }  // Send was tapped: send what is typed
  }

  // ---- the server way: record, then ElevenLabs writes it down ----
  // `gen` numbers each start: a late answer from an earlier one (its mic prompt, its recorder) touches nothing
  let gen = 0, stream = null, rec = null, chunks = [], ac = null, meter = 0, stopAt = 0, startedAt = 0, heard = false, dropped = false;
  const TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  function pick() {
    try { return TYPES.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || ""; } catch (e) { return ""; }
  }
  // iPhone: while the mic is open the page is in play-and-record; back to normal after, so his voice comes out of
  // the speaker again and not quietly from the earpiece (Safari 16.4+)
  function session(type) { try { if (navigator.audioSession) navigator.audioSession.type = type; } catch (e) {} }
  function release() {
    clearInterval(meter); clearTimeout(stopAt); meter = 0;
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
    if (ac) { try { const p = ac.close(); if (p && p.catch) p.catch(() => {}); } catch (e) {} ac = null; }
    session("auto");
  }
  function drop(r) { if (r) { r.ondataavailable = r.onstop = r.onerror = null; if (r.state !== "inactive") try { r.stop(); } catch (e) {} } }
  // a pause after you spoke ends it: loudness from an analyser on the mic (it never plays anything), against the
  // room's own noise level in the first moments. The 8 s "nothing said" stop only when the analyser really runs.
  function watch(s) {
    let lv = null;
    if (note) { lv = document.createElement("span"); lv.className = "lv"; lv.innerHTML = "<i></i>"; note.appendChild(lv); lv = lv.firstChild; }
    if (!ac) return;
    try {
      const an = ac.createAnalyser(); an.fftSize = 1024;
      ac.createMediaStreamSource(s).connect(an);
      const buf = new Uint8Array(an.fftSize);
      let floor = 0, n = 0, quietSince = 0;
      meter = setInterval(() => {
        if (!ac || ac.state !== "running") return;  // suspended: you stop it, or the 30 s do
        an.getByteTimeDomainData(buf);
        let sum = 0; for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
        const rms = Math.sqrt(sum / buf.length), t = Date.now() - startedAt;
        if (lv) lv.style.width = Math.min(100, rms * 900).toFixed(0) + "%";
        if (t < 400) { floor = (floor * n + rms) / ++n; return; }
        const loud = rms > Math.min(Math.max(0.025, floor * 3), 0.06);  // capped: talking from the first moment still counts
        if (loud) { heard = true; quietSince = 0; return; }
        if (!quietSince) quietSince = Date.now();
        if (heard && Date.now() - quietSince > 1600) stop();       // you finished
        else if (!heard && t > 8000) { dropped = true; stop(); }  // nothing said at all
      }, 100);
    } catch (e) {}  // no analyser: you stop it, or the 30 s do
  }
  async function why(e) {  // a refused mic: blocked for this site, or only the prompt dismissed
    const n = e && e.name;
    if (n === "NotFoundError" || n === "OverconstrainedError") return SAY.nomic;
    if (n !== "NotAllowedError" && n !== "SecurityError") return SAY.fail;
    try {
      const st = await navigator.permissions.query({ name: "microphone" });
      if (st && st.state === "prompt") return SAY.again;
    } catch (x) {}
    return SAY.denied;
  }
  async function startServer() {
    const my = ++gen;
    set("starting"); H().hearStart && H().hearStart();
    // the analyser's AudioContext is made inside the tap: iPhone only lets it run when started by one
    const AC = window.AudioContext || window.webkitAudioContext;
    try { ac = AC ? new AC() : null; if (ac && ac.state === "suspended") ac.resume().catch(() => {}); } catch (e) { ac = null; }
    session("play-and-record");
    let s;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      if (my !== gen) return;
      release();
      return fail(await why(e));
    }
    if (my !== gen || state !== "starting") { s.getTracks().forEach(t => t.stop()); return; }  // cancelled while asking
    stream = s;
    s.getTracks().forEach(t => t.addEventListener("ended", () => { if (my === gen && state === "rec") stop(); }));  // mic taken away
    const type = pick();
    let r;
    try { r = new MediaRecorder(s, type ? { mimeType: type, audioBitsPerSecond: 32000 } : undefined); }
    catch (e) { try { r = new MediaRecorder(s); } catch (e2) { release(); return fail(SAY.fail); } }
    rec = r; chunks = []; heard = false; dropped = false;
    r.ondataavailable = e => { if (my === gen && e.data && e.data.size) chunks.push(e.data); };
    r.onstop = () => { if (my === gen) done(r, type); };
    r.onerror = () => { if (my !== gen) return; r.onstop = null; dropped = true; release(); rec = null; fail(SAY.fail); };
    startedAt = Date.now();
    try { r.start(); } catch (e) { rec = null; release(); return fail(SAY.fail); }  // one blob at the end (Safari's mp4 pieces don't join well)
    set("rec");
    tell(SAY.tap, "rec");
    watch(s);
    stopAt = setTimeout(stop, MAX_MS);
  }
  function stop() {
    clearInterval(meter); clearTimeout(stopAt); meter = 0;
    if (rec && rec.state !== "inactive") { try { rec.stop(); } catch (e) { drop(rec); rec = null; release(); fail(SAY.fail); } }
  }
  function cancel() {
    gen++; dropped = true; sendAfter = false;
    drop(rec); rec = null; chunks = [];
    release(); set("idle"); H().hearEnd && H().hearEnd();
  }
  async function done(r, type) {
    const len = Date.now() - startedAt;
    release();
    const blob = new Blob(chunks, { type: r.mimeType || type || "audio/mp4" });
    chunks = []; rec = null;
    if (dropped || len < 500 || blob.size < 1000) return fail(SAY.none);
    if (blob.size > MAX_BYTES) return fail(SAY.long);
    const my = gen;
    set("wait"); tell(SAY.wait); H().hearWait && H().hearWait();
    let res = null, j = null;
    try {
      const c = new AbortController(), t = setTimeout(() => c.abort(), 45000);
      res = await fetch("/api/listen?lang=" + (fi ? "fi" : "en"), { method: "POST", body: blob, signal: c.signal,
        headers: { "Content-Type": (blob.type || "audio/mp4").split(";")[0] } });
      clearTimeout(t);
      j = res.status === 200 ? await res.json() : null;
    } catch (e) { return my === gen && fail(SAY.fail); }
    if (my !== gen) return;
    if (res.status === 204) {  // the server has no key after all: the browser's recognition, or no mic
      mode = SR ? "browser" : null;
      if (!mode) btn.hidden = true;
      return fail(SAY.fail);
    }
    if (j && landed(j.text)) { set("idle"); return; }
    fail(res.status === 422 || res.status === 200 ? SAY.none : res.status === 413 ? SAY.long : res.status === 429 ? SAY.much : SAY.fail);
  }

  // ---- the browser way (no key on the server): its own speech recognition ----
  let sr = null, srText = "", srQuiet = false;
  function startBrowser() {
    try {
      sr = new SR();
    } catch (e) { return fail(SAY.fail); }
    sr.lang = fi ? "fi-FI" : "en-GB"; sr.interimResults = true; sr.continuous = false; sr.maxAlternatives = 1;
    srText = ""; srQuiet = false; let err = "";
    const had = q.value.trim();
    sr.onresult = e => {
      srText = Array.from(e.results).map(x => x[0].transcript).join(" ").trim();
      q.value = had ? had + " " + srText : srText;  // you see it being written
    };
    sr.onerror = e => { err = e.error; };
    sr.onend = () => {
      sr = null; clearTimeout(stopAt);
      if (srQuiet) { sendAfter = false; q.value = had; set("idle"); H().hearEnd && H().hearEnd(); return; }  // put away mid-way: nothing to say
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
    sendAfter = false;
    if (sendBtn) sendBtn.classList.remove("ready");
    (mode === "server" ? startServer : startBrowser)();
  });
  // the page goes to the background (a call, the lock screen) while recording: drop it rather than send half
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && (state === "rec" || state === "starting")) {
      if (mode === "browser") { srQuiet = true; if (sr) sr.abort(); } else cancel();
      tell("");
    }
  });
})();
