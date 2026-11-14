"""Another bank, for real: PSD2 account information through Enable Banking (read only).

S-Pankki stays the main bank (the challenge data). This adds what the customer has elsewhere (Revolut, OP, Nordea...)
from their real accounts: balances and the last 90 days of transactions, shown in get_full_picture.

Who: only the person who opened a link Oiva sent them on WhatsApp ("login with WhatsApp"). The link carries a signed,
expiring token for their phone; opening it sets a cookie. The data is stored per phone, never shown to anyone else.
Ported from Moni (src/eb.js).
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

from . import store
from .engine import eur

log = logging.getLogger("enable_banking")
API = "https://api.enablebanking.com"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "eb"
COOKIE = "oiva_wa"
LINK_DAYS = 30          # how long a WhatsApp login link / cookie is valid
CONSENT_DAYS = 90       # how long the bank consent is asked for
HISTORY_DAYS = 90
MAX_PAGES = 8           # banks page transactions (some return 20 per page)
STATE_TTL = 15 * 60
# The picker: Revolut and the big Finnish banks first, then the rest Enable Banking lists for FI. S-Pankki is the
# main bank here, so it is not "another bank".
FIRST = ["Revolut", "OP", "Nordea", "Danske Bank", "Aktia", "Säästöpankki", "POP Pankki", "Ålandsbanken", "N26", "Wise"]
MAIN_BANK = "S-Pankki"
# Banks looked up in another country than FI: label -> (Enable Banking name, country code).
ABROAD = {"Revolut": ("Revolut", "RO")}  # the linked Revolut accounts are Revolut Romania

_pending: dict[str, tuple[str, str, float]] = {}  # state -> (phone, bank, created)
# Whose real banks this coach turn may use: the WhatsApp number, or the web visitor logged in with WhatsApp.
viewer: contextvars.ContextVar[str | None] = contextvars.ContextVar("eb_viewer", default=None)


def _private_key() -> str | None:
    """EB_PRIVATE_KEY (PEM), or its base64 body in parts EB_KEY_0, EB_KEY_1... (how the worker sends it)."""
    if os.environ.get("EB_PRIVATE_KEY"):
        return os.environ["EB_PRIVATE_KEY"].replace("\\n", "\n")
    parts, i = [], 0
    while os.environ.get(f"EB_KEY_{i}"):
        parts.append(os.environ[f"EB_KEY_{i}"])
        i += 1
    if not parts:
        return None
    body = "".join(parts)
    lines = "\n".join(body[j:j + 64] for j in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{lines}\n-----END PRIVATE KEY-----\n"


def configured() -> bool:
    return bool(os.environ.get("EB_APP_ID") and _private_key())


# ---------- API ----------
def _token() -> str:
    now = int(time.time())
    key = _private_key()
    return jwt.encode({"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
                      key, algorithm="RS256", headers={"kid": os.environ["EB_APP_ID"]})


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    r = httpx.request(method, API + path, json=body, timeout=30,
                      headers={"authorization": f"Bearer {_token()}", "content-type": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"{path} {r.status_code}: {r.text[:300]}")
    return r.json()


_banks_cache: tuple[float, list[str]] | None = None


def banks(country: str = "FI") -> list[str]:
    """Bank names for the picker, Revolut first. Cached for a day."""
    global _banks_cache
    if _banks_cache and time.time() - _banks_cache[0] < 86400:
        return _banks_cache[1]
    names = [a["name"] for a in call(f"/aspsps?country={country}&psu_type=personal")["aspsps"]]
    names = [n for n in names if n != MAIN_BANK]
    ordered = list(ABROAD) + [n for n in FIRST if n in names and n not in ABROAD] + sorted(
        n for n in names if n not in FIRST and n not in ABROAD)
    _banks_cache = (time.time(), ordered)
    return ordered


def start(phone: str, bank: str, redirect_url: str, country: str = "FI") -> str:
    """The bank's login URL. The random state ties the callback to this phone (one use, 15 minutes)."""
    now = time.time()
    for s, (_, _, t) in list(_pending.items()):
        if now - t > STATE_TTL:
            _pending.pop(s, None)
    state = secrets.token_urlsafe(24)
    _pending[state] = (phone, bank, now)
    name, country = ABROAD.get(bank, (bank, country))
    valid = (datetime.now(timezone.utc) + timedelta(days=CONSENT_DAYS)).isoformat(timespec="seconds")
    return call("/auth", "POST", {"access": {"valid_until": valid}, "aspsp": {"name": name, "country": country},
                                  "state": state, "redirect_url": redirect_url, "psu_type": "personal"})["url"]


def pending(state: str) -> tuple[str, str] | None:
    p = _pending.pop(state or "", None)
    if not p or time.time() - p[2] > STATE_TTL:
        return None
    return p[0], p[1]


def _transactions(uid: str, days: int = HISTORY_DAYS) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    out, key = [], None
    for _ in range(MAX_PAGES):
        q = f"/accounts/{uid}/transactions?date_from={since}" + (f"&continuation_key={key}" if key else "")
        page = call(q)
        out += page.get("transactions") or []
        key = page.get("continuation_key")
        if not key:
            break
    return out


def finish(phone: str, bank: str, code: str) -> dict:
    """Code from the callback -> session -> balances and transactions of every shared account -> saved for phone."""
    session = call("/sessions", "POST", {"code": code})
    accounts = []
    for a in session.get("accounts") or []:
        uid = a["uid"] if isinstance(a, dict) else a
        acc = {"uid": uid, "product": (a.get("product") or a.get("name")) if isinstance(a, dict) else None,
               "kind": a.get("cash_account_type") if isinstance(a, dict) else None,
               "currency": a.get("currency") if isinstance(a, dict) else None,
               "iban_end": ((a.get("account_id") or {}).get("iban") or "")[-4:] if isinstance(a, dict) else ""}
        accounts.append(_pull(acc))
    link = {"bank": bank, "session_id": session.get("session_id"), "linked": date.today().isoformat(),
            "valid_until": (session.get("access") or {}).get("valid_until"), "accounts": accounts}
    data = load(phone)
    data[bank] = link
    save(phone, data)
    return link


_fx_cache: tuple[float, dict[str, float]] | None = None


def fx() -> dict[str, float]:
    """Units of each currency per 1 EUR (ECB reference rates via Frankfurter), cached for 12 hours."""
    global _fx_cache
    if _fx_cache and time.time() - _fx_cache[0] < 43200:
        return _fx_cache[1]
    try:
        rates = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": "EUR"}, timeout=10,
                          follow_redirects=True).json()["rates"]
    except Exception as e:  # no rates: only EUR amounts are counted
        log.warning("fx rates: %s", e)
        rates = {}
    rates["EUR"] = 1.0
    _fx_cache = (time.time(), rates)
    return rates


def to_eur(amount: float, currency: str | None) -> float | None:
    rate = fx().get(currency or "EUR")
    return eur(amount / rate) if rate else None


def _pull(acc: dict) -> dict:
    """Balance and the last 90 days of one account, fresh from the bank."""
    acc.pop("error", None)
    try:
        acc["balance_eur"] = pick_balance(call(f"/accounts/{acc['uid']}/balances").get("balances") or [])
        acc["transactions"] = [t for t in (simplify(x) for x in _transactions(acc["uid"])) if t]
    except Exception as e:  # one account failing should not lose the others
        log.warning("account %s: %s", acc["uid"][-6:], e)
        acc["error"] = str(e)[:200]
    return acc


def refresh(phone: str) -> int:
    """Pull every linked account again (same consent, no new login). Returns how many accounts were read."""
    data = load(phone)
    n = 0
    for link in data.values():
        for acc in link.get("accounts", []):
            _pull(acc)
            n += "error" not in acc
    save(phone, data)
    return n


def pick_balance(balances: list[dict]) -> float | None:
    """The available balance when the bank gives it, else the booked one, in EUR (RON, USD... converted)."""
    order = ["ITAV", "CLAV", "XPCD", "ITBD", "CLBD", "OPBD"]
    bal = [b for b in balances if (b.get("balance_amount") or {}).get("amount") is not None]
    bal.sort(key=lambda b: order.index(b.get("balance_type")) if b.get("balance_type") in order else 99)
    if not bal:
        return None
    a = bal[0]["balance_amount"]
    return to_eur(float(a["amount"]), a.get("currency"))


# Category from the merchant code, else from the name; the same names the S-Pankki data uses.
MCC_CATEGORY = {"4111": "Transportation", "4121": "Transportation", "5541": "Transportation", "5542": "Transportation",
                "7523": "Transportation", "4511": "Travel", "4722": "Travel", "7011": "Travel",
                "5411": "Groceries", "5422": "Groceries", "5451": "Groceries", "5499": "Groceries",
                "5812": "Restaurants", "5813": "Restaurants", "5814": "Restaurants",
                "5912": "Health", "8011": "Health", "8021": "Health", "8099": "Health",
                "5200": "Home & Tech", "5712": "Home & Tech", "5732": "Home & Tech",
                "7832": "Entertainment", "7922": "Entertainment", "7991": "Entertainment", "7996": "Entertainment",
                "7999": "Entertainment", "5815": "Entertainment", "5816": "Entertainment", "4899": "Entertainment"}
NAME_CATEGORY = [
    (r"prisma|s-market|sale\b|alepa|k-market|k-supermarket|k-citymarket|lidl|kaufland|carrefour|mega image|"
     r"profi|oiva|auchan|tesco|rewe|aldi", "Groceries"),
    (r"bolt|uber|hsl|vr\b|taxi|neste|st1|abc\b|teboil|parking|parkki", "Transportation"),
    (r"wolt|foodora|mcdonald|burger|hesburger|subway|starbucks|caf[eé]|ravintola|restaurant|pizza|kebab|bar\b|pub\b",
     "Restaurants"),
    (r"netflix|spotify|youtube|disney|hbo|steam|playstation|xbox|finnkino|cinema|apple\.com|google", "Entertainment"),
    (r"ryanair|finnair|wizz|airbnb|booking\.com|hotel|norwegian", "Travel"),
    (r"apteekki|pharmacy|farmacia|terveystalo|mehil[aä]inen|dentist|hammas", "Health"),
    (r"ikea|gigantti|verkkokauppa|power\b|clas ohlson|emag|altex", "Home & Tech"),
    (r"zalando|h&m|zara|amazon|aliexpress|temu|shein", "Lifestyle"),
]


def category(t: dict) -> str:
    if t["amount_eur"] > 0:
        return "Income"
    if t.get("mcc") in MCC_CATEGORY:
        return MCC_CATEGORY[t["mcc"]]
    name = (t.get("what") or "").lower()
    for rx, cat in NAME_CATEGORY:
        if re.search(rx, name):
            return cat
    # No merchant code and no shop we know: money sent to a person or an account (rent to a landlord, a friend, a
    # top-up elsewhere). Card payments always carry a merchant code.
    return "Other" if t.get("mcc") else TRANSFERS


TRANSFERS = "Transfers"


def simplify(x: dict) -> dict | None:
    """An Enable Banking transaction -> {date, what, amount_eur, mcc, category}. Booked only; RON, USD... converted
    to EUR (the original amount is kept)."""
    amt = x.get("transaction_amount") or {}
    if (x.get("status") or "BOOK") != "BOOK" or amt.get("amount") is None:
        return None
    cur = amt.get("currency") or "EUR"
    if to_eur(1.0, cur) is None:
        return None
    sign = -1 if x.get("credit_debit_indicator") == "DBIT" else 1
    other = x.get("creditor") if sign < 0 else x.get("debtor")
    what = (other or {}).get("name") or " ".join(x.get("remittance_information") or [])[:60] or "Unknown"
    t = {"date": x.get("booking_date") or x.get("value_date") or x.get("transaction_date"), "what": what.strip(),
         "amount_eur": to_eur(sign * abs(float(amt["amount"])), cur), "mcc": x.get("merchant_category_code")}
    if cur != "EUR":
        t["original"] = f"{sign * abs(float(amt['amount'])):.2f} {cur}"
    t["category"] = category(t)
    return t if t["date"] else None


# ---------- storage, per phone ----------
def _name(phone: str) -> str:
    return "eb_" + hashlib.sha256(phone.encode()).hexdigest()[:16] + ".json"


def load(phone: str) -> dict:
    p = DATA_DIR / _name(phone)
    text = p.read_text() if p.exists() else store.load(_name(phone))
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def save(phone: str, data: dict) -> None:
    text = json.dumps(data, default=str)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / _name(phone)).write_text(text)
    store.save(_name(phone), text)


def mask(phone: str | None) -> str:
    return "•••" + phone[-4:] if phone else ""


def unlink(phone: str) -> None:
    save(phone, {})


# ---------- login with WhatsApp: a signed link, then a cookie ----------
def _secret() -> bytes:
    s = os.environ.get("EB_LINK_SECRET") or os.environ.get("WA_APP_SECRET") or os.environ.get("WA_VERIFY_TOKEN")
    if not s:
        raise RuntimeError("no secret to sign WhatsApp links")
    return s.encode()


def sign(phone: str, days: int = LINK_DAYS) -> str:
    body = base64.urlsafe_b64encode(f"{phone}|{int(time.time()) + days * 86400}".encode()).decode().rstrip("=")
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def verify(token: str | None) -> str | None:
    """The phone in a valid, unexpired token, else None."""
    try:
        body, mac = (token or "").rsplit(".", 1)
        if not hmac.compare_digest(mac, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]):
            return None
        phone, exp = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().split("|")
        return phone if int(exp) > time.time() else None
    except (ValueError, RuntimeError):
        return None


# ---------- what Oiva sees ----------
def _drop_internal(tx: list[dict]) -> list[dict]:
    """Money moved between the person's own accounts (out of one, the same amount into another within a day) is not
    spending or income: leave both sides out."""
    ins = [i for i, t in enumerate(tx) if t["amount_eur"] > 0]
    drop = set()
    for i, t in enumerate(tx):
        if t["amount_eur"] >= 0:
            continue
        for j in ins:
            # same amount, or within 3% when it was exchanged between currencies
            if j in drop or tx[j]["_acc"] == t["_acc"] or abs(tx[j]["amount_eur"] + t["amount_eur"]) > 0.03 * -t["amount_eur"]:
                continue
            if abs((date.fromisoformat(tx[j]["date"]) - date.fromisoformat(t["date"])).days) <= 1:
                drop.update((i, j))
                break
    return [t for k, t in enumerate(tx) if k not in drop]


def picture(phone: str | None) -> list[dict]:
    """The person's real linked banks, summarised for the coach: balances, spending per month by category, recent."""
    if not phone:
        return []
    out = []
    for link in load(phone).values():
        accs, tx, seen = [], [], set()
        for n, a in enumerate(link.get("accounts", [])):
            # The same account can come back several times (one per earlier consent): count it once.
            key = (a.get("iban_end"), a.get("currency"), a.get("product")) if a.get("iban_end") else ("uid", a.get("uid"))
            if key in seen:
                continue
            seen.add(key)
            # Named by currency: some banks put the holder's name in "product", which is not an account type.
            accs.append({"type": f"{a['currency']} account" if a.get("currency") else "account",
                         "ending": a.get("iban_end") or None, "balance_eur": a.get("balance_eur")})
            tx += [{**t, "_acc": n} for t in a.get("transactions") or []]
        tx = [{**t, "category": category(t)} for t in _drop_internal(tx)]  # categories as of today's rules
        months = max(1.0, HISTORY_DAYS / 30.4)
        by_cat: dict[str, float] = {}
        sent = 0.0
        for t in tx:
            if t["amount_eur"] >= 0:
                continue
            if t["category"] == TRANSFERS:
                sent -= t["amount_eur"]
            else:
                by_cat[t["category"]] = by_cat.get(t["category"], 0) - t["amount_eur"]
        received = sum(t["amount_eur"] for t in tx if t["amount_eur"] > 0)
        out.append({
            "bank": f"{link['bank']} (linked via Enable Banking, real data)", "linked_since": link.get("linked"),
            "accounts": accs, "real": True,
            "spending_per_month_eur": eur(sum(by_cat.values()) / months),
            "transfers_out_per_month_eur": eur(sent / months),
            "money_in_per_month_eur": eur(received / months),
            "how_to_read": "spending = card payments and shops; transfers_out = money sent to people or other "
                           "accounts (not spending on things); transfers between the customer's own accounts are "
                           "left out entirely",
            "spending_per_month_by_category_eur": {k: eur(v / months) for k, v in
                                                   sorted(by_cat.items(), key=lambda kv: -kv[1])},
            "recent_transactions": [{k: v for k, v in t.items() if k != "_acc"}
                                    for t in sorted(tx, key=lambda t: t["date"], reverse=True)[:8]],
            "consent_valid_until": link.get("valid_until"),
        })
    return out


def merge(full: dict, phone: str | None) -> dict:
    """Engine.full_picture() plus the person's real banks. Someone with a real bank sees S-Pankki and their real
    banks only: the synthetic Demo Bank (shared by everyone on the demo customer) is left out, with its savings and
    card debt."""
    real = picture(phone)
    if not real:
        return full
    s_pankki = full["banks"][0]
    own = sum(a.get("balance_eur") or 0 for a in s_pankki["accounts"])
    extra = sum(a["balance_eur"] for b in real for a in b["accounts"] if isinstance(a.get("balance_eur"), (int, float)))
    out = {"linked": True, "banks": [s_pankki] + real, "total_balances_eur": eur(own + extra),
           "net_position_eur": eur(own + extra),
           "note": "S-Pankki is the sample customer's data. Banks marked 'real data' are the customer's own "
                   "accounts, read through Enable Banking (PSD2, read only), last 90 days. A negative balance there "
                   "is money owed."}
    return out
