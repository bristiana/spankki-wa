// The chat's Helper mode (docs/plans/squirrel-helper.md): a big Oiva (oiva.js from the mascot set) that takes the
// pose the server picked for each reply (`mood`, app/mood.py), says the reply's first line in a speech bubble and,
// with the sound on, reads the reply aloud (speechSynthesis, fi-FI or en-GB). Chat mode is the chat as it was.
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
  let oiva = null, mood = "idle", idleTimer = 0, last = "", lastMood = "success";
  const on = () => phone.classList.contains("helper");

  function plain(s) { return String(s || "").replace(/[*_]/g, "").replace(/\s+/g, " ").trim(); }
  // amounts never break across lines in the bubble: "3 345.06 €" keeps its spaces non-breaking
  function nb(s) { return s.replace(/(\d) (?=\d{3}(?!\d))/g, "$1\u00a0").replace(/(\d) €/g, "$1\u00a0€"); }
  function first(parts) {
    const s = plain(parts && parts[0]);
    return s.length > 170 ? s.slice(0, 166).replace(/\s+\S*$/, "") + " …" : s;
  }
  function pose(name) {
    mood = name;
    if (oiva) oiva.setState(name);
  }
  function show(name, text, thinking) {
    if (!say) return;
    say.dataset.mood = name;
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
    }, ms);
  }

  // ---- voice ----
  const canSpeak = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  let voiceOn = store.get("oiva-voice") === "on";
  function looksFinnish(s) { return /[äöå]/i.test(s) || /\b(ja|on|olet|sinun|että|kuin|kuussa|euroa)\b/i.test(s); }
  function speak(parts) {
    if (!canSpeak || !voiceOn || !on()) return;
    const text = plain((parts || []).join(". ")).replace(/€/g, fi ? " euroa" : " euros");
    if (!text) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const lang = looksFinnish(text) ? "fi-FI" : "en-GB";
    u.lang = lang;
    const v = speechSynthesis.getVoices().find(x => x.lang && x.lang.replace("_", "-").toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()));
    if (v) u.voice = v;
    u.rate = 1.02;
    speechSynthesis.speak(u);
  }
  function setVoice(v, sayNow) {
    voiceOn = v; store.set("oiva-voice", v ? "on" : "off");
    if (voiceBtn) { voiceBtn.setAttribute("aria-pressed", v); voiceBtn.querySelector("span").textContent = v ? T("Ääni", "Sound") : T("Mykistetty", "Muted"); }
    if (!v && canSpeak) speechSynthesis.cancel();
    if (v && sayNow && last) speak([last]);
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
      oiva = window.Oiva.create(pal, { palette: "red", state: mood, hoverReact: true });
      pal.onclick = () => oiva && oiva.poke();
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
    listening() {
      if (mood === "thinking") return;
      clearTimeout(idleTimer); pose("listening");
      if (on()) show("listening", T("Kerro vain, kuuntelen.", "Go ahead, I'm listening."), true);
      restLater(8000);
    },
    thinking() {
      clearTimeout(idleTimer); pose("thinking");
      if (canSpeak) speechSynthesis.cancel();
      show("thinking", T("Hetkinen, katson lukujasi…", "One moment, looking at your numbers…"), true);
    },
    reply(name, parts) {
      const m = (window.Oiva && window.Oiva.STATES.includes(name)) ? name : "success";
      lastMood = m; last = first(parts);
      pose(m);
      show(m, last || T("Tässä.", "Here you go."));
      speak(parts);
      restLater(12000);
    },
    mood: () => mood,
  };
  const hello = document.getElementById("hello");
  if (hello) { last = first([hello.textContent]); show("", last); }
  setMode(startMode, false);
  pose("idle");
  if (startMode === "helper" && oiva) oiva.setState("idle");
  const q = document.getElementById("q");
  if (q) q.addEventListener("input", () => { if (q.value.trim()) window.oivaHelper.listening(); });
})();
