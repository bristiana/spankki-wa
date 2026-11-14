# Aprobare WhatsApp: checklist

Cum am pus Oiva pe WhatsApp Cloud API (Meta) și ce mai e de făcut pentru producție.
Niciun secret nu stă în acest fișier: token-urile și cheile sunt doar în `.env` (local, ignorat de git) și în secretele Cloudflare.

## 1. Aplicația Meta
- [x] Aplicație creată pe developers.facebook.com, use case **Connect with customers through WhatsApp** („S-Pankki Junction”).
- [x] Portofoliu de afaceri asociat („Criss”).
- [x] Număr de test WhatsApp de la Meta (+1 555 179 7943), cont „Test Messaging Account”.
- [x] Numărul nostru adăugat ca destinatar de test (maxim 5 numere pe un număr de test).

## 2. Webhook
- [x] URL: `https://spankki-wa.bogdan-c-2206.workers.dev/webhook`, setat prin Graph API.
- [x] `WA_VERIFY_TOKEN` pentru handshake (`GET /webhook`).
- [x] Câmpul `messages` abonat.
- [x] Semnătura `X-Hub-Signature-256` verificată cu `WA_APP_SECRET`: cererile fără semnătură corectă primesc 403.

## 3. Token de acces (de ce „tot expira”)
- [x] ~~Token temporar din API Setup („Generate access token”)~~: ține doar câteva ore. Primul a expirat pe 14 nov la ~05:00, al doilea la 15:00.
- [x] **Utilizator de sistem** în Business Settings → Utilizatori de sistem: **Oiva-bot**, rol **Admin**.
- [x] Active atribuite lui Oiva-bot, cu **Acces deplin**: aplicația „S-Pankki Junction” și contul WhatsApp „Test Messaging Account”.
- [x] „Generează un cod”: aplicația S-Pankki Junction, permisiuni `whatsapp_business_messaging` + `whatsapp_business_management`.
- [x] Verificat cu `debug_token`: `type: SYSTEM_USER`, valid până pe **13 ian 2027**.
- [x] Pus în `.env` și în Cloudflare (`wrangler secret bulk`), container repornit (`/admin/restart`).
- [ ] Înainte de 13 ian 2027: generează un cod nou pentru Oiva-bot (același loc) și repetă pasul de mai sus.

## 4. Template-uri: renunțat (14 nov)
Decizie: **fără template-uri**. Pe WhatsApp, în 24h de la ultimul mesaj al omului, Oiva poate trimite orice mesaj liber (text, butoane, linkuri), fără aprobare. După 24h de tăcere, Oiva nu scrie primul: așteaptă să-i scrie omul.
- [x] Cele 7 template-uri trimise inițial la Meta au fost **șterse** (`DELETE /<WABA_ID>/message_templates?name=…`).
- [x] Codul de template-uri scos din aplicație (`templates_wa.py`, `send_template`, mesajele „ținute”).
- [x] Pentru demo: scrii „hi” botului, apoi din panoul demo pornești reminderul, alertele etc., toate în fereastra de 24h.
- [x] Fereastra de 24h rezistă la redeploy (ora ultimului mesaj e salvată în starea persistentă).

## 5. Mesaje proactive
- [x] Cron pe Worker la 07, 12 și 17 UTC (09, 14, 19 în Helsinki iarna) → `POST /cron/wake`.
- [x] Siguranță: nu pleacă nimic fără secretul `DEMO_PROACTIVE=1`. Acum e **oprit**.
- [ ] Pentru a porni: `wrangler secret put DEMO_PROACTIVE` (valoare `1`), apoi redeploy cu `INSTANCE` nou.

## 6. Conexiunea în demo
- [x] Numărul demo (`DEMO_PHONE`) rămâne conectat după redeploy sau „Reset demo”; doar STOP îl deconectează.
- [x] Orice alt număr trece prin „Connect S-Pankki” (consimțământ explicit) înainte să primească date.

## 7. Pentru producție (nu e în demo)
- [ ] Număr WhatsApp Business propriu, verificat (nu numărul de test), cu nume afișat aprobat.
- [ ] Business verification în Meta Business Settings.
- [ ] Doar dacă Oiva trebuie să scrie primul după 24h: template-uri aprobate de Meta; opt-in documentat.
- [ ] Politica de confidențialitate și termenii pe un URL public, în setările aplicației.
- [ ] App Review / modul Live pentru aplicație.
- [ ] Autentificare reală în S-mobiili (SCA) în locul PIN-ului demo.
