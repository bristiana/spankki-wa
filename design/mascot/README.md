# Oiva Orava — the vault-keeper squirrel

> **Status (14 Nov, 18:00):** source artwork only, not used by the app yet. The app's coach is still Birky
> (`app/static/birky.svg`, `app/static/birky/`). **The name is not decided:** Oiva Orava, Pähkinä or Eino. The files,
> the speech lines below and the `Oiva` JavaScript name all say Oiva for now. To use a state in the app, copy the
> files it needs into `app/static/`; this folder stays out of the pages, so its 35 MB doesn't go into the deploy.

*Oiva* means "excellent, capable" in Finnish (and is a classic Finnish name); *orava* means "squirrel".

Oiva Orava is a concept mascot for S-Pankki's **Personal Economic Trainer** (Junction 2026). He's a Finnish red squirrel in the S-Pankki vault-keeper uniform: he teaches, cheers your savings, and gently notices spending. It's built as SVG plus one small JavaScript file with no dependencies, so it runs in a web view inside S-mobiili.

## What's in here

| Path | What it is |
|---|---|
| `oiva.js` | The animated character: all 16 states and 3 fur colours. |
| `demo.html` / `oiva-orava-standalone.html` | Interactive demo (the first needs `oiva.js` next to it). |
| `svg/<fur>/<state>.svg` | Every state as a vector still (3 fur colours x 16 states = 48). |
| `png/<fur>/<state>.png` / `@2x.png` | Transparent PNG stills at 512 and 1024 px. |
| `avatar/<fur>/` | Head-only chat avatars for 8 key moods: transparent 512 px, plus circle and rounded-square versions at 512, 192, 96 and 48 px, and SVG. |
| `contact-sheets/` | Every state per fur colour (`red.png`, `winter.png`, `golden.png`) and all chat avatars (`chat-avatars.png`). |

## States and where to use them

| State | App moment | Speech-bubble line (FI / EN) |
|---|---|---|
| `idle` | Home, waiting | "Hei, olen Oiva! Rahasi ovat turvassa holvissa." / Hi, I'm Oiva! Your money is safe in the vault. (Ear twitch, looks around, flicks his tail, nibbles an acorn.) |
| `listening` | User is typing or speaking | "Kerro vain, kuuntelen." / Go ahead, I'm listening. |
| `thinking` | AI is processing | "Hetkinen, mietin…" / One moment… |
| `working` | Crunching transactions | "Lasken kulujasi…" / Crunching your spending… |
| `teaching` | Explaining the three spending baskets | "Katso: kolme rahakoria." / Look: three money baskets. |
| `checking` | Verifying an answer | "Tarkistan vielä kerran." / Double-checking. |
| `needs_approval` | Confirm an action or payment | "Hyväksytkö tämän?" / Do you approve this? |
| `success` | Task done | "Valmista! Hyvin säästetty." / Done! Nicely saved. |
| `error` | Something went wrong | "Hups, korjaan tämän." / Oops, let me fix this. |
| `learned` | User learned something (quiz, lesson) | "Hienoa, opit jotain uutta!" / Nice, you learned something new! |
| `saving` | User saved money | "Pähkinä holviin! Säästit juuri." / Into the vault! You just saved. |
| `spent` | Money went out | "Voi, rahaa lähti. Seurataan yhdessä." / Aw, money went out. Let's keep track together. |
| `safe_to_spend` | Safe-to-spend answer | "Tällä viikolla voit vielä käyttää 45 €." / You can still spend €45 this week. |
| `caution` | Spending would hit must-pay money | "Hetkinen – tämä on vuokrarahaa." / Hold on – this is rent money. |
| `what_if` | What-if simulation ("dining out −20 %") | "Jos syöt ulkona 20 % vähemmän, säästät 540 € vuodessa." |
| `compare` | Benchmark against open data (Statistics Finland) | "Kulutat ruokaan 12 % vähemmän kuin suomalaiset keskimäärin." |

(The amounts are examples for the demo; in the app they come from the user's data.)

## Use it

```html
<div id="oiva" style="width:160px;height:160px"></div>
<script src="oiva.js"></script>
<script>
  const oiva = Oiva.create(document.getElementById('oiva'), { palette: 'red', state: 'idle' });
  oiva.setState('safe_to_spend');   // any state above
  oiva.setPalette('winter');        // red | winter | golden
  oiva.poke();                      // also happens on click

  // head-only chat avatar
  Oiva.create(avatarEl, { framing: 'head', trackPointer: false, hoverReact: false });
</script>
```

The motion follows the OS reduced-motion setting automatically. The stills are the reduced-motion pose of each state.

## Brand notes

The page and props follow the published S-Pankki refresh principles: clarity, friendliness and confidence, rounder shapes, generous white space, neon green used only as a highlighter, and large friendly buttons. The uniform follows the vault-keeper campaign. **Colour values are approximations:** swap in the official tokens (e.g. the exact neon green, defined as `NEON` in `oiva.js`) when you have them. This is an unofficial concept with no logo.

The library is exposed as `Oiva` (`Orava` also works as an alias).
