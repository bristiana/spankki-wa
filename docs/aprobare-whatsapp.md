# Aprobare WhatsApp: checklist

Cum am pus Birky pe WhatsApp Cloud API (Meta) și ce mai e de făcut pentru producție.
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
- [x] **Utilizator de sistem** în Business Settings → Utilizatori de sistem: **Birky-bot**, rol **Admin**.
- [x] Active atribuite lui Birky-bot, cu **Acces deplin**: aplicația „S-Pankki Junction” și contul WhatsApp „Test Messaging Account”.
- [x] „Generează un cod”: aplicația S-Pankki Junction, permisiuni `whatsapp_business_messaging` + `whatsapp_business_management`.
- [x] Verificat cu `debug_token`: `type: SYSTEM_USER`, valid până pe **13 ian 2027**.
- [x] Pus în `.env` și în Cloudflare (`wrangler secret bulk`), container repornit (`/admin/restart`).
- [ ] Înainte de 13 ian 2027: generează un cod nou pentru Birky-bot (același loc) și repetă pasul de mai sus.

## 4. Template-uri (mesaje după fereastra de 24h)
WhatsApp permite mesaje libere doar în 24h de la ultimul mesaj al omului. După aceea, doar template-uri aprobate. Textele sunt în `docs/whatsapp-templates.md` și `app/templates_wa.py`.
- [x] Toate 7 trimise la Meta pe 14 nov (`POST /<WABA_ID>/message_templates`), categoria cerută UTILITY.
- [ ] Aprobare Meta. Starea pe 14 nov: toate **PENDING**. Meta a mutat 6 la MARKETING; doar `kurre_bill_due` a rămas UTILITY.

| Template | Categorie Meta | Stare |
|---|---|---|
| `kurre_bill_due` | UTILITY | PENDING |
| `kurre_subscription_renewal` | MARKETING | PENDING |
| `kurre_subscription_price_up` | MARKETING | PENDING |
| `kurre_unusual_payment` | MARKETING | PENDING |
| `kurre_goal_pace` | MARKETING | PENDING |
| `kurre_salary_in` | MARKETING | PENDING |
| `kurre_weekly_checkin` | MARKETING | PENDING |

Numele `kurre_*` rămân cum au fost trimise (le vede doar Meta); textele lor nu pomenesc numele coach-ului.

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
- [ ] Template-uri aprobate în categoriile finale; opt-in documentat.
- [ ] Politica de confidențialitate și termenii pe un URL public, în setările aplicației.
- [ ] App Review / modul Live pentru aplicație.
- [ ] Autentificare reală în S-mobiili (SCA) în locul PIN-ului demo.
