# Birky WhatsApp templates (English)

WhatsApp lets a business start a conversation more than 24 hours after the person last wrote only with a
pre-approved template. These are the ones Birky's proactive check (`POST /cron/wake`) sends then. The bodies here
must stay identical to `TEMPLATES` in `app/templates_wa.py` (a test checks it).

## How they work

1. The cron (`0 7,12,17 * * *` UTC, about 09, 14 and 19 in Helsinki) calls `/cron/wake`. `app/rules.py` picks the
   most urgent signal per connected person, skipping any `dedup_key` sent in the last 7 days, at most one a day.
2. Inside the 24-hour window the full message goes out as text with quick-reply buttons.
3. Outside it, the signal's template below goes out, and the full signal is held in `data/state.json` (`held`).
   One at a time: a more urgent signal replaces it, an equal or less urgent one waits. Held messages expire after 3 days.
4. When the person taps **Show me** (payload `proactive:show`) or writes anything, the held message is sent first.
   A tap only delivers it; a written message is also answered.
5. Nothing is sent unless `DEMO_PROACTIVE=1`. Without it, the cron does not run and `/cron/wake` is a dry run.

Templates are discreet on purpose: no amounts, no shop names, no goal titles on the lock screen. The only names
shown are a subscription service and the kind of bill.

## Meta rules these follow

- Category **UTILITY**, language `en`: about the person's own account, which they opted into; nothing promotional.
  Meta decides the final category: `kurre_weekly_checkin` and `kurre_goal_pace` may be moved to MARKETING. Note
  what Meta assigns next to each template below.
- Names lowercase with underscores. Variables `{{1}}`, `{{2}}` never at the start or end of the body, never next to
  each other, each with a sample value.
- Variable values (cleaned by `templates_wa.clean`): no new lines or tabs, no links, letters, digits, spaces and
  `& ' -` only, at most 40 characters. When cleaning leaves nothing, no template is sent.
- One quick-reply button, `Show me` (at most 25 characters).

## Templates

| Signal (`app/rules.py`) | Priority | Template | Variables |
|---|---|---|---|
| `UNUSUAL_TRANSACTION` | 1 | `kurre_unusual_payment` | none |
| `RECURRING_CHARGE_UPCOMING` (bill) | 2 | `kurre_bill_due` | bill, when |
| `RECURRING_CHARGE_UPCOMING` (subscription) | 2 | `kurre_subscription_renewal` | service, when |
| `SUBSCRIPTION_PRICE_INCREASE` | 3 | `kurre_subscription_price_up` | service |
| `GOAL_DRIFT` | 4 | `kurre_goal_pace` | none |
| `SALARY_RECEIVED` | 5 | `kurre_salary_in` | none |
| `WEEKLY_CHECK_IN` | 6 | `kurre_weekly_checkin` | none |

### kurre_unusual_payment

> I noticed a payment on your account that's bigger than usual 👀 Want to take a look at it with me?

Button: `Show me` · Category assigned by Meta: _pending_

### kurre_bill_due

> Your {{1}} payment is due {{2}}. Want to see what's safe to spend after it?

- `{{1}}` = the bill's name, lower case, sample `rent`
- `{{2}}` = `today`, `tomorrow` or `on Wed 07 Oct`, sample `tomorrow`

Button: `Show me` · Category assigned by Meta: _pending_

### kurre_subscription_renewal

> Your {{1}} subscription renews {{2}}. Want to check if it's still worth keeping?

- `{{1}}` = the service, sample `StreamPlus`
- `{{2}}` = `today`, `tomorrow` or `on Wed 07 Oct`, sample `tomorrow`

Button: `Show me` · Category assigned by Meta: _pending_

### kurre_subscription_price_up

> Your {{1}} subscription got more expensive. Want to see by how much and if it's still worth it?

- `{{1}}` = the service, sample `StreamPlus`

Button: `Show me` · Category assigned by Meta: _pending_

### kurre_goal_pace

> Your savings goal is a little behind plan. Want to see an easy way to catch up?

Button: `Show me` · Category assigned by Meta: _pending_

### kurre_salary_in

> Your salary just came in 💰 Want to put some aside before it slips through your fingers?

Button: `Show me` · Category assigned by Meta: _pending_

### kurre_weekly_checkin

> Hi 👋 Your weekly money check-in is ready. Want to see how your week went?

Button: `Show me` · Category assigned by Meta: _pending_

## Submitting to Meta

One `POST https://graph.facebook.com/v25.0/<WABA_ID>/message_templates` per template (or WhatsApp Manager >
Message templates). Leave `example` out when the body has no variables.

```json
{
  "name": "kurre_subscription_renewal",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "Your {{1}} subscription renews {{2}}. Want to check if it's still worth keeping?",
      "example": { "body_text": [["StreamPlus", "tomorrow"]] }
    },
    { "type": "BUTTONS", "buttons": [{ "type": "QUICK_REPLY", "text": "Show me" }] }
  ]
}
```

What `whatsapp.send_template` sends:

```json
{
  "messaging_product": "whatsapp",
  "to": "358401234567",
  "type": "template",
  "template": {
    "name": "kurre_subscription_renewal",
    "language": { "code": "en" },
    "components": [
      { "type": "body", "parameters": [{ "type": "text", "text": "StreamPlus" }, { "type": "text", "text": "tomorrow" }] },
      { "type": "button", "sub_type": "quick_reply", "index": "0", "parameters": [{ "type": "payload", "payload": "proactive:show" }] }
    ]
  }
}
```
