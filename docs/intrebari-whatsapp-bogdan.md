# Întrebări WhatsApp (de la Bogdan): cum merge pe numărul de test din „Criss”

Răspunsurile vin din starea reală a contului, citită prin Graph API pe 14 nov 2026 (`health_status` pe contul WhatsApp „Test Messaging Account”, portofoliul „Criss”). Fișierul nu conține secrete.

## Pe scurt
Pe contul nostru, **mesajele inițiate de firmă sunt BLOCATE** și noi nu am rezolvat nimic din ce cere Meta pentru ele. Oiva merge pentru că folosim **doar fereastra de 24h**: omul scrie primul, iar în următoarele 24h botul poate răspunde liber (text, butoane, linkuri), fără template-uri și fără plată. Dacă la tine „Meta blochează trimiterea”, verifică întâi dacă trimiți primul (sau un template). Dacă da, acolo e blocajul.

## Răspunsuri

**1. Ai completat în Criss la „Informații despre afacere” Legal Name, Country și Website? Ce ai pus la Website?**
Nu. Meta raportează la noi: *„Your business profile is incomplete. The following fields are required: Legal Name, Country, and Website.”* (cod 131000). Nu am pus niciun website. Lipsa lor blochează doar mesajele inițiate de firmă, nu răspunsurile în fereastra de 24h.

**2. Ai pus o metodă de plată pe contul WhatsApp?**
Nu. Contul are „No payment methods on file”, iar Meta raportează eroarea 141006: *„There is an error with the payment method. This will block business initiated conversations.”* Răspunsurile în fereastra de 24h sunt gratuite, așa că nu am avut nevoie de card.

**3. Ai început business verification sau ai mers fără?**
Am mers fără. Status: `business_verification_status: not_verified`, eroarea 141010 *„The Business has not passed business verification.”* Pe numărul de test nu e nevoie de ea pentru răspunsuri.

**4. Ți-a cerut Meta vreo confirmare de cont („unusual activity”, confirm account) pe parcurs?**
Nu ne-a cerut o confirmare de cont. Am avut însă o dată, pe 14 nov pe la 17:15, eroarea **„API access blocked”** (cod 200, subcod 5258005): botul nu mai putea trimite. S-a deblocat singur pe la 17:27, fără să schimbăm nimic.

## Ce am făcut ca să meargă
1. Aplicație pe developers.facebook.com, use case **Connect with customers through WhatsApp**, legată de portofoliul „Criss”.
2. Numărul de test dat de Meta (+1 555 179 7943). Numărul nostru de telefon e adăugat ca destinatar de test (maxim 5).
3. **Token permanent de utilizator de sistem** (cel temporar din API Setup expiră în câteva ore):
   - în Business Settings → Utilizatori de sistem, creezi un utilizator de sistem cu rol Admin;
   - îi atribui, cu Acces deplin, aplicația și contul WhatsApp (fără asta zice „no permissions”);
   - la „Generează un cod” bifezi doar `whatsapp_business_messaging` și `whatsapp_business_management`.
4. Webhook setat pe URL-ul nostru, cu câmpul `messages` abonat și semnătura `X-Hub-Signature-256` verificată.
5. **Fără template-uri.** Le-am șters pe toate. Botul răspunde doar după ce omul scrie primul, în 24h.

## Dacă tu vrei ca botul să scrie primul
Atunci trebuie tot ce lipsește la noi:
- Legal Name, Country și Website în Business Info;
- o metodă de plată pe contul WhatsApp;
- business verification;
- template-uri aprobate.

Pentru un demo la hackathon e mai simplu să scrii tu primul „hi” și apoi să lucrezi în fereastra de 24h.

## Verifică singur starea contului tău
```
GET https://graph.facebook.com/v25.0/<WABA_ID>?fields=health_status&access_token=<TOKEN>
```
Răspunsul spune exact ce e blocat (WABA, BUSINESS, APP) și de ce, cu soluția propusă.
