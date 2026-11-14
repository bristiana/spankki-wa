"""Official benchmark: monthly household consumption by age of the reference person (Statistics Finland,
Household Budget Survey 2022, table ktutk/14q4). Writes data/statfin_benchmark.json.

  python scripts/fetch_statfin.py
"""
import json
from pathlib import Path

import httpx

URL = "https://pxdata.stat.fi/PxWeb/api/v1/en/StatFin/ktutk/14q4.px"
OUT = Path(__file__).resolve().parents[1] / "data" / "statfin_benchmark.json"
# our categories -> COICOP 2018 codes in the table
MAP = {"Restaurants": "111", "Groceries": "01", "Housing": "041", "Utilities": "045", "Transportation": "07",
       "Health": "06", "Entertainment": "09", "Lifestyle": "03", "Home & Tech": "05", "Total": "0"}
AGES = ["17-24", "25-34", "SSS"]


def main():
    q = {"query": [
        {"code": "timeperiod_y", "selection": {"filter": "item", "values": ["2022"]}},
        {"code": "coicop_46_20231201", "selection": {"filter": "item", "values": list(MAP.values())}},
        {"code": "ikaryhma_10_20180101", "selection": {"filter": "item", "values": AGES}},
        {"code": "contentscode", "selection": {"filter": "item", "values": ["kulukk_kt_hk_1_1"]}}],
        "response": {"format": "json-stat2"}}
    d = httpx.post(URL, json=q, timeout=30).json()
    ci = d["dimension"]["coicop_46_20231201"]["category"]["index"]
    ai = d["dimension"]["ikaryhma_10_20180101"]["category"]["index"]
    n_age = len(ai)
    out = {"source": "Statistics Finland, Household Budget Survey 2022, table 14q4 "
                     "(household consumption expenditure per month, EUR, current prices)",
           "url": "https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__ktutk/statfin_ktutk_pxt_14q4.px/",
           "year": 2022, "by_age": {}}
    for age in AGES:
        key = "all" if age == "SSS" else age
        out["by_age"][key] = {cat: d["value"][ci[code] * n_age + ai[age]] for cat, code in MAP.items()}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["by_age"], indent=1))


if __name__ == "__main__":
    main()
