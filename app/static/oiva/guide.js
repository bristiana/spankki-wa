/* Oiva as an on-screen guide: the squirrel hops to each key element in turn and says one line about it.

   Steps are data: a JSON list [{target: "element-id", say: "...", mood: "talk"}] (e.g. from a
   <script type="application/json">), or, when the list is empty, every [data-guide] element in the root, in the
   order of its value: <div data-guide="1" data-say="Your balance" data-mood="talk">.

     const g = OivaGuide.create(phoneEl, {autoplay: false, dwell: 2600, onEnd() {}});
     g.start(steps)          walk through the steps; tap anywhere or "Next" to advance, "Skip guide" to hide
     g.point(id, say, mood)  move next to one element and say something (a promise, resolved when it is there)
     g.hide()

   Oiva never covers what it points at: it stands below the target, or above it when there is no room below.
   It runs there (left or right, its run cycles), glances at the target (one of the 16 look directions),
   then plays the step's mood. With prefers-reduced-motion it does not walk: it just appears next to each target.
   Moods are the rows of /static/oiva/oiva.webp (oiva.css): idle talk happy think worried wave busy. */
(function () {
  const MOODS = ["idle", "talk", "happy", "think", "worried", "wave", "busy", "walk-r", "walk-l", "look"];
  const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = ms => new Promise(r => setTimeout(r, ms));

  function fromAttributes(root) {
    return [...root.querySelectorAll("[data-guide]")]
      .sort((a, b) => (+a.dataset.guide || 0) - (+b.dataset.guide || 0))
      .map(el => ({target: el, say: el.dataset.say || "", mood: el.dataset.mood || "talk"}));
  }

  function create(root, opts = {}) {
    const o = {autoplay: false, dwell: 2600, onEnd: () => {}, ...opts};
    const el = document.createElement("div");
    el.className = "guide";
    el.hidden = true;
    el.innerHTML = '<div class="g-bubble" role="status" aria-live="polite"><p class="g-say"></p>' +
      '<div class="g-btns"><button type="button" class="g-skip">Skip guide</button>' +
      '<button type="button" class="g-next">Next</button></div></div>' +
      '<span class="g-hop"><span class="oiva idle" role="img" aria-label="Oiva the squirrel"></span></span>';
    root.appendChild(el);
    const sprite = el.querySelector(".oiva"), say = el.querySelector(".g-say"), btns = el.querySelector(".g-btns");
    const nextBtn = el.querySelector(".g-next");
    let steps = [], i = -1, active = false, timer = 0, moveId = 0, current = null, lastMood = "idle";

    function mood(m) {
      sprite.classList.remove(...MOODS);
      sprite.style.removeProperty("--row");
      sprite.style.removeProperty("--col");
      sprite.classList.add(MOODS.includes(m) ? m : "idle");
    }

    // Turn the head towards the target: 16 directions, 22.5 deg apart, clockwise from straight up.
    function lookAt(target) {
      const s = sprite.getBoundingClientRect(), t = target.getBoundingClientRect();
      const dx = t.left + t.width / 2 - (s.left + s.width / 2), dy = t.top + t.height / 2 - (s.top + s.height / 2);
      const n = Math.round(((Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360) / 22.5) % 16;
      mood("look");
      sprite.style.setProperty("--row", 9 + Math.floor(n / 8));
      sprite.style.setProperty("--col", n % 8);
    }

    function position() {
      const m = /translate\((-?[\d.]+)px/.exec(el.style.transform);
      return m ? +m[1] : 0;
    }

    function resolve(t) {
      return typeof t === "string" ? document.getElementById(t) || root.querySelector(t) : t;
    }

    // Where Oiva stands: below the target, right-aligned, or above it. Never on top of it.
    function spot(target) {
      const R = root.getBoundingClientRect(), t = target.getBoundingClientRect();
      const w = el.offsetWidth, h = el.offsetHeight, pad = 8, gap = 8;
      const x = Math.max(pad, Math.min(t.right - R.left - w, R.width - w - pad));
      const below = t.bottom - R.top + gap, above = t.top - R.top - h - gap;
      if (below + h <= R.height - pad) return {x, y: below};
      if (above >= pad) return {x, y: above};
      // Taller than the room around it: beside it, on whichever side is wider.
      const left = t.left - R.left - w - gap, right = t.right - R.left + gap;
      const y = Math.max(pad, Math.min(t.top - R.top, R.height - h - pad));
      return right + w <= R.width - pad ? {x: right, y} : {x: Math.max(pad, left), y};
    }

    function bringIntoView(target) {
      const box = target.closest(".screen");
      if (!box) return;
      const b = box.getBoundingClientRect(), t = target.getBoundingClientRect(), room = el.offsetHeight + 24;
      if (t.top < b.top + 8 || t.bottom + room > b.bottom) {
        box.scrollTop += t.top - b.top - Math.max(8, (b.height - t.height - room) / 2);
      }
    }

    async function point(target, text, m = "talk", {buttons = false} = {}) {
      const t = resolve(target);
      if (!t) return;
      const id = ++moveId;
      current = t;
      el.hidden = false;
      say.textContent = text;
      btns.hidden = !buttons;
      // A target that is still animating in (a sheet scaling up) is measured once it has its final size.
      const settling = t.getAnimations ? t.getAnimations().filter(a => a.playState === "running") : [];
      if (settling.length) await Promise.race([Promise.all(settling.map(a => a.finished.catch(() => {}))), wait(800)]);
      if (id !== moveId) return;
      bringIntoView(t);
      const {x, y} = spot(t);
      const moving = !still() && el.dataset.placed && el.style.transform !== `translate(${x}px, ${y}px)`;
      el.classList.toggle("moving", !!moving);
      el.classList.toggle("instant", !el.dataset.placed || still());
      if (moving) mood(x < position() - 2 ? "walk-l" : x > position() + 2 ? "walk-r" : "busy");
      el.style.transform = `translate(${x}px, ${y}px)`;
      el.dataset.placed = "1";
      root.querySelectorAll(".g-target").forEach(n => n.classList.remove("g-target"));
      t.classList.add("g-target");
      if (moving) await wait(720);
      if (id !== moveId) return;
      el.classList.remove("moving");
      if (m) lastMood = m;
      if (!still()) {
        lookAt(t);
        await wait(moving ? 420 : 260);
        if (id !== moveId) return;
      }
      mood(lastMood);
    }

    async function show() {
      clearTimeout(timer);
      const s = steps[i];
      const last = i === steps.length - 1;
      nextBtn.textContent = last ? "Got it" : "Next";
      await point(s.target, s.say, s.mood, {buttons: !o.autoplay});
      if (o.autoplay && active) timer = setTimeout(next, o.dwell);
    }

    function next() {
      if (!active) return;
      if (i >= steps.length - 1) {
        active = false;
        clearTimeout(timer);
        btns.hidden = true;
        mood("idle");
        o.onEnd();
        return;
      }
      i += 1;
      show();
    }

    function start(list) {
      steps = (list && list.length ? list : fromAttributes(root)).filter(s => resolve(s.target));
      i = -1;
      active = steps.length > 0;
      if (active) next();
    }

    function hide() {
      active = false;
      clearTimeout(timer);
      moveId++;
      el.hidden = true;
      delete el.dataset.placed;  // back on screen, Oiva appears at the next target instead of walking across
      root.querySelectorAll(".g-target").forEach(n => n.classList.remove("g-target"));
    }

    // Tap anywhere (but on a real control) to advance; "Skip guide" hides Oiva for this screen.
    el.querySelector(".g-skip").addEventListener("click", ev => { ev.stopPropagation(); hide(); o.onSkip && o.onSkip(); });
    root.addEventListener("click", ev => {
      if (!active) return;
      const onGuide = ev.target.closest(".guide");
      if (!onGuide && ev.target.closest("button, a, input, select, label, summary")) return;
      next();
    });
    addEventListener("resize", () => { if (!el.hidden && current) point(current, say.textContent, null, {buttons: !btns.hidden}); });
    return {start, next, point, hide, mood, get active() { return active; }, get el() { return el; }};
  }

  window.OivaGuide = {create};
})();
