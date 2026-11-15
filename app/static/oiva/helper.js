// The chat's Helper mode (docs/plans/squirrel-helper.md): a big Oiva (oiva.js from the mascot set) that takes the
// pose the server picked for each reply (`mood`, app/mood.py), says one short sentence of the reply in a speech
// bubble (the full reply is in the log) and, with the sound on, reads the whole reply aloud (speechSynthesis, fi-FI
// or en-GB by the page language). Chat mode is the chat as it was.
(function () {
  const fi = (document.documentElement.lang || "fi").startsWith("fi");
  const T = (a, b) => (fi ? a : b);
  const TAGS = {
    compare: T("Verrattuna kaltaisiisi", "Compared with people like you"),
    needs_approval: T("Tarvitaan hyväksyntäsi", "Your OK is needed"),
    caution: T("Varovasti", "Careful"),
    safe_to_spend: T("Turvallisesti käytettävissä", "Safe to spend"),
    what_if: T("Entä jos…", "What if…"),
    saving: T("Säästöön", "Saving"),
    teaching: T("Katso", "Let me show you"),
    learned: T("Hienoa!", "Nice!"),
    spent: T("Kulutuksesi", "Your spending"),
    working: T("Lukusi", "Your numbers"),
    error: T("Hups", "Oops"),
    checking: T("Tarkistin", "Checked for you"),
    listening: T("Kuuntelen", "Listening"),
    thinking: T("Mietin", "Thinking"),
  };
  const phone = document.getElementById("phone");
  const stage = document.getElementById("stage"), pal = document.getElementById("pal");
  const say = stage && stage.querySelector(".say"), tag = document.getElementById("say-tag"), line = document.getElementById("say-text");
  const voiceBtn = document.getElementById("voice");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  let oiva = null, mood = "idle", idleTimer = 0, last = "", lastParts = [];
  const on = () => phone.classList.contains("helper");

  function plain(s) { return String(s || "").replace(/[*_]/g, "").replace(/\s+/g, " ").trim(); }
  // amounts never break across lines in the bubble: "3 345.06 €" keeps its spaces non-breaking
  function nb(s) { return s.replace(/(\d) (?=\d{3}(?!\d))/g, "$1\u00a0").replace(/(\d) €/g, "$1\u00a0€"); }
  // The bubble: one sentence, not a copy of the log. For a comparison, the sentence that holds it (a spender
  // profile opens with the type and compares in its second part).
  const PEER = /\b(people|customers|typical|average|most|others|their|year[- ]olds|asiakk\w*|tyypilli\w*|keskim\w*|muut\w*|muill\w*|kaltais\w*|ikäis\w*|\w*vuotiai\w*)\b|\d+\s*[–-]\s*\d+[- ]?(year|vuot)/i;
  function sentences(s) { return plain(s).split(/(?<=[.!?…])\s+(?=["'(A-ZÄÖÅ0-9])/); }
  function first(parts, m) {
    const all = (parts || []).flatMap(sentences).filter(Boolean);
    // a comparison: the sentence with both a peer word and an amount, else any peer sentence
    const peer = m === "compare" && (all.find(x => PEER.test(x) && /\d[\d\s.,]*\s?€/.test(x)) || all.find(x => PEER.test(x)));
    let s = peer || all[0] || "";
    return s.length > 150 ? s.slice(0, 146).replace(/\s+\S*$/, "") + " …" : s;
  }
  function pose(name) {
    mood = name;
    if (oiva) oiva.setState(name);
  }
  function show(name, text, thinking) {
    if (!say) return;
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
      if (window.speechSynthesis && speechSynthesis.speaking) { idleTimer = setTimeout(rest, 2000); return; }
      pose("idle");
      if (say) say.classList.add("rest");  // the tag fades with the pose; the sentence stays readable
    }, ms);
  }

  // ---- voice ----
  const canSpeak = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  let voiceOn = store.get("oiva-voice") === "on";
  const LANG = fi ? "fi-FI" : "en-GB";
  function voiceFor() {
    const vs = speechSynthesis.getVoices(), norm = x => (x.lang || "").replace("_", "-").toLowerCase();
    return vs.find(x => norm(x) === LANG.toLowerCase()) || vs.find(x => norm(x).startsWith(LANG.slice(0, 2).toLowerCase()));
  }
  function speak(parts) {
    if (!canSpeak || !voiceOn || !on()) return;
    const text = plain((parts || []).join(". ")).replace(/€/g, fi ? " euroa" : " euros");
    if (!text) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = LANG;
    const v = voiceFor();
    if (v) u.voice = v;
    u.rate = 1.02;
    speechSynthesis.speak(u);
  }
  function setVoice(v, sayNow) {
    voiceOn = v; store.set("oiva-voice", v ? "on" : "off");
    if (voiceBtn) { voiceBtn.setAttribute("aria-pressed", v); voiceBtn.querySelector("span").textContent = v ? T("Ääni", "Sound") : T("Mykistetty", "Muted"); }
    if (!v && canSpeak) speechSynthesis.cancel();
    if (v && sayNow && lastParts.length) speak(lastParts);
  }
  if (voiceBtn && canSpeak) {
    voiceBtn.classList.add("can");
    voiceBtn.onclick = () => setVoice(!voiceOn, true);
    setVoice(voiceOn, false);  // remembered per browser; off until turned on
  }

  // ---- modes ----
  function setMode(m, remember) {
    const helper = m === "helper";
    phone.classList.toggle("helper", helper);
    document.querySelectorAll(".modes [data-mode]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === m)));
    if (remember) store.set("oiva-mode", m);
    if (stage) stage.hidden = !helper;
    if (helper && !oiva && window.Oiva && pal) {
      oiva = window.Oiva.create(pal, { palette: "red", state: mood, hoverReact: true });  // a tap pokes him (oiva.js)
    }
    if (!helper && oiva) { oiva.destroy(); oiva = null; }
    if (!helper && canSpeak) speechSynthesis.cancel();
    const log = document.getElementById("log");
    if (log) log.scrollTop = log.scrollHeight;
  }
  document.querySelectorAll(".modes [data-mode]").forEach(b => b.onclick = () => {
    setMode(b.dataset.mode, true);
    const u = new URL(location.href);
    if (b.dataset.mode === "helper") u.searchParams.set("mode", "helper"); else u.searchParams.delete("mode");
    history.replaceState(null, "", u.pathname + u.search);
  });
  const asked = new URLSearchParams(location.search).get("mode");
  const startMode = asked === "helper" || asked === "chat" ? asked : (store.get("oiva-mode") === "helper" ? "helper" : "chat");

  // ---- keyboard: a smaller stage when the visual viewport is short ----
  if (window.visualViewport) {
    const fit = () => phone.classList.toggle("kb", visualViewport.height < 560);
    visualViewport.addEventListener("resize", fit); fit();
  }

  // ---- what the chat tells us ----
  window.oivaHelper = {
    listening() {  // only the pose: the last reply's sentence stays in the bubble while you type
      if (mood === "thinking") return;
      clearTimeout(idleTimer);
      if (mood !== "listening") pose("listening");
      restLater(8000);
    },
    thinking() {
      clearTimeout(idleTimer); pose("thinking");
      if (canSpeak) speechSynthesis.cancel();
      show("thinking", T("Hetkinen, katson lukujasi…", "One moment, looking at your numbers…"), true);
    },
    reply(name, parts) {
      const m = (window.Oiva && window.Oiva.STATES.includes(name)) ? name : "success";
      last = first(parts, m); lastParts = parts || [];
      pose(m);
      show(m, last || T("Tässä.", "Here you go."));
      speak(parts);
      restLater(12000);
    },
    mood: () => mood,
  };
  const hello = document.getElementById("hello");
  if (hello) {  // the greeting: its first two sentences ("Hi Antti! I'm Oiva, your S-Pankki money coach.")
    last = sentences(hello.textContent).slice(0, 2).join(" "); lastParts = [hello.textContent]; show("", last);
  }
  setMode(startMode, false);
  pose("idle");
  if (startMode === "helper" && oiva) oiva.setState("idle");
  const q = document.getElementById("q");
  if (q) q.addEventListener("input", () => { if (q.value.trim()) window.oivaHelper.listening(); });
})();
