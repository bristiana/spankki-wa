"""Two synthetic Finnish-style bills for testing and filming the bill-photo flow: HTML rendered to PNG by Playwright.

    python scripts/make_demo_bills.py   ->  docs/demo/bills/electricity.png, docs/demo/bills/phone-internet.png

The companies are made up and every bill says DEMO. The IBANs and reference numbers have valid check digits, so the
app's checks accept them; they belong to no one.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "demo" / "bills"
PAYER = ("Aino Virtanen", "Esimerkkikatu 4 B 12", "00100 Helsinki")

BILLS = [
    {"file": "electricity.png", "color": "#e8590c", "logo": "⚡", "biller": "Voltti Energia Oy (demo)",
     "address": ("Sähkötie 1", "00980 Helsinki", "Y-tunnus 0000000-0 (demo)"), "title": "SÄHKÖLASKU",
     "invoice": "2026-104518", "customer": "438 221", "date": date(2026, 10, 2), "due": date(2026, 10, 20),
     "period": "1.9.2026–30.9.2026", "iban": "FI5979900020451187", "bic": "DEMOFIHH", "ref": "20261005837",
     "extra": [("Käyttöpaikka", "Esimerkkikatu 4 B 12, Helsinki"), ("Mittarilukema 30.9.", "18 642 kWh"),
               ("Kulutus", "214 kWh")],
     "lines": [("Energia, 214 kWh × 13,42 c/kWh", 28.72), ("Energian perusmaksu", 4.90),
               ("Sähkönsiirto, 214 kWh × 4,95 c/kWh", 10.59), ("Siirron perusmaksu", 6.90),
               ("Sähkövero, 214 kWh × 2,79 c/kWh", 5.97)]},
    {"file": "phone-internet.png", "color": "#5f3dc4", "logo": "📶", "biller": "Kipinä Mobiili Oy (demo)",
     "address": ("Signaalikuja 7", "02150 Espoo", "Y-tunnus 0000001-9 (demo)"), "title": "LASKU",
     "invoice": "88310472", "customer": "7 015 336", "date": date(2026, 10, 1), "due": date(2026, 10, 14),
     "period": "1.10.2026–31.10.2026", "iban": "FI8340060010998642", "bic": "DEMOFIHH", "ref": "55130091727",
     "extra": [("Liittymä", "+358 40 000 0000 (demo)"), ("Kotinetti", "Esimerkkikatu 4 B 12")],
     "lines": [("Mobiililiittymä 5G 200M, 10/2026", 19.90), ("Kotinetti 300M, 10/2026", 24.90),
               ("Puhelut ulkomaille, 9/2026", 1.85), ("Paperilaskun lisä", 0.00)]},
]


def fi(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def fid(d: date) -> str:
    return f"{d.day}.{d.month}.{d.year}"


def barcode(b: dict, total: float) -> str:
    """Finnish virtual barcode, version 4: IBAN digits, euros, cents, reference, due date."""
    cents = round(total * 100)
    return (f"4{b['iban'][2:]}{cents // 100:06d}{cents % 100:02d}000{int(b['ref']):020d}"
            f"{b['due']:%y%m%d}")


def html(b: dict) -> str:
    total = round(sum(a for _, a in b["lines"]), 2)
    vat = round(total - total / 1.255, 2)
    ref = " ".join([b["ref"][::-1][i:i + 5][::-1] for i in range(0, len(b["ref"]), 5)][::-1])
    iban = " ".join(b["iban"][i:i + 4] for i in range(0, len(b["iban"]), 4))
    rows = "".join(f"<tr><td>{n}</td><td class=r>{fi(a)}</td></tr>" for n, a in b["lines"])
    extra = "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in b["extra"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}} body{{margin:0;background:#fff;font:14px/1.4 Arial,Helvetica,sans-serif;color:#1d1d1d}}
.page{{width:800px;padding:40px 44px 28px;position:relative}}
.demo{{position:absolute;top:18px;right:20px;border:2px solid #c92a2a;color:#c92a2a;font:700 11px Arial;
  letter-spacing:2px;padding:2px 8px;transform:rotate(4deg);opacity:.8}}
header{{display:flex;justify-content:space-between;align-items:flex-start}}
.logo{{display:flex;align-items:center;gap:10px;font:700 22px Arial;color:{b['color']}}}
.logo i{{font-style:normal;width:42px;height:42px;border-radius:10px;background:{b['color']};color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:24px}}
.from{{font-size:12px;color:#555;margin-top:6px}}
h1{{font:700 26px Arial;letter-spacing:1px;margin:0;color:#222;text-align:right}}
.meta{{margin-top:8px;font-size:13px;text-align:right}} .meta div span{{color:#666;margin-right:8px}}
.addr{{margin:34px 0 22px;font-size:15px}}
.info{{display:grid;grid-template-columns:1fr 1fr;gap:4px 24px;background:#f6f6f4;border-radius:8px;padding:12px 16px;
  font-size:13px}} .info span{{color:#666;margin-right:8px}}
table{{width:100%;border-collapse:collapse;margin:22px 0 6px}} th{{text-align:left;font-size:12px;color:#666;
  border-bottom:2px solid #222;padding:6px 0}} td{{padding:7px 0;border-bottom:1px solid #e3e3e3}} .r{{text-align:right}}
.tot td{{border:0;font-weight:700;font-size:17px;padding-top:12px}} .vat td{{border:0;color:#666;font-size:12px;padding:0}}
.slip{{margin-top:30px;border:1.5px solid #222;display:grid;grid-template-columns:1.15fr 1fr;font-size:12px}}
.slip>div{{padding:8px 10px;border-bottom:1px solid #222}} .slip>div:nth-child(odd){{border-right:1px solid #222}}
.slip small{{display:block;color:#555;font-size:10px;text-transform:uppercase;letter-spacing:.3px}}
.slip b{{font-size:14px}} .bar{{grid-column:1/3;border-bottom:0!important;font:12px monospace;letter-spacing:1px}}
footer{{margin-top:10px;font-size:11px;color:#777}}
</style></head><body><div class="page"><div class="demo">DEMO</div>
<header><div><div class="logo"><i>{b['logo']}</i>{b['biller']}</div>
<div class="from">{' · '.join(b['address'])}</div></div>
<div><h1>{b['title']}</h1><div class="meta">
<div><span>Laskun numero</span><b>{b['invoice']}</b></div><div><span>Laskun päiväys</span>{fid(b['date'])}</div>
<div><span>Asiakasnumero</span>{b['customer']}</div><div><span>Eräpäivä</span><b>{fid(b['due'])}</b></div></div></div>
</header>
<div class="addr">{'<br>'.join(PAYER)}</div>
<div class="info"><div><span>Laskutuskausi</span><b>{b['period']}</b></div>{extra}</div>
<table><tr><th>Erittely</th><th class=r>€ (sis. alv)</th></tr>{rows}
<tr class="tot"><td>Maksettava yhteensä</td><td class=r>{fi(total)} €</td></tr>
<tr class="vat"><td>Hinnat sisältävät arvonlisäveron 25,5 %: {fi(vat)} €</td><td></td></tr></table>
<div class="slip">
<div><small>Saajan tilinumero · Mottagarens kontonummer</small><b>{iban}</b> &nbsp; BIC {b['bic']}</div>
<div><small>Saaja · Mottagare</small><b>{b['biller']}</b></div>
<div><small>Maksaja · Betalare</small>{PAYER[0]}<br>{PAYER[1]}, {PAYER[2]}</div>
<div><small>Viitenumero · Ref. nr</small><b>{ref}</b></div>
<div><small>Eräpäivä · Förfallodag</small><b>{fid(b['due'])}</b></div>
<div><small>Euro</small><b>{fi(total)}</b></div>
<div class="bar"><small>Virtuaaliviivakoodi</small>{barcode(b, total)}</div></div>
<footer>Demo invoice made for a hackathon prototype. Not a real company, account or bill.</footer>
</div></body></html>"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception:
            browser = pw.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 800, "height": 600}, device_scale_factor=2)
        for b in BILLS:
            page.set_content(html(b))
            page.locator(".page").screenshot(path=str(OUT / b["file"]))
            print("wrote", OUT / b["file"], "total", fi(sum(a for _, a in b["lines"])))
        browser.close()


if __name__ == "__main__":
    main()
