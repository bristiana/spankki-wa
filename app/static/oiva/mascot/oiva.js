/* Oiva Orava — vault-keeper squirrel mascot (S-Pankki concept). No dependencies. See README.md */
/* ==================================================================
   Butler v5 — playful soft glossy owl-butler
   ================================================================== */
(function (global) {
  'use strict';

  const STATES = ['idle', 'listening', 'thinking', 'working', 'teaching', 'checking', 'needs_approval', 'success', 'error',
                  'learned', 'saving', 'spent', 'safe_to_spend', 'caution', 'what_if', 'compare'];

  /* ---------- Palettes ----------
     b* body, c* crest, iri* iridescent rim, accent2 = hat feather / confetti. */
  /* Fur colours; the vault-keeper uniform stays the same in every variant. */
  const PALETTES = {
    red:    { label: 'Red squirrel', b0: '#f4ad6a', b1: '#e5853f', b2: '#c9622b', cream: '#fbead3', inner: '#f3bfa2', tuft: '#b8622f',
              shadow: '#b9a58f', blush: '#ff8f8f', brow: '#7d4224', accent: '#b3323f', accentDk: '#7d1f2c', accent2: '#d6ae58',
              iri0: '#ffe2b8', iri1: '#9fc7d3', iri2: '#ffd27a' },
    winter: { label: 'Winter grey', b0: '#dcd6d1', b1: '#b9b1aa', b2: '#958c85', cream: '#f7f3ef', inner: '#efd0ca', tuft: '#7d746e',
              shadow: '#b3aca5', blush: '#ff9a9a', brow: '#554d48', accent: '#b3323f', accentDk: '#7d1f2c', accent2: '#d6ae58',
              iri0: '#eef3f6', iri1: '#9fc7d3', iri2: '#ffd27a' },
    golden: { label: 'Golden', b0: '#f8d08a', b1: '#ebaf5a', b2: '#cf8c39', cream: '#fff3de', inner: '#f6cdaa', tuft: '#b87b32',
              shadow: '#c2ab8a', blush: '#ff9488', brow: '#7d5225', accent: '#b3323f', accentDk: '#7d1f2c', accent2: '#d6ae58',
              iri0: '#fff0c8', iri1: '#9fc7d3', iri2: '#ffd27a' }
  };
  // vault-keeper uniform (from the campaign look): blue coat, red piping, brass/gold
  const NEON = '#c6f03c';  // highlighter only (approximate)
  const INK = '#1f2035', COAT0 = '#3f74b9', COAT1 = '#244a82', PIPING = '#c8323c', GOLD = '#d6ae58', GOLD_DK = '#a8822f';

  /* Tail spine (three bezier segments) sampled into overlapping fur puffs */
  const TAIL_SEGS = [[[128, 150], [152, 178], [200, 166], [204, 124]], [[204, 124], [208, 90], [196, 58], [176, 54]], [[176, 54], [161, 51], [158, 70], [170, 77]]];
  const TAIL = [], bez = (p, t) => { const u = 1 - t; return [0, 1].map((k) => u * u * u * p[0][k] + 3 * u * u * t * p[1][k] + 3 * u * t * t * p[2][k] + t * t * t * p[3][k]); };
  TAIL_SEGS.forEach((seg, si) => { for (let i = 0; i < 7; i++) {
    const sGlobal = (si * 7 + i) / 20, [x, y] = bez(seg, i / 7);
    const r = sGlobal < .15 ? 11 + 50 * sGlobal : sGlobal < .68 ? 18.5 + 2 * Math.sin(sGlobal * 40) : 18.5 - 22 * (sGlobal - .68);
    TAIL.push([x, y, r]); } });
  const TAIL_SPINE = TAIL_SEGS.map((p, i) => (i ? '' : `M${p[0][0]} ${p[0][1]} `) + `C${p[1].join(' ')} ${p[2].join(' ')} ${p[3].join(' ')}`).join(' ');

  const ACORN = `<path d="M-5.4 -1 Q-5.4 8 0 10.5 Q5.4 8 5.4 -1 Z" fill="#f2c14e" stroke="#c98a22" stroke-width=".9"/>
    <path d="M-3.6 1 Q-3.4 6 -1 8" fill="none" stroke="#fff" stroke-width="1.2" stroke-linecap="round" opacity=".7"/>
    <path d="M-7 -1 Q-6.6 -6.6 0 -6.8 Q6.6 -6.6 7 -1 Q0 1 -7 -1 Z" fill="#8a5a33"/>
    <path d="M-5 -3 h10 M-4 -5 h8" stroke="#6e4426" stroke-width=".8" opacity=".7"/>
    <path d="M0 -6.8 q.6 -3 2.6 -3.8" fill="none" stroke="#6e4426" stroke-width="1.4" stroke-linecap="round"/>`;

  /* ---------- SVG template ---------- */
  function template(u, C) {
    const id = (n) => `${n}-${u}`, url = (n) => `url(#${n}-${u})`;
    const star = 'M0 -6 Q1 -1 6 0 Q1 1 0 6 Q-1 1 -6 0 Q-1 -1 0 -6 Z';
    const heart = 'M0 4 C-7 -1 -5 -8 0 -4.5 C5 -8 7 -1 0 4 Z';
    // a squirrel paw: round mitt + thumb + little toe lines
    const glove = (tx) => `<circle cx="${tx}" cy="-4" r="3.6" fill="${url('paw')}" stroke="${C.b2}" stroke-width="1"/>
      <circle r="8.2" fill="${url('paw')}" stroke="${C.b2}" stroke-width="1"/>
      <path d="M-3.4 4.6 L-3 7.4 M0 5.2 L0 8.2 M3.4 4.6 L3 7.4" stroke="${C.b2}" stroke-width="1.1" stroke-linecap="round" opacity=".7"/>
      <ellipse cx="-2.6" cy="-3.4" rx="2.8" ry="1.8" fill="#fff" opacity=".55"/>`;
    const confColors = [C.accent, GOLD, COAT0, C.iri1, C.blush, C.b1];
    const confetti = confColors.map((c, i) =>
      `<rect data-p="conf${i}" x="-2.5" y="-4" width="5" height="8" rx="1.5" fill="${c}" opacity="0"/>`).join('');
    const ear = (side) => {
      const m = (x) => 100 + side * (x - 100);
      return `<path d="M${m(63)} 80 Q${m(58)} 52 ${m(70)} 38 Q${m(82)} 52 ${m(86)} 74 Z" fill="${url('fur')}"/>
        <path d="M${m(67)} 74 Q${m(64)} 56 ${m(71)} 46 Q${m(78)} 58 ${m(80)} 72 Z" fill="${C.inner}"/>
        <path d="M${m(66)} 44 C${m(64)} 36 ${m(67)} 30 ${m(71)} 26 C${m(71)} 31 ${m(73.5)} 32 ${m(75)} 29 C${m(78)} 34 ${m(77.5)} 41 ${m(75)} 46 Z" fill="${C.tuft}"/>
        <path d="M${m(68)} 40 C${m(67)} 34 ${m(68.5)} 30 ${m(70.5)} 27" fill="none" stroke="${C.b0}" stroke-width="1.3" stroke-linecap="round" opacity=".7"/>`;
    };
    return `
<svg viewBox="-10 -20 220 220" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Oiva Orava, the vault-keeper squirrel">
  <defs>
    <linearGradient id="${id('fur')}" x1="0" y1="0" x2=".3" y2="1">
      <stop offset="0" stop-color="${C.b0}"/><stop offset=".55" stop-color="${C.b1}"/><stop offset="1" stop-color="${C.b2}"/>
    </linearGradient>
    <radialGradient id="${id('paw')}" cx=".35" cy=".3" r=".8">
      <stop offset="0" stop-color="${C.b0}"/><stop offset="1" stop-color="${C.b1}"/>
    </radialGradient>
    <linearGradient id="${id('tail')}" gradientUnits="userSpaceOnUse" x1="140" y1="170" x2="195" y2="40">
      <stop offset="0" stop-color="${C.b2}"/><stop offset=".45" stop-color="${C.b1}"/><stop offset="1" stop-color="${C.b0}"/>
    </linearGradient>
    <linearGradient id="${id('coat')}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${COAT0}"/><stop offset="1" stop-color="${COAT1}"/>
    </linearGradient>
    <linearGradient id="${id('tie')}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${C.accent}"/><stop offset="1" stop-color="${C.accentDk}"/>
    </linearGradient>
    <linearGradient id="${id('gold')}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#f6e2a6"/><stop offset=".5" stop-color="${GOLD}"/><stop offset="1" stop-color="#f0d68e"/>
    </linearGradient>
    <linearGradient id="${id('silver')}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f6f7fc"/><stop offset="1" stop-color="#c5cbe0"/>
    </linearGradient>
    <filter id="${id('blur6')}" x="-50%" y="-200%" width="200%" height="500%"><feGaussianBlur stdDeviation="5"/></filter>
    <filter id="${id('blur2')}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2"/></filter>
    <clipPath id="${id('tailClip')}"><path d="M128 160 C150 174 182 163 187 132 C191 106 181 92 180 74 C179 56 187 40 200 40 C211 41 211 59 200 61 C191 63 185 58 176 61 C161 65 148 72 142 86 C135 102 131 126 128 160 Z"/></clipPath>
    <clipPath id="${id('bodyClip')}"><circle cx="100" cy="112" r="50"/></clipPath>
    <clipPath id="${id('dome')}"><path d="M-18 -1 A18 17 0 0 1 18 -1 Z"/></clipPath>
    <clipPath id="${id('lapClip')}"><rect x="-20" y="-28" width="40" height="19"/></clipPath>
  </defs>

  <ellipse data-p="shadow" cx="100" cy="196" rx="40" ry="6" fill="${C.shadow}" filter="${url('blur6')}"/>

  <g data-p="float"><g data-p="lean"><g data-p="body">
    <!-- big fluffy tail (behind) -->
    <!-- bushy tail: rises behind his back in an S, tip curls outward above the ear -->
    <g data-p="tail">
      <path d="M128 160 C150 174 182 163 187 132 C191 106 181 92 180 74 C179 56 187 40 200 40 C211 41 211 59 200 61 C191 63 185 58 176 61 C161 65 148 72 142 86 C135 102 131 126 128 160 Z" fill="${C.b2}" transform="translate(2.2 2.4)"/>
      <path d="M128 160 C150 174 182 163 187 132 C191 106 181 92 180 74 C179 56 187 40 200 40 C211 41 211 59 200 61 C191 63 185 58 176 61 C161 65 148 72 142 86 C135 102 131 126 128 160 Z" fill="${url('tail')}"/>
      <g clip-path="${url('tailClip')}">
        <path d="M146 168 C170 160 182 136 180 112 C178 94 168 82 170 66 C171 55 178 48 190 46" fill="none" stroke="${C.cream}" stroke-width="8" stroke-linecap="round" opacity=".3"/>
        <path d="M160 150 q8 -6 12 -16 M166 118 q4 -8 3 -18 M174 82 q0 -8 4 -14" fill="none" stroke="${C.b2}" stroke-width="1.6" stroke-linecap="round" opacity=".35"/>
        <path d="M186 132 C190 112 186 98 180 86" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".28"/>
      </g>
      <path d="M181 47 C186 42 195 41 201 45" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".7"/>
    </g>
    <!-- hind legs + feet (behind the coat) -->
    ${[-1, 1].map((sd) => { const m = (x) => 100 + sd * (x - 100);
      return `<path d="M${m(84)} 148 C${m(82.5)} 165 ${m(82)} 176 ${m(84)} 186 L${m(96)} 186 C${m(97)} 175 ${m(96.5)} 163 ${m(95)} 148 Z" fill="${url('fur')}"/>
        <path d="M${m(86)} 160 C${m(85.5)} 170 ${m(85.5)} 177 ${m(86.5)} 183" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" opacity=".3"/>
        <g transform="translate(${m(88)} 188) rotate(${sd * -9})">
          <ellipse rx="11" ry="5.4" fill="${url('paw')}" stroke="${C.b2}" stroke-width="1.1"/>
          <path d="M-2.5 -1 v3 M2.5 -1 v3" stroke="${C.b2}" stroke-width=".9" stroke-linecap="round" opacity=".45"/>
          <ellipse cx="-3.6" cy="-2.4" rx="3.6" ry="1.3" fill="#fff" opacity=".5"/>
        </g>`; }).join('')}
    <!-- coat tails -->
    <path d="M70 151 Q65 162 65 171 Q74 170 82 160 Z" fill="${url('coat')}"/>
    <path d="M130 151 Q135 162 135 171 Q126 170 118 160 Z" fill="${url('coat')}"/>
    <path d="M70 151 Q65 162 65 171" fill="none" stroke="${PIPING}" stroke-width="1.6" stroke-linecap="round"/>
    <path d="M130 151 Q135 162 135 171" fill="none" stroke="${PIPING}" stroke-width="1.6" stroke-linecap="round"/>

    <!-- round fur body -->
    <circle cx="100" cy="112" r="50" fill="${url('fur')}"/>
    <g clip-path="${url('bodyClip')}">
      <!-- double-breasted vault-keeper coat -->
      <path d="M40 133 Q66 124 88 128 L100 153 L112 128 Q134 124 160 133 L160 175 L40 175 Z" fill="${url('coat')}"/>
      <path d="M88 128 Q100 125 112 128 L100 153 Z" fill="#f4f6fb"/>
      <path d="M88 128 L100 153 L112 128" fill="none" stroke="${PIPING}" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M100 153 L100 175" stroke="${PIPING}" stroke-width="1.8"/>
      <ellipse cx="72" cy="146" rx="14" ry="5" fill="#fff" opacity=".12" transform="rotate(-20 72 146)"/>
      ${[[90, 142], [110, 142], [91, 152], [109, 152], [92, 162], [108, 162]].map(([x, y]) =>
        `<circle cx="${x}" cy="${y}" r="2.3" fill="${url('gold')}" stroke="${GOLD_DK}" stroke-width=".7"/>`).join('')}
    </g>
    <!-- gold epaulettes -->
    ${[-1, 1].map((sd) => { const m = (x) => 100 + sd * (x - 100);
      return `<path d="M${m(49)} 128 Q${m(55)} 119 ${m(67)} 123 L${m(65)} 131 Q${m(56)} 133 ${m(49)} 131 Z" fill="${url('gold')}" stroke="${GOLD_DK}" stroke-width=".8"/>
        <path d="M${m(51)} 131 l${sd * -.5} 5 M${m(54.5)} 132 l0 5.5 M${m(58)} 132.5 l0 5.5 M${m(61.5)} 132.5 l0 5 M${m(64.5)} 131.5 l${sd * .4} 4.5" stroke="${GOLD}" stroke-width="1.3" stroke-linecap="round"/>`; }).join('')}
    <!-- soft fur lighting -->
    <ellipse cx="68" cy="104" rx="6" ry="14" fill="#fff" opacity=".28" filter="${url('blur2')}"/>
    <path d="M62 92 A42 42 0 0 1 80 70" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" opacity=".55"/>

    <!-- ears + peaked cap -->
    <g data-p="crest">
      <g data-p="earL">${ear(1)}</g>
      <g data-p="earR">${ear(-1)}</g>
      <!-- LEARNED: idea bulb above the cap -->
      <g data-p="bulb" opacity="0">
        <g data-p="bulbRays" stroke="#f2c14e" stroke-width="2.4" stroke-linecap="round">
          ${[0, 45, 90, 135, 180, 225, 270, 315].map((a) => `<path d="M${f(Math.cos(a * DEG) * 13)} ${f(Math.sin(a * DEG) * 13)} L${f(Math.cos(a * DEG) * 18)} ${f(Math.sin(a * DEG) * 18)}"/>`).join('')}
        </g>
        <circle r="9.5" fill="#fff3b0" stroke="#e8b93c" stroke-width="1.6"/>
        <path d="M-3 3 Q0 -3 3 3" fill="none" stroke="#e8a12c" stroke-width="1.4" stroke-linecap="round"/>
        <rect x="-4.6" y="8.5" width="9.2" height="5" rx="1.6" fill="#9fb1c8"/>
        <path d="M-4.4 10.6 H4.4" stroke="#7d8fa6" stroke-width="1"/>
        <ellipse cx="-3.6" cy="-4" rx="2.6" ry="1.6" fill="#fff" opacity=".9"/>
      </g>
      <g data-p="hat"><g transform="translate(100 60) scale(1.12) translate(-100 -60)">
        <path d="M79 60 Q77 42 100 39 Q123 42 121 60 Z" fill="${url('coat')}"/>
        <rect x="79.2" y="52" width="41.6" height="2.4" fill="${PIPING}"/>
        <rect x="79" y="54.4" width="42" height="4.2" fill="${url('gold')}"/>
        <circle cx="100" cy="47" r="3.6" fill="${url('gold')}" stroke="${GOLD_DK}" stroke-width=".8"/>
        <path d="M88 44 Q92 41 98 40.5" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" opacity=".45"/>
        <path d="M76 59 Q100 56 124 59 Q119 66 100 67 Q81 66 76 59 Z" fill="#1d2a45"/>
        <path d="M82 62 Q100 64.5 118 62" fill="none" stroke="#fff" stroke-width="1" stroke-linecap="round" opacity=".3"/>
      </g></g>
    </g>

    <g data-p="face">
      <ellipse cx="70" cy="117" rx="7" ry="4.2" data-p="blushL" fill="${C.blush}" filter="${url('blur2')}"/>
      <ellipse cx="130" cy="117" rx="7" ry="4.2" data-p="blushR" fill="${C.blush}" filter="${url('blur2')}"/>
      <!-- cream muzzle + cheek fluff -->
      <ellipse cx="100" cy="115" rx="17" ry="11.5" fill="${C.cream}"/>
      <ellipse cx="80" cy="113" rx="9" ry="7" fill="${C.cream}" opacity=".85"/>
      <ellipse cx="120" cy="113" rx="9" ry="7" fill="${C.cream}" opacity=".85"/>
      <g data-p="eyeL"><ellipse cx="84" cy="102" rx="6.2" ry="7.2" fill="${INK}"/><circle cx="82" cy="99" r="2.3" fill="#fff"/><circle cx="86.2" cy="104.6" r="1" fill="#fff"/></g>
      <g data-p="eyeR"><ellipse cx="116" cy="102" rx="6.2" ry="7.2" fill="${INK}"/><circle cx="114" cy="99" r="2.3" fill="#fff"/><circle cx="118.2" cy="104.6" r="1" fill="#fff"/></g>
      <path data-p="happy" d="M77 104 Q84 96 91 104 M109 104 Q116 96 123 104" fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round" opacity="0"/>
      <path data-p="squint" d="M79 97 L88 102 L79 107 M121 97 L112 102 L121 107" fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" opacity="0"/>
      <path data-p="browL" d="M79 89 L89 89" stroke="${C.brow}" stroke-width="2.4" stroke-linecap="round" opacity=".8"/>
      <path data-p="browR" d="M111 89 L121 89" stroke="${C.brow}" stroke-width="2.4" stroke-linecap="round" opacity=".8"/>
      <!-- whiskers -->
      <path d="M86 115 L74 113 M86 118 L75 119.5 M114 115 L126 113 M114 118 L125 119.5" stroke="${C.brow}" stroke-width=".9" stroke-linecap="round" opacity=".45"/>
      <!-- nose -->
      <path d="M96.5 109.5 Q100 108 103.5 109.5 Q101.5 113.5 100 113.5 Q98.5 113.5 96.5 109.5 Z" fill="#4a2a22" stroke="#4a2a22" stroke-width="1.2" stroke-linejoin="round"/>
      <ellipse cx="98.8" cy="109.6" rx="1.1" ry=".6" fill="#fff" opacity=".6"/>
      <path d="M100 113.5 L100 116.5" stroke="#4a2a22" stroke-width="1.2" stroke-linecap="round"/>
      <path data-p="mouth" d="M95 118 Q100 118 105 118 Q100 118 95 118 Z" fill="#4a2a22" stroke="#4a2a22" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round"/>
      <!-- little buck teeth (ride on the upper lip) -->
      <g data-p="teeth"><rect x="98" y="118.4" width="4" height="3.4" rx=".9" fill="#fff" stroke="#e2d8ca" stroke-width=".6"/>
      <path d="M100 118.6 L100 121.6" stroke="#e2d8ca" stroke-width=".5"/></g>
      <path data-p="tear" d="M78 106 Q81.4 111.6 78 114.6 Q74.6 111.6 78 106 Z" fill="#9fd0f0" stroke="#fff" stroke-width=".8" opacity="0"/>
      <path data-p="sweat" d="M136 84 Q141 92 136 96 Q131 92 136 84 Z" fill="#a9d6f2" opacity="0"/>
      <g data-p="monocle" opacity="0"></g>
    </g>

    <!-- tie -->
    <g data-p="tie">
      <path d="M100 130 L90.5 125 Q88.8 130 90.5 135 Z" fill="${url('tie')}" stroke="${C.accentDk}" stroke-width="1.3" stroke-linejoin="round"/>
      <path d="M100 130 L109.5 125 Q111.2 130 109.5 135 Z" fill="${url('tie')}" stroke="${C.accentDk}" stroke-width="1.3" stroke-linejoin="round"/>
      <rect x="97" y="127" width="6" height="6" rx="2" fill="${C.accentDk}"/>
    </g>

    <!-- SAVING: golden acorn held in the paw -->
    <g data-p="acorn" opacity="0">${ACORN}</g>
    <!-- coat sleeves -->
    ${['L', 'R'].map((n) => `<path data-p="sleeve${n}" fill="none" stroke="${COAT1}" stroke-width="11" stroke-linecap="round"/>
      <path data-p="sleeve${n}i" fill="none" stroke="${url('coat')}" stroke-width="8.4" stroke-linecap="round"/>
      <circle data-p="cuff${n}" r="5.6" fill="#f4f6fb" stroke="${GOLD}" stroke-width="1.4"/>`).join('')}
    <g data-p="trayC" opacity="0"><g>
      <ellipse cx="0" cy="0" rx="24" ry="4.5" fill="#b9c0d8"/>
      <ellipse cx="0" cy="-1.5" rx="23" ry="3.6" fill="#f3f5fb"/>
      <path d="M-18 -1 A18 17 0 0 1 18 -1 Z" fill="${url('silver')}"/>
      <g clip-path="${url('dome')}"><rect data-p="shimmer" x="-60" y="-20" width="7" height="22" fill="#fff" opacity=".95" transform="skewX(-20)"/></g>
      <path d="M-11 -6 A12 11 0 0 1 -4 -13" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="0" cy="-19" r="3.2" fill="${C.accent2}"/>
    </g></g>
    <g data-p="trayK" opacity="0"><g>
      <ellipse cx="0" cy="0" rx="24" ry="4.5" fill="#b9c0d8"/>
      <ellipse cx="0" cy="-1.5" rx="23" ry="3.6" fill="#f3f5fb"/>
      <g transform="rotate(-8)">
        <rect x="-11" y="-17" width="22" height="14" rx="2" fill="#fffdf8" stroke="#e3dccd" stroke-width="1"/>
        <rect x="-11" y="-17" width="22" height="3" rx="1.5" fill="${COAT0}"/>
        <rect x="-8" y="-11.5" width="9" height="1.6" rx=".8" fill="#cfd6e3"/>
        <rect x="-8" y="-8.4" width="6" height="1.6" rx=".8" fill="#cfd6e3"/>
        <path d="M8.2 -12.2 A3.4 3.4 0 1 0 8.2 -6.2 M3 -10.1 H6.9 M3 -8.3 H6.9" fill="none" stroke="${GOLD_DK}" stroke-width="1.2" stroke-linecap="round"/>
      </g>
    </g></g>
    <g data-p="glass" opacity="0">
      <path d="M0 0 L7 -9" stroke="${COAT0}" stroke-width="4.5" stroke-linecap="round"/>
      <circle cx="12" cy="-16" r="9" fill="#e3f1fb" fill-opacity=".85" stroke="${url('gold')}" stroke-width="3.5"/>
      <path d="M7 -18.5 A6 6 0 0 1 12 -22" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
    </g>

    <!-- WORK: floating laptop (code scrolls, progress fills) -->
    <g data-p="lap" opacity="0">
      <path d="M-27 0 L27 0 L31 6 Q31 8 29 8 L-29 8 Q-31 8 -31 6 Z" fill="${url('silver')}" stroke="#b9c0d8" stroke-width="1"/>
      <path d="M-9 4 L9 4" stroke="#b9c0d8" stroke-width="1.6" stroke-linecap="round"/>
      <rect x="-23" y="-31" width="46" height="31" rx="3.5" fill="${COAT1}" stroke="#565a86" stroke-width="1.5"/>
      <g clip-path="${url('lapClip')}"><g data-p="code">
        ${[0,1,2,3,4,5,6,7,8,9,10,11].map((i) => {
          const cat = ['#7fb3e6', '#e6b45a', '#e8857c'][[0, 2, 2, 1, 2, 0][i % 6]], pw = [12, 9, 14, 10, 8, 13][i % 6], aw = [5, 3, 4, 7, 3, 5][i % 6];
          const y = -26 + i * 4;
          return `<circle cx="-16" cy="${y + 1}" r="1.3" fill="${cat}"/><rect x="-13" y="${y}" width="${pw}" height="2" rx="1" fill="#c9d6ea" opacity=".85"/>
            <rect x="${17 - aw}" y="${y}" width="${aw}" height="2" rx="1" fill="#f2f5fa"/>`;
        }).join('')}
      </g></g>
      <rect x="-18" y="-6" width="36" height="2.4" rx="1.2" fill="#3a3d5c"/>
      <rect data-p="prog" x="-18" y="-6" width="0" height="2.4" rx="1.2" fill="${C.accent}"/>
      <path d="M-20 -29 L-4 -29" stroke="#fff" stroke-width="1.2" stroke-linecap="round" opacity=".25"/>
    </g>

    <!-- TEACH: floating app window he walks you through -->
    <g data-p="scr" opacity="0">
      <rect x="-26" y="-20" width="52" height="40" rx="5" fill="#fff" stroke="${C.iri1}" stroke-width="1.6"/>
      <path d="M-26 -15 A5 5 0 0 1 -21 -20 L21 -20 A5 5 0 0 1 26 -15 L26 -13 L-26 -13 Z" fill="#dce7f5"/>
      <circle cx="-21.5" cy="-16.5" r="1.3" fill="#ff8f8f"/><circle cx="-17.5" cy="-16.5" r="1.3" fill="#ffd36e"/><circle cx="-13.5" cy="-16.5" r="1.3" fill="#7fd8a4"/>
      <rect data-p="rowHi" x="-24.5" y="-8.5" width="49" height="9" rx="4.5" fill="${NEON}" opacity="0"/>
      ${[0,1,2].map((i) => { const y = -4 + 9 * i, col = ['#3f74b9', '#d6ae58', '#e07a6f'][i];
        const icon = [
          `<path d="M-23 ${y + 2.2} V${y - .4} L-20.5 ${y - 2.6} L-18 ${y - .4} V${y + 2.2} Z" fill="${col}"/>`,                         // house: mandatory & regular
          `<path d="M-19.6 ${y - 2.8} L-22.4 ${y + .4} H-20.4 L-21.2 ${y + 2.8} L-18.2 ${y - .6} H-20.2 Z" fill="${col}"/>`,             // bolt: mandatory & irregular
          `<path d="M-23 ${y - 1.4} H-18.6 V${y + 1.2} Q-18.6 ${y + 2.6} -20.8 ${y + 2.6} Q-23 ${y + 2.6} -23 ${y + 1.2} Z M-18.6 ${y - .6} Q-16.8 ${y - .6} -16.8 ${y + .4} Q-16.8 ${y + 1.2} -18.6 ${y + 1.2}" fill="${col}" stroke="${col}" stroke-width=".5"/>`  // cup: everyday
        ][i];
        return `${icon}<rect x="-14.5" y="${y - 1.75}" width="${[16, 12, 14][i]}" height="3.5" rx="1.75" fill="#e4e8f0"/>
          <rect x="6" y="${y - 2.5}" width="15" height="5" rx="2.5" fill="#dce7f5"/>
          <rect x="6" y="${y - 2.5}" width="${[12, 5, 9][i]}" height="5" rx="2.5" fill="${col}" opacity=".55"/>`; }).join('')}
      <rect data-p="hilite" x="4.5" y="-8" width="18" height="8" rx="4" fill="none" stroke="${C.accent}" stroke-width="1.6" opacity="0"/>
      <circle data-p="ripple" r="2" fill="none" stroke="${C.accent}" stroke-width="1.4" opacity="0"/>
      <path data-p="cursor" d="M0 0 L0 9 L2.6 6.6 L4.6 10.6 L6.2 9.8 L4.3 6 L7.6 5.8 Z" fill="${INK}" stroke="#fff" stroke-width="1" stroke-linejoin="round" opacity="0"/>
      <g data-p="check" opacity="0">
        <circle r="12" fill="#e7f8ef" stroke="#3fb27f" stroke-width="1.5"/>
        <path data-p="checkPath" d="M-6 0 L-1.5 4.5 L6.5 -4.5" fill="none" stroke="#2f9a6a" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="20" stroke-dashoffset="20"/>
      </g>
      <g data-p="dots">
        <circle data-p="dot0" cx="-6" cy="25" r="2" fill="${C.accent}"/>
        <circle data-p="dot1" cx="0" cy="25" r="2" fill="${C.accent}"/>
        <circle data-p="dot2" cx="6" cy="25" r="2" fill="${C.accent}"/>
      </g>
    </g>

    <!-- LISTEN: note pad with lines that write themselves -->
    <g data-p="pad" opacity="0">
      <rect x="-10" y="-26" width="20" height="24" rx="2.5" fill="#fffdf6" stroke="#e6dcc8" stroke-width="1"/>
      <path d="M-6 -26 v-2 M-2 -26 v-2 M2 -26 v-2 M6 -26 v-2" stroke="#b9c0d8" stroke-width="1.4" stroke-linecap="round"/>
      <rect data-p="padL0" x="-7" y="-20" width="0" height="1.8" rx=".9" fill="${C.brow}"/>
      <rect data-p="padL1" x="-7" y="-15" width="0" height="1.8" rx=".9" fill="${C.brow}"/>
      <rect data-p="padL2" x="-7" y="-10" width="0" height="1.8" rx=".9" fill="${C.brow}"/>
      <g data-p="pencil"><path d="M0 0 L4 -10 L6.5 -9 L2.5 1 Z" fill="${C.accent2}"/><path d="M0 0 L2.5 1 L0.6 2.6 Z" fill="${INK}"/><path d="M4 -10 L6.5 -9 L7.2 -10.8 L4.7 -11.8 Z" fill="${C.blush}"/></g>
    </g>

    <!-- TEACH: pointer baton -->
    <g data-p="baton" opacity="0">
      <path d="M0 0 L0 -28" stroke="${COAT0}" stroke-width="2.6" stroke-linecap="round"/>
      <circle cy="-29" r="2.6" fill="${C.accent2}" stroke="#fff" stroke-width="1"/>
    </g>

    <g data-p="napkin">
      <circle cy="6" r="4.2" fill="none" stroke="${GOLD}" stroke-width="1.4"/>
      <g transform="translate(-2 9) rotate(12)"><circle r="2.4" fill="none" stroke="${GOLD_DK}" stroke-width="1.4"/><path d="M0 2.4 V12 M0 9 h2.6 M0 11.4 h2" stroke="${GOLD_DK}" stroke-width="1.4" stroke-linecap="round"/></g>
      <g transform="translate(3 9) rotate(-14)"><circle r="2.2" fill="none" stroke="${GOLD}" stroke-width="1.3"/><path d="M0 2.2 V10.5 M0 8 h2.3 M0 10 h1.8" stroke="${GOLD}" stroke-width="1.3" stroke-linecap="round"/></g>
    </g>

    <g data-p="gloveR">${glove(-5.5)}<g data-p="thumbR" opacity="0">
      <rect x="-3" y="-17" width="6" height="10" rx="3" fill="${url('paw')}" stroke="${C.b2}" stroke-width="1"/>
      <ellipse cx="-1" cy="-14" rx="1.4" ry="2" fill="#fff" opacity=".5"/></g></g>
    <g data-p="gloveL">${glove(5.5)}</g>

    <path data-p="spark1" d="${star}" fill="#ffd36e" opacity="0"/>
    <path data-p="spark2" d="${star}" fill="${C.accent}" opacity="0"/>
  </g></g></g>

  <!-- free-floating fx (not squashed with the body) -->
  <!-- SAVING: mini vault at his feet; the acorn flies into it -->
  <g data-p="vault" opacity="0">
    <rect x="-15" y="-26" width="30" height="26" rx="3.5" fill="#2e4b50" stroke="#1f3538" stroke-width="1.4"/>
    <rect x="-11.5" y="-22.5" width="23" height="19" rx="2" fill="#35565b"/>
    <rect x="-6" y="-29" width="12" height="3" rx="1.5" fill="#1f3538"/>
    <g data-p="vwheel" stroke="${GOLD}" stroke-width="1.8" stroke-linecap="round" fill="none">
      <circle r="5.6"/><path d="M0 -5.6 V5.6 M-5.6 0 H5.6"/><circle r="1.6" fill="${GOLD}"/>
    </g>
    <circle data-p="vring" r="8" fill="none" stroke="#f2c14e" stroke-width="2" opacity="0"/>
  </g>
  <g data-p="acornFly" opacity="0">${ACORN}</g>
  <!-- SPENT: a coin flutters away -->
  <g data-p="coinOut" opacity="0">
    <path d="M-10 -2 C-14 -8 -20 -6 -22 -2 C-18 -3 -14 -1 -10 -2 Z M10 -2 C14 -8 20 -6 22 -2 C18 -3 14 -1 10 -2 Z" fill="#fff" stroke="#c9d3e2" stroke-width=".8"/>
    <circle r="7" fill="${url('gold')}" stroke="${GOLD_DK}" stroke-width="1"/>
    <path d="M2.6 -3 A3.4 3.4 0 1 0 2.6 3 M-3.2 -.9 H1 M-3.2 .9 H1" fill="none" stroke="${GOLD_DK}" stroke-width="1.1" stroke-linecap="round"/>
  </g>
  <!-- WHAT IF / COMPARE: a small chart card -->
  <g data-p="chart" opacity="0">
    <rect x="-26" y="-24" width="52" height="46" rx="8" fill="#fff" stroke="#dfe3ea" stroke-width="1.4"/>
    <path d="M-19 14 H19" stroke="#d3d8e0" stroke-width="1.4" stroke-linecap="round"/>
    <path data-p="chAvg" d="M-21 -6 H21" stroke="#9aa3b2" stroke-width="1.2" stroke-dasharray="2.4 2" opacity="0"/>
    <rect data-p="chA" x="-14" y="14" width="11" height="0" rx="2.5" fill="${COAT0}"/>
    <rect data-p="chB" x="3" y="14" width="11" height="0" rx="2.5" fill="${NEON}"/>
    <g data-p="chIcons">
      <circle cx="-8.5" cy="18.6" r="1.6" fill="${COAT0}"/>
      <g data-p="chIconB"><circle cx="6.6" cy="18.6" r="1.4" fill="#9aa3b2"/><circle cx="10.4" cy="18.6" r="1.4" fill="#9aa3b2"/></g>
    </g>
    <g data-p="chTag" opacity="0"><rect x="-1" y="-20" width="21" height="10" rx="5" fill="${INK}"/>
      <path d="M3.4 -15 h4.4 M5.6 -17.2 v4.4" stroke="${NEON}" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M15.6 -17.4 A2.6 2.6 0 1 0 15.6 -12.6 M11 -15.8 H14.2 M11 -14.2 H14.2" fill="none" stroke="#fff" stroke-width="1" stroke-linecap="round"/></g>
  </g>
  <!-- SAFE TO SPEND / CAUTION badges -->
  <g data-p="okBadge" opacity="0">
    <circle r="12.5" fill="${NEON}"/>
    <path d="M-5.6 0 L-1.6 4 L6 -4.6" fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
  <g data-p="warnBadge" opacity="0">
    <path d="M0 -13 L12.4 9 H-12.4 Z" fill="#fff4d6" stroke="#e0a32e" stroke-width="1.8" stroke-linejoin="round"/>
    <path d="M0 -5 V2.4" stroke="#c4841a" stroke-width="2.8" stroke-linecap="round"/><circle cy="5.8" r="1.6" fill="#c4841a"/>
  </g>
  <g data-p="think">
    <circle data-p="think0" cx="148" cy="62" r="3" fill="#fff" stroke="${C.iri1}" stroke-width="1.2" opacity="0"/>
    <circle data-p="think1" cx="158" cy="48" r="4.5" fill="#fff" stroke="${C.iri1}" stroke-width="1.2" opacity="0"/>
    <circle data-p="think2" cx="172" cy="30" r="7" fill="#fff" stroke="${C.iri1}" stroke-width="1.2" opacity="0"/>
  </g>
  ${confetti}
  <path data-p="heart0" d="${heart}" fill="${C.blush}" opacity="0"/>
  <path data-p="heart1" d="${heart}" fill="${C.accent}" opacity="0"/>
  <path data-p="heart2" d="${heart}" fill="${C.accent2}" opacity="0"/>
</svg>`;
  }

  /* ---------- Math helpers ---------- */
  const PI = Math.PI, DEG = PI / 180;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, k) => a + (b - a) * k;
  const smooth = (a, b, x) => { const k = clamp((x - a) / (b - a), 0, 1); return k * k * (3 - 2 * k); };
  const win = (ph, a, b, c, d) => smooth(a, b, ph) * (1 - smooth(c, d, ph));
  const phase = (tau, period) => (tau % period) / period;
  const f = (n) => n.toFixed(2);

  /* ---------- Rig parameters ---------- */
  const REST = {
    floatY: 0, lean: 0, sqx: 0, sqy: 0,
    fx: 0, fy: 0,
    crx: 0, cry: 0, crr: 0,
    eyeOpen: 1, happy: 0, squint: 0, smile: 0, mouthO: 0, blush: 0.55,
    browL: 0, browR: -8, browLY: 0, browRY: -1.5,
    earL: 0, earR: 0,
    hatLift: 0, hatRot: -8, hatX: 0, tieRot: 0,
    rhx: 50, rhy: 142, lhx: 150, lhy: 144,
    attChin: 0, attEar: 0, attHat: 0,
    trayC: 0, trayK: 0, glass: 0, glassRot: 0,
    sweat: 0, spark: 0, think: 0, monoDrop: 0,
    lap: 0, scr: 0, pad: 0, baton: 0,              // work / teach props (0..1)
    shimmer: -1, sweatY: 0, tapY: 0, hatSpin: 0, confT: -1,   // unsmoothed ↓
    workT: 0, teachT: -1, padT: 0, checkDraw: 0, dotsOn: 0,
    acornT: -1, spentT: -1, vSpin: 0, tearY: 0, ringT: -1,                  // unsmoothed (new moods)
    bulb: 0, acorn: 0, vault: 0, ok: 0, warn: 0, tear: 0, frown: 0, tailDrop: 0,
    thumb: 0, chart: 0, chMode: 0, chT: 0, flick: 0
  };
  const UNSMOOTHED = new Set(['shimmer', 'sweatY', 'tapY', 'hatSpin', 'confT', 'workT', 'teachT', 'padT', 'checkDraw', 'dotsOn',
                              'acornT', 'spentT', 'vSpin', 'tearY', 'ringT', 'chT', 'chMode', 'flick']);
  // App window sits up-right of his head; rows' button centres in window space.
  const SCR = [176, 24], BTN = [[13.5, -4], [13.5, 5], [13.5, 14]], TEACH_STEP = 1.9;
  const HAT_SEAT = 0;
  const CREST_PIVOT = [100, 90], HAT_PIVOT = [100, 60], TIE = [100, 130];
  const ATTACH = { chin: [91, 123], ear: [58, 64], hatGrip: [82, 61] };

  /* ---------- State poses ---------- */
  const POSES = {
    idle(T, tau) {                                        // squirrel life: ear twitch, look around, tail flick, nibble
      const ph = phase(tau, 12);
      const tw = win(ph, .05, .065, .08, .1);
      T.earL += 14 * tw * Math.sin(tau * 40); T.earR -= 10 * win(ph, .09, .105, .12, .14) * Math.sin(tau * 40);
      const look = win(ph, .18, .23, .38, .43);
      T.fx += 7 * Math.sin((ph - .18) / .25 * 2 * PI) * look; T.browLY -= 1.5 * look; T.browRY -= 1.5 * look;
      T.flick = win(ph, .5, .53, .55, .62);
      const nib = win(ph, .7, .75, .9, .95);
      T.acorn = nib; T.rhx = lerp(T.rhx, 95, nib); T.rhy = lerp(T.rhy, 132, nib);
      T.mouthO = Math.max(T.mouthO, .35 * nib * Math.max(0, Math.sin(tau * 18)));
      T.happy = Math.max(T.happy, nib > .5 ? .4 : 0); T.fy += 1.5 * nib;
    },
    listening(T, tau, L) {
      const side = Math.tanh(L.x * 2.5);
      T.lean += 3 + 5 * side; T.crr += 4 * side; T.fx += 2 * side;
      const tw = win(phase(tau, 2.6), 0, .04, .10, .16);
      T.earL = 14 + 7 * tw; T.earR = -14 - 7 * tw;
      T.eyeOpen = 1.15; T.browLY = -3; T.browRY = -4; T.browR = -4;
      T.attEar = 1;
      T.floatY -= 2 * Math.abs(Math.sin(tau * 2.2));       // eager little bounces
      // takes notes on a little pad while you talk
      T.pad = 1; T.padT = phase(tau, 4.2);
      T.lhx = 140; T.lhy = 132; T.fy += 2 + 2 * Math.abs(Math.sin(tau * 1.3)); // glances down at his notes
      T.lhy += 1.2 * Math.sin(tau * 14) * (T.padT < .82 ? 1 : 0);   // scribble jiggle
    },
    thinking(T, tau, L) {
      T.fx = L.x * 2 + 2 * Math.sin(tau * 0.8); T.fy = -4;
      T.crr -= 4; T.browR = -16; T.browRY = -4; T.browL = 4;
      T.attChin = 1;
      T.tapY = -3 * Math.pow(Math.abs(Math.sin(tau * 5)), 4);
      T.glass = 1; T.glassRot = 10 * Math.sin(tau * 1.7);
      T.lhx = 156 + 8 * Math.cos(tau * 1.7); T.lhy = 112 + 6 * Math.sin(tau * 3.4);
      T.think = 1;
    },
    working(T, tau, L) {
      // types away on a floating laptop, gloves taking turns on the keys
      const s = Math.sin(tau * 2 * PI / 0.9);
      T.floatY -= 1.2 * Math.abs(s); T.lean += 3; T.crr += 1.5 * s;
      T.lap = 1; T.workT = tau;
      const beat = Math.floor(tau * 7), alt = beat % 2;
      const tap = Math.pow(Math.abs(Math.sin(tau * 7 * PI)), 3);
      T.lhx = 160 + 14 * alt + ((beat * 7) % 5); T.lhy = 163 - 4 * tap;
      T.rhx = 56; T.rhy = 140;
      if (phase(tau, 6) > .7) { T.rhx = 146; T.rhy = 164 - 3 * (1 - tap); }  // second glove joins in
      T.fx = 4 + L.x * 1.5; T.fy = 3;                         // eyes on the screen
      T.browL = 6; T.browR = -6; T.browRY = 0; T.smile = .6;
      T.tieRot = 3 * Math.sin(tau * 2 * PI / 0.9 + 1);
      T.blush = .45;
    },
    teaching(T, tau, L) {
      // walks you through an app: cursor visits step 1 → 2 → 3, clicks each
      T.scr = 1; T.baton = 1; T.teachT = tau; T.dotsOn = 1;
      const u = (tau % TEACH_STEP) / TEACH_STEP;
      T.lean -= 2; T.crr += 3;
      T.fx = 4; T.fy = -2; T.browL = -4; T.browLY = -3; T.browRY = -4; T.browR = -10;
      // talks while the cursor travels, smiles once it clicks
      const talk = (1 - smooth(.42, .5, u)) * Math.max(0, Math.sin(tau * 11));
      T.mouthO = .75 * talk; T.smile = smooth(.5, .62, u);
      T.lhx = 146 + 2 * Math.sin(tau * 2); T.lhy = 100 + 2 * Math.cos(tau * 2.4);
      T.rhx = 58; T.rhy = 128 - 6 * win(u, .5, .6, .7, .85);   // little "see?" gesture on the click
      T.eyeOpen = 1.1;
    },
    checking(T, tau, L) {
      // magnifier sweeps the window, then a tick draws in
      const ph = phase(tau, 3.6);
      T.scr = 1;
      const look = 1 - smooth(.56, .64, ph);
      T.glass = look; T.glassRot = -14 + 8 * Math.sin(ph * 2 * PI * 2);
      T.lhx = lerp(148, 156 + 14 * Math.sin(ph * 2 * PI * 1.6), look);
      T.lhy = lerp(128, 40 + 4 * Math.cos(ph * 2 * PI * 3.2), look);
      T.fx = 5 * look + L.x; T.fy = -4 * look;
      T.browR = -16 * look; T.browRY = -4 * look; T.browL = 4 * look;
      T.squint = .3 * look;                                   // focused squint
      T.checkDraw = smooth(.62, .78, ph) * (1 - smooth(.94, 1, ph));
      const ok = win(ph, .7, .76, .9, .97);
      T.happy = ok; T.smile = Math.max(T.smile, ok); T.floatY -= 5 * win(ph, .7, .75, .75, .84);
      T.rhx = lerp(50, 70, ok); T.rhy = lerp(142, 120, ok);   // tiny thumbs-up height
    },
    needs_approval(T, tau) {
      const k = win(phase(tau, 3.8), .05, .2, .42, .58);
      T.fy += 5 * k; T.cry += 3 * k; T.crr -= 2 * k; T.hatRot -= 6 * k; T.sqy -= 0.03 * k;
      T.eyeOpen = 1 - 0.45 * k; T.browL = -3; T.browLY = -3; T.browRY = -4;
      T.trayK = 1; T.rhx = 28 - 3 * k; T.rhy = 146;
      T.lhx = 122; T.lhy = 142;
      T.blush = 0.55 + 0.35 * k;
    },
    success(T, tau) {
      const ph = phase(tau, 2.8);
      const up = win(ph, .02, .2, .62, .8);
      const bow = win(ph, .18, .32, .5, .66);
      const hop = win(ph, .68, .75, .75, .86);
      T.attHat = up;
      T.hatLift = 12 * up; T.hatRot = -8 - 16 * up; T.hatX = -3 * up;
      T.fy += 4 * bow; T.cry += 3 * bow; T.sqy -= 0.03 * bow; T.floatY -= 12 * hop;
      T.sqy += 0.05 * hop; T.sqx -= 0.03 * hop;
      T.happy = 1; T.smile = 1; T.blush = 0.95;
      T.lhx = lerp(122, 156, hop); T.lhy = lerp(142, 104, hop);   // little fist pump
      T.spark = win(ph, .22, .32, .55, .68);
      T.confT = ph;
      T.scr = 1; T.checkDraw = 1;                          // job done: the window shows a tick
    },
    error(T, tau, L) {
      const ph = phase(tau, 3.4);
      const w = win(ph, 0, .05, .26, .38);
      const fix = win(ph, .3, .42, .76, .88);
      const straight = smooth(.44, .8, ph);
      T.tieRot = 20 * (1 - straight) + 4 * Math.sin(tau * 20) * fix * (1 - straight);
      T.squint = w; T.eyeOpen = 0.85; T.mouthO = w;
      const worry = Math.max(w, 0.55);
      T.browL = -16 * worry; T.browR = 16 * worry; T.browLY = 1; T.browRY = 1;
      T.floatY += 5 * w; T.lean += 1.5 * Math.sin(tau * 42) * w; T.sqy -= 0.04 * w; T.sqx += 0.025 * w;
      T.earL = -12 * w; T.earR = 12 * w; T.fy += 2;
      T.sweat = Math.max(w, fix * 0.9); T.sweatY = ph * 8;
      T.monoDrop = 1 - smooth(.78, .95, ph);               // monocle pops, dangles, gets put back
      T.rhx = lerp(56, 91, fix); T.rhy = lerp(138, 131, fix);
      T.lhx = lerp(144, 109, fix); T.lhy = lerp(138, 131, fix);
    },
    learned(T, tau) {                                     // "aha!": bulb pops on, happy hop, paw up
      const ph = phase(tau, 2.6), pop = win(ph, .04, .14, .8, .95);
      T.bulb = pop; T.happy = win(ph, .1, .17, .74, .88); T.smile = 1; T.blush = .9;
      T.floatY -= 9 * Math.sin(PI * clamp((ph - .08) / .2, 0, 1));
      T.rhx = lerp(50, 62, pop); T.rhy = lerp(142, 98, pop);
      T.earL = 10 * pop; T.earR = -10 * pop; T.browLY = -3 * pop; T.browRY = -3 * pop;
      T.spark = win(ph, .14, .24, .55, .7);
    },
    saving(T, tau) {                                      // stashes a golden acorn in the vault
      const ph = phase(tau, 3.4), show = win(ph, 0, .08, .3, .36);
      T.vault = 1; T.acorn = 1 - smooth(.3, .35, ph);
      T.rhx = lerp(50, 44, show); T.rhy = lerp(142, 120, show);
      T.acornT = ph >= .33 && ph < .6 ? (ph - .33) / .27 : -1;
      T.ringT = ph >= .6 && ph < .85 ? (ph - .6) / .25 : -1;
      T.vSpin = 360 * smooth(.6, .82, ph);
      const yay = win(ph, .6, .66, .9, .97);
      T.fx = ph < .6 ? -6 : 0; T.fy = ph < .6 ? 3 : 0;
      T.happy = yay; T.smile = 1; T.blush = .6 + .4 * yay;
      T.floatY -= 6 * Math.sin(PI * clamp((ph - .62) / .16, 0, 1));
      T.spark = win(ph, .6, .7, .8, .92);
    },
    spent(T, tau) {                                       // money went out: ears and tail droop, a small tear
      const ph = phase(tau, 3.6), sad = smooth(.06, .3, ph);
      T.spentT = ph < .5 ? ph / .5 : -1;
      T.earL = 22 * sad; T.earR = -22 * sad; T.tailDrop = sad;
      T.frown = .85 * sad; T.smile = 0;
      T.browL = -14 * sad; T.browR = 14 * sad; T.browLY = -1 * sad; T.browRY = -1 * sad;
      T.eyeOpen = 1 - .12 * sad; T.fy = 3 * sad; T.sqy -= .03 * sad; T.floatY += 3 * sad;
      T.tear = win(ph, .34, .44, .86, .98); T.tearY = clamp((ph - .34) / .6, 0, 1) * 10;
      const reach = win(ph, 0, .1, .35, .5);
      T.lhx = lerp(150, 158, reach); T.lhy = lerp(144, 118, reach);
      T.blush = .25;
    },
    safe_to_spend(T, tau) {                               // relaxed thumbs-up + green tick
      const b = Math.sin(tau * 3);
      T.ok = 1; T.thumb = 1; T.smile = 1; T.blush = .7; T.lean += 2;
      T.rhx = 42; T.rhy = 118 + 2 * b; T.browLY = -1; T.browRY = -2;
      T.happy = win(phase(tau, 3), .55, .6, .75, .8);
    },
    caution(T, tau) {                                     // polite "hold on": raised paw + amber sign
      T.warn = 1; T.smile = 0; T.mouthO = .18;
      T.browL = -9; T.browR = 9; T.browLY = -1; T.browRY = -1;
      T.lhx = 160; T.lhy = 100 + 1.5 * Math.sin(tau * 5); T.fx = 2; T.fy = -1; T.blush = .4;
    },
    what_if(T, tau) {                                     // "if you cut dining out by 20 %…" — the new bar grows
      const ph = phase(tau, 3.6);
      T.chart = 1; T.chMode = 0; T.chT = ph;
      const wow = win(ph, .45, .55, .85, .95);
      T.lhx = 158; T.lhy = 106 + 1.5 * Math.sin(tau * 3); T.fx = 4; T.fy = -2;
      T.eyeOpen = 1 + .15 * wow; T.mouthO = .45 * wow; T.smile = 1; T.browLY = -2 - 2 * wow; T.browRY = -2 - 2 * wow;
      T.spark = win(ph, .5, .6, .75, .88); T.blush = .6 + .3 * wow;
    },
    compare(T, tau) {                                     // you vs. the average in Finland (Statistics Finland benchmark)
      const ph = phase(tau, 3.6);
      T.chart = 1; T.chMode = 1; T.chT = ph;
      T.glass = 1; T.glassRot = -10 + 6 * Math.sin(tau * 2);
      T.lhx = 130 + 4 * Math.sin(tau * 1.6); T.lhy = 86;          // lens held up at the chart's edge
      T.fx = 4; T.fy = -3; T.browR = -12; T.browRY = -3; T.smile = .5;
      T.happy = win(ph, .7, .76, .88, .95);
    }
  };
  const STATIC_TAU = { learned: .9, saving: 2.4, spent: 2.2, safe_to_spend: 0, caution: 0, what_if: 2.6, compare: 2.2,
                       idle: 0, listening: 2.6, thinking: 0, working: 0.6, teaching: 2 * TEACH_STEP + 1.1,
                       checking: 2.9, needs_approval: 0, success: 1.26, error: 2.04 };

  function rot([x, y], [cx, cy], deg) {
    const c = Math.cos(deg * DEG), s = Math.sin(deg * DEG), dx = x - cx, dy = y - cy;
    return [cx + dx * c - dy * s, cy + dx * s + dy * c];
  }
  const crestToWorld = (p, P) => { const r = rot(p, CREST_PIVOT, P.crr); return [r[0] + P.crx, r[1] + P.cry]; };
  const hatToWorld = (p, P) => { const r = rot(p, HAT_PIVOT, P.hatRot); return crestToWorld([r[0] + P.hatX, r[1] + HAT_SEAT - P.hatLift], P); };

  /* ---------- Factory ---------- */
  let uid = 0;
  function create(container, opts = {}) {
    const u = 'b' + (++uid);
    let paletteName = PALETTES[opts.palette] ? opts.palette : 'red';
    let svg, el;
    function build() {
      container.innerHTML = template(u, PALETTES[paletteName]);
      if (opts.framing === 'head') {                          // head-and-shoulders crop for chat avatars / icons
        const sv = container.querySelector('svg');
        sv.setAttribute('viewBox', '30 6 140 140'); sv.style.overflow = 'hidden'; sv.setAttribute('aria-label', 'Oiva avatar');
        ['shadow', 'vault', 'okBadge', 'warnBadge', 'think', 'scr', 'chart', 'spark1', 'spark2', 'coinOut', 'acornFly', 'baton', 'glass', 'lap', 'trayK', 'trayC', 'pad'].forEach((n) => { const e = sv.querySelector(`[data-p="${n}"]`); if (e) e.style.display = 'none'; });
      }
      svg = container.querySelector('svg');
      el = {};
      svg.querySelectorAll('[data-p]').forEach((n) => { el[n.dataset.p] = n; });
    }
    build();

    const mq = global.matchMedia ? global.matchMedia('(prefers-reduced-motion: reduce)') : null;
    let rmSetting = opts.reducedMotion ?? 'auto';
    const reduced = () => (rmSetting === 'auto' ? !!(mq && mq.matches) : !!rmSetting);

    let state = STATES.includes(opts.state) ? opts.state : 'idle';
    let trackPointer = opts.trackPointer !== false;
    const hoverReact = opts.hoverReact !== false;
    let t = 0, last = performance.now(), stateStart = 0, popStart = -10, popAmp = 1, pokeStart = -10;
    let nextBlink = 1.5, blinkStart = -10;
    const P = Object.assign({}, REST);
    const look = { x: 0, y: 0 };
    let pointer = null, manual = null, lastMove = 0, raf = 0;
    let hovered = false, hover = 0;
    let jig = 0, jv = 0, prevLX = 0;           // jelly spring (degrees of skew)

    function gazeTarget() {
      const src = manual || pointer;
      if (!src) return { x: 0, y: 0 };
      if (!manual && performance.now() - lastMove > 6000) return { x: 0, y: 0 };
      const r = svg.getBoundingClientRect(), RANGE = 260;
      const soft = (v) => clamp(v / Math.sqrt(1 + v * v), -1, 1);
      return { x: soft((src.x - (r.left + r.width * 0.5)) / RANGE), y: soft((src.y - (r.top + r.height * 0.5)) / RANGE) };
    }

    let fps = opts.fps || 0;                   // 0 = the display's rate; the chat helper caps it (calmer, cheaper)
    function frame(now) {
      if (fps && now - last < 1000 / fps - 2) { raf = requestAnimationFrame(frame); return; }
      const dt = Math.max(1e-3, Math.min(0.05, (now - last) / 1000)); last = now; t += dt;
      const rm = reduced(), motion = rm ? 0 : 1;

      const g = gazeTarget(), gk = 1 - Math.exp(-dt * (rm ? 20 : 5));
      look.x += (g.x - look.x) * gk; look.y += (g.y - look.y) * gk;
      hover += ((hovered ? 1 : 0) - hover) * (1 - Math.exp(-dt * 8));

      // jelly: gaze velocity drives a damped spring, so quick mouse swipes make him wobble
      const vel = (look.x - prevLX) / dt; prevLX = look.x;
      if (rm) { jig = 0; jv = 0; }
      else {
        jv += (-140 * jig - 7 * jv - vel * (40 + 60 * hover)) * dt;
        jig = clamp(jig + jv * dt, -12, 12);
      }

      const T = Object.assign({}, REST);
      const fl = Math.sin(t * 2 * PI / 3.2) * motion;
      T.floatY = 0;                                         // stands on his feet now
      T.sqy += 0.012 * Math.cos(t * 2 * PI / 3.2) * motion;
      T.rhy += 3 * Math.sin(t * 2 * PI / 3.2 - 0.7) * motion;
      T.lhy += 3 * Math.sin(t * 2 * PI / 3.2 - 1.3) * motion;
      T.fx += look.x * 6; T.fy += look.y * 4;
      T.crx += look.x * 2.5 * motion; T.cry += look.y * 1.5 * motion;
      T.crr += look.x * 3 * motion; T.lean += look.x * 3 * motion;
      POSES[state](T, rm ? STATIC_TAU[state] : t - stateStart, look);
      if (hoverReact) {
        T.floatY -= 2 * hover * motion; T.blush += 0.35 * hover; T.smile = Math.max(T.smile, hover);
        T.earL += 8 * hover; T.earR -= 8 * hover; T.rhy -= 4 * hover; T.lhy -= 4 * hover;
        if (state === 'idle') T.happy = Math.max(T.happy, hover);
      }
      // boop reaction: big blush, happy eyes for a moment
      const pk = clamp((t - pokeStart) / 1.2, 0, 1), boop = pk < 1 ? 1 - pk : 0;
      if (boop > 0) { T.happy = Math.max(T.happy, boop > .2 ? 1 : 0); T.blush = Math.max(T.blush, 1); T.smile = 1; T.earL += 12 * boop; T.earR -= 12 * boop; }
      if (rm) { T.shimmer = -1; T.sweatY = 0; T.tapY = 0; T.hatSpin = 0; T.acornT = -1; T.spentT = -1; T.ringT = -1; T.vSpin = 0; }

      const k = 1 - Math.exp(-dt * (rm ? 30 : 11));        // friendly and quick
      for (const key in T) P[key] = UNSMOOTHED.has(key) ? T[key] : P[key] + (T[key] - P[key]) * k;

      if (!rm && t > nextBlink) { blinkStart = t; nextBlink = t + 2.4 + Math.random() * 3.6 + (Math.random() < .2 ? -2.2 : 0); }
      const bp = (t - blinkStart) / 0.16;
      const blink = bp >= 0 && bp <= 1 ? 1 - Math.abs(bp * 2 - 1) : 0;

      const ppk = clamp((t - popStart) / 0.6, 0, 1);
      const pop = ppk < 1 ? Math.sin(ppk * PI * 2.5) * (1 - ppk) * popAmp * motion : 0;

      render(blink, pop, rm, pk);
      raf = requestAnimationFrame(frame);
    }

    function render(blink, pop, rm, pk) {
      const set = (n, a, v) => el[n].setAttribute(a, v);
      const crouch = Math.max(0, P.floatY);
      set('float', 'transform', `translate(0 ${f(Math.min(0, P.floatY))})`);
      set('lean', 'transform', `rotate(${f(P.lean)} 100 162)`);
      set('body', 'transform', `translate(100 190) skewX(${f(jig * .6)}) scale(${f(1 + P.sqx + .05 * pop + .006 * crouch)} ${f(1 + P.sqy - .08 * pop - .01 * crouch)}) translate(-100 -190)`);   // squash from the feet, so they stay planted
      const h = clamp(-P.floatY / 20, -0.3, 1);
      set('shadow', 'transform', `translate(100 193) scale(${f(1 - .28 * h)} ${f(1 - .2 * h)}) translate(-100 -196)`);
      set('shadow', 'opacity', f(.6 - .3 * h));

      set('crest', 'transform', `translate(${f(P.crx)} ${f(P.cry)}) rotate(${f(P.crr)} ${CREST_PIVOT[0]} ${CREST_PIVOT[1]})`);
      set('earL', 'transform', `rotate(${f(-.5 * P.earL)} 76 76)`);
      set('earR', 'transform', `rotate(${f(-.5 * P.earR)} 124 76)`);
      set('hat', 'transform', `translate(${f(P.hatX)} ${f(HAT_SEAT - P.hatLift)}) rotate(${f(P.hatRot)} ${HAT_PIVOT[0]} ${HAT_PIVOT[1]}) rotate(${f(P.hatSpin)} ${HAT_PIVOT[0]} ${HAT_PIVOT[1] - 11})`);
      set('face', 'transform', `translate(${f(P.fx)} ${f(P.fy)})`);

      const closed = Math.max(P.happy, P.squint), shut = closed >= .6;      // squash shut, then swap (no ghosting)
      const sy = Math.max(0.08, P.eyeOpen * (1 - blink) * Math.max(0, 1 - closed / .6));
      const eyeT = (cx) => `translate(${cx} 104) scale(1 ${f(sy)}) translate(${-cx} -104)`;
      set('eyeL', 'transform', eyeT(84)); set('eyeR', 'transform', eyeT(116));
      set('eyeL', 'opacity', shut ? '0' : '1'); set('eyeR', 'opacity', shut ? '0' : '1');
      set('happy', 'opacity', shut && P.happy >= P.squint ? '1' : '0');
      set('squint', 'opacity', shut && P.squint > P.happy ? '1' : '0');
      // mouth: one shape morphing between smile, flat, frown and open
      const open = clamp(P.mouthO, 0, 1), fr = clamp(P.frown, 0, 1), sm = clamp(P.smile, 0, 1) * (1 - fr);
      const hw = 5.2 - 1.5 * open + .5 * sm, mcy = 117.6 - 1.1 * sm + 1.8 * fr;
      const mid = mcy + 3.4 * sm - 3.4 * fr, topY = mid - (mid - mcy) * open - 1 * open, botY = mid + 6 * open;
      set('mouth', 'd', `M${f(100 - hw)} ${f(mcy)} Q100 ${f(topY)} ${f(100 + hw)} ${f(mcy)} Q100 ${f(botY)} ${f(100 - hw)} ${f(mcy)} Z`);
      const teethTop = lerp(mid + .1, topY + .4, smooth(0, .3, open));
      set('teeth', 'transform', `translate(0 ${f(teethTop - 118.4)})`);
      set('teeth', 'opacity', f(1 - fr));
      set('tear', 'opacity', f(P.tear)); set('tear', 'transform', `translate(0 ${f(P.tearY)})`);
      set('blushL', 'opacity', f(Math.min(1, P.blush))); set('blushR', 'opacity', f(Math.min(1, P.blush)));
      set('browL', 'transform', `translate(0 ${f(P.browLY)}) rotate(${f(P.browL)} 84 91)`);
      set('browR', 'transform', `translate(0 ${f(P.browRY)}) rotate(${f(P.browR)} 116 91)`);
      set('sweat', 'opacity', f(P.sweat));
      set('sweat', 'transform', `translate(0 ${f(P.sweatY)})`);
      set('tie', 'transform', `rotate(${f(P.tieRot)} ${TIE[0]} ${TIE[1]})`);
      // tail: lazy swish, flicks up on success, puffs out when startled
      const tailRot = (rm ? 0 : 5 * Math.sin(t * 1.7) * (1 - .7 * P.tailDrop)) + P.lean * .8 - 8 * P.spark + (rm ? 0 : 5 * Math.sin(t * 11) * P.sweat) + 7 * P.tailDrop - 9 * P.flick;
      const tailPuff = 1 + .14 * P.sweat;
      set('tail', 'transform', `rotate(${f(tailRot)} 126 164) translate(126 164) scale(${f(tailPuff * (1 - .06 * P.tailDrop))} ${f(tailPuff * (1 - .2 * P.tailDrop))}) translate(-126 -164)`);   // droops: lower and slumped

      // monocle: drops to hang on its chain, swinging
      const md = P.monoDrop, swing = rm ? 0 : Math.sin(t * 6) * 12 * md;
      set('monocle', 'transform', `translate(${f(-4 * md)} ${f(20 * md)}) rotate(${f(28 * md + swing)} 116 104)`);

      let rx = P.rhx, ry = P.rhy;
      const glue = (p, w) => { if (w > 0.001) { rx = lerp(rx, p[0], w); ry = lerp(ry, p[1], w); } };
      glue([ATTACH.chin[0] + P.fx, ATTACH.chin[1] + P.fy], P.attChin);
      glue(crestToWorld(ATTACH.ear, P), P.attEar);
      glue(hatToWorld(ATTACH.hatGrip, P), P.attHat);
      ry += P.tapY;
      set('gloveR', 'transform', `translate(${f(rx)} ${f(ry)})`);
      set('gloveL', 'transform', `translate(${f(P.lhx)} ${f(P.lhy)})`);
      set('thumbR', 'opacity', f(clamp(P.thumb * 1.4 - .2, 0, 1)));
      // --- new-mood props ---
      const popS = (v) => f(Math.max(.01, rm ? (v > .5 ? 1 : 0) : (v <= 0 ? 0 : v >= 1 ? 1 : 1 + 2.7 * (v - 1) ** 3 + 1.7 * (v - 1) ** 2)));
      set('bulb', 'opacity', f(clamp(P.bulb * 2, 0, 1)));
      set('bulb', 'transform', `translate(100 ${f(14 + 3 * (1 - P.bulb))}) scale(${popS(P.bulb)})`);
      set('bulbRays', 'transform', `rotate(${f(rm ? 0 : t * 40)}) scale(${f(.9 + (rm ? 0 : .1 * Math.sin(t * 8)))})`);
      set('acorn', 'opacity', f(clamp(P.acorn * 1.4 - .2, 0, 1)));
      set('acorn', 'transform', `translate(${f(rx + 2)} ${f(ry - 11)}) scale(${f(.6 + .4 * P.acorn)})`);
      [['L', 1, P.lhx, P.lhy], ['R', -1, rx, ry]].forEach(([n, side, hx, hy]) => {
        const sx = 100 + side * 40, sy = 128;                                 // shoulder, under the epaulette
        const dx = hx - sx, dy = hy - sy, len = Math.max(1, Math.hypot(dx, dy));
        const ex = hx - dx / len * 6.5, ey = hy - dy / len * 6.5;            // stop just inside the paw
        const cx2 = (sx + ex) / 2 + side * len * .18, cy2 = (sy + ey) / 2 + len * .12;
        const d = `M${f(sx)} ${f(sy)} Q${f(cx2)} ${f(cy2)} ${f(ex)} ${f(ey)}`;
        set('sleeve' + n, 'd', d); set('sleeve' + n + 'i', 'd', d);
        set('cuff' + n, 'transform', `translate(${f(hx - dx / len * 4)} ${f(hy - dy / len * 4)})`);
      });
      set('napkin', 'transform', `translate(${f(P.lhx)} ${f(P.lhy + 5)})`);
      set('napkin', 'opacity', f(clamp(1 - 1.5 * Math.max(P.pad, P.baton, P.glass, P.lap), 0, 1)));

      const prop = (n, v, x, y, r = 0) => {
        set(n, 'opacity', f(clamp(v * 1.4 - .2, 0, 1)));
        set(n, 'transform', `translate(${f(x)} ${f(y)}) rotate(${f(r)}) scale(${f(0.6 + 0.4 * v)})`);
      };
      prop('trayC', P.trayC, rx, ry - 9);
      prop('trayK', P.trayK, rx, ry - 9);
      prop('glass', P.glass, P.lhx, P.lhy, P.glassRot);
      set('shimmer', 'x', f(P.shimmer < 0 ? -60 : -30 + P.shimmer * 60));

      /* --- work: laptop --- */
      const fade = (v) => f(clamp(v * 1.4 - .2, 0, 1));
      set('lap', 'opacity', fade(P.lap));
      set('lap', 'transform', `translate(168 ${f(166 - 6 * P.lap)}) scale(${f(.6 + .4 * P.lap)})`);
      set('code', 'transform', `translate(0 ${f(-((P.workT * 9) % 24))})`);
      set('prog', 'width', f(36 * smooth(0, .92, phase(P.workT, 4.5))));

      /* --- teach: app window, cursor, clicks, step dots --- */
      const sc = .6 + .4 * P.scr, sx = SCR[0], sy0 = SCR[1] - 6 * (1 - P.scr);
      set('scr', 'opacity', fade(P.scr));
      set('scr', 'transform', `translate(${sx} ${f(sy0)}) scale(${f(sc)})`);
      const toWorld = (p) => [sx + p[0] * sc, sy0 + p[1] * sc];
      let cur = null;
      if (P.teachT >= 0) {
        const n = Math.floor(P.teachT / TEACH_STEP), step = n % 3, uu = (P.teachT % TEACH_STEP) / TEACH_STEP;
        const from = BTN[(step + 2) % 3], to = BTN[step];
        const m = smooth(.05, .45, uu), arc = Math.sin(m * PI) * -8;
        cur = [lerp(from[0], to[0], m) + arc * .3 - 2, lerp(from[1], to[1], m) + arc];
        set('cursor', 'opacity', fade(P.scr));
        set('cursor', 'transform', `translate(${f(cur[0])} ${f(cur[1])}) scale(${f(1 - .15 * win(uu, .46, .5, .52, .58))})`);
        set('hilite', 'opacity', f(smooth(.4, .5, uu) * P.scr));
        set('hilite', 'y', f(to[1] - 4));
        set('rowHi', 'y', f(to[1] - 4.5)); set('rowHi', 'opacity', f(.85 * smooth(.38, .5, uu)));
        const r = smooth(.5, .8, uu);
        set('ripple', 'opacity', f((1 - r) * (uu > .5 ? 1 : 0)));
        set('ripple', 'r', f(2 + 9 * r));
        set('ripple', 'transform', `translate(${to[0]} ${to[1]})`);
        for (let i = 0; i < 3; i++) {
          const done = i < step || (i === step && uu > .5);
          set('dot' + i, 'opacity', done ? '1' : '.28');
          set('dot' + i, 'r', i === step ? f(2 + .8 * win(uu, .5, .56, .6, .75)) : '2');
        }
      } else {
        set('cursor', 'opacity', '0'); set('hilite', 'opacity', '0'); set('ripple', 'opacity', '0'); set('rowHi', 'opacity', '0');
      }
      set('dots', 'opacity', f(P.dotsOn * P.scr));
      set('check', 'opacity', f(P.scr * clamp(P.checkDraw * 4, 0, 1)));
      set('check', 'transform', `scale(${f(.6 + .4 * smooth(0, .5, P.checkDraw))})`);
      set('checkPath', 'stroke-dashoffset', f(20 * (1 - smooth(.25, 1, P.checkDraw))));

      // baton in the left glove, aimed at the cursor
      set('baton', 'opacity', fade(P.baton));
      const aim = cur ? toWorld([cur[0] - 2, cur[1] + 4]) : [P.lhx + 20, P.lhy - 40];
      const ang = Math.atan2(aim[1] - P.lhy, aim[0] - P.lhx) / DEG + 90;
      set('baton', 'transform', `translate(${f(P.lhx)} ${f(P.lhy)}) rotate(${f(ang)})`);

      /* --- listen: note pad --- */
      set('pad', 'opacity', fade(P.pad));
      set('pad', 'transform', `translate(${f(P.lhx - 2)} ${f(P.lhy - 3)}) rotate(-8) scale(${f(.6 + .4 * P.pad)})`);
      const pt = P.padT, W = [14, 11, 13];
      const ln = [smooth(0, .25, pt), smooth(.28, .52, pt), smooth(.55, .8, pt)];
      for (let i = 0; i < 3; i++) set('padL' + i, 'width', f(W[i] * ln[i]));
      const li = pt < .27 ? 0 : pt < .54 ? 1 : 2;
      const penOn = pt < .82;
      set('pencil', 'transform', `translate(${f(-7 + W[li] * ln[li] + (penOn ? 0 : 6))} ${f(-18.5 + 5 * li - (penOn ? 0 : 6))})`);

      const s = P.spark;
      // vault, flying acorn, coin out, badges (outside the body, so they don't squash)
      const fy0 = Math.min(0, P.floatY), VX = 30, VY = 186;
      set('vault', 'opacity', f(clamp(P.vault * 1.4 - .2, 0, 1)));
      set('vault', 'transform', `translate(${VX} ${VY}) scale(${popS(P.vault)})`);
      set('vwheel', 'transform', `translate(0 -13) rotate(${f(P.vSpin)})`);
      if (P.ringT >= 0) { set('vring', 'opacity', f(1 - P.ringT)); set('vring', 'r', f(8 + 14 * P.ringT)); set('vring', 'transform', 'translate(0 -27)'); }
      else set('vring', 'opacity', '0');
      if (P.acornT >= 0) {
        const c = P.acornT, sx0 = P.rhx + 2, sy0 = P.rhy - 11 + fy0;
        set('acornFly', 'opacity', '1');
        set('acornFly', 'transform', `translate(${f(lerp(sx0, VX, c))} ${f(lerp(sy0, VY - 30, c) - 34 * Math.sin(c * PI))}) rotate(${f(c * 300)}) scale(${f(1 - .25 * c)})`);
      } else set('acornFly', 'opacity', '0');
      if (P.spentT >= 0) {
        const c = P.spentT, x = P.lhx + 6 + 50 * c, y = P.lhy - 6 + fy0 - 110 * c + 8 * Math.sin(c * 12);
        set('coinOut', 'opacity', f(1 - smooth(.7, 1, c)));
        set('coinOut', 'transform', `translate(${f(x)} ${f(y)}) rotate(${f(12 * Math.sin(c * 16))}) scale(1 ${f(rm ? 1 : .55 + .45 * Math.abs(Math.cos(c * 22)))})`);
      } else set('coinOut', 'opacity', '0');
      // chart card: what-if (now vs. after the change) or compare (you vs. average)
      set('chart', 'opacity', f(clamp(P.chart * 1.4 - .2, 0, 1)));
      set('chart', 'transform', `translate(172 ${f(46 + fy0)}) scale(${popS(P.chart)})`);
      const chp = rm ? .8 : P.chT, cmp = P.chMode > .5;
      const hA = cmp ? 20 * smooth(.05, .3, chp) : 14 * smooth(.02, .2, chp);
      const hB = cmp ? 27 * smooth(.25, .5, chp) : 30 * smooth(.3, .55, chp);
      set('chA', 'height', f(hA)); set('chA', 'y', f(14 - hA));
      set('chB', 'height', f(hB)); set('chB', 'y', f(14 - hB));
      set('chB', 'fill', cmp ? '#c3c9d3' : NEON);
      set('chAvg', 'opacity', cmp ? f(smooth(.5, .6, chp)) : '0');
      set('chIconB', 'opacity', cmp ? '1' : '0');
      set('chTag', 'opacity', cmp ? '0' : f(smooth(.55, .62, chp)));
      set('chTag', 'transform', `translate(0 ${f(rm ? 0 : -3 * (1 - smooth(.55, .7, chp)))})`);
      set('okBadge', 'opacity', f(clamp(P.ok * 2, 0, 1)));
      set('okBadge', 'transform', `translate(150 ${f(34 + fy0 + (rm ? 0 : 1.5 * Math.sin(t * 2.4)))}) scale(${popS(P.ok)})`);
      set('warnBadge', 'opacity', f(clamp(P.warn * 2, 0, 1)));
      set('warnBadge', 'transform', `translate(150 ${f(34 + fy0)}) rotate(${f(rm ? 0 : 6 * Math.sin(t * 6) * P.warn)}) scale(${popS(P.warn)})`);

      set('spark1', 'opacity', f(s)); set('spark2', 'opacity', f(s));
      set('spark1', 'transform', `translate(134 ${f(18 - 6 * s)}) scale(${f(.4 + .7 * s)})`);
      set('spark2', 'transform', `translate(150 ${f(40 - 4 * s)}) scale(${f(.3 + .45 * s)})`);

      // thought bubbles pulse in sequence
      for (let i = 0; i < 3; i++) {
        const beat = rm ? 1 : 0.35 + 0.65 * Math.max(0, Math.sin(t * 3 - i * 0.9));
        set('think' + i, 'opacity', f(P.think * beat));
        set('think' + i, 'transform', `translate(${f(P.fx * .5)} ${f(P.floatY + (rm ? 0 : -1.5 * Math.sin(t * 2 + i)))})`);
      }

      // confetti burst on success (fountain from the hat, with gravity + spin)
      const ct = P.confT, cu = ct < 0 || rm ? -1 : (ct - .2) / .55;
      for (let i = 0; i < 6; i++) {
        if (cu < 0 || cu > 1) { set('conf' + i, 'opacity', '0'); continue; }
        const a = (-150 + i * 24) * DEG, sp = 70 + (i % 3) * 14;
        const x = 100 + Math.cos(a) * sp * cu, y = 40 + P.floatY + Math.sin(a) * sp * cu + 90 * cu * cu;
        set('conf' + i, 'opacity', f(1 - smooth(.7, 1, cu)));
        set('conf' + i, 'transform', `translate(${f(x)} ${f(y)}) rotate(${f(cu * 540 * (i % 2 ? 1 : -1))}) scale(1 ${f(.5 + .5 * Math.abs(Math.cos(cu * 12 + i)))})`);
      }

      // boop hearts
      for (let i = 0; i < 3; i++) {
        const hp = pk - i * 0.08;
        if (rm || hp <= 0 || hp >= 1 || t - pokeStart > 1.3) { set('heart' + i, 'opacity', '0'); continue; }
        const x = 100 + (i - 1) * 26 + Math.sin(hp * 7 + i) * 4, y = 64 + P.floatY - hp * 56;
        set('heart' + i, 'opacity', f(1 - smooth(.55, 1, hp)));
        set('heart' + i, 'transform', `translate(${f(x)} ${f(y)}) rotate(${f((i - 1) * 14)}) scale(${f(.6 + 1.1 * Math.sin(Math.min(1, hp * 2) * PI / 2))})`);
      }
    }

    /* ---------- Input ---------- */
    const inBody = (e) => {
      const r = svg.getBoundingClientRect();
      const dx = (e.clientX - (r.left + r.width / 2)) / (r.width * 0.36);
      const dy = (e.clientY - (r.top + r.height * 0.5)) / (r.height * 0.42);
      return dx * dx + dy * dy < 1;
    };
    const onMove = (e) => {
      if (trackPointer) { pointer = { x: e.clientX, y: e.clientY }; lastMove = performance.now(); }
      if (hoverReact) {
        const inside = inBody(e);
        if (inside && !hovered) { popStart = t; popAmp = 1; }
        hovered = inside;
      }
    };
    const onDown = (e) => { if (inBody(e)) api.poke(); };
    const onLeave = (e) => { if (!e.relatedTarget) { pointer = null; hovered = false; } };
    global.addEventListener('pointermove', onMove, { passive: true });
    global.addEventListener('pointerdown', onDown, { passive: true });
    document.addEventListener('pointerout', onLeave);

    /* ---------- Public API ---------- */
    const api = {
      setState(name) {
        if (!STATES.includes(name)) { console.warn('[Oiva] unknown state:', name); return; }
        if (name === state) return;
        const prev = state;
        state = name; stateStart = t; popStart = t; popAmp = 1;
        if (!reduced()) { blinkStart = t; nextBlink = t + 2 + Math.random() * 2; }
        container.dispatchEvent(new CustomEvent('butler:state', { detail: { state, prev } }));
      },
      getState: () => state,
      poke() {
        popStart = t; popAmp = 2.2; pokeStart = t;
        if (!reduced()) jv += (Math.random() < .5 ? -1 : 1) * 90;
        container.dispatchEvent(new CustomEvent('butler:poke'));
      },
      setPalette(name) { if (PALETTES[name] && name !== paletteName) { paletteName = name; build(); } },
      getPalette: () => paletteName,
      lookAt(x, y) { manual = x == null ? null : { x, y }; },
      setTrackPointer(on) { trackPointer = !!on; if (!on) pointer = null; },
      setReducedMotion(v) { rmSetting = v; },
      setFps(v) { fps = Math.max(0, +v || 0); },
      destroy() {
        cancelAnimationFrame(raf);
        global.removeEventListener('pointermove', onMove);
        global.removeEventListener('pointerdown', onDown);
        document.removeEventListener('pointerout', onLeave);
        container.innerHTML = '';
      }
    };
    raf = requestAnimationFrame(frame);
    return api;
  }

  global.Oiva = global.Orava = {
    create, STATES,
    PALETTES: Object.keys(PALETTES),
    paletteInfo: (n) => PALETTES[n] && { label: PALETTES[n].label, color: PALETTES[n].b1, color2: PALETTES[n].b0, color3: PALETTES[n].b2 }
  };
})(window);
