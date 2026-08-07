"""
fetch-nasdaq-symbols.py
========================
الوظيفة: جلب قائمة كل رموز وأسماء الشركات المُدرجة حالياً بـ NASDAQ،
مباشرة من الـ API الرسمي غير الموثّق لـ nasdaq.com.
"""

import requests
import json
import time
from datetime import datetime

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
}

OUTPUT_FILE = "symbols-reference.json"


def fetch_nasdaq_symbols():
    params = {
        "tableonly": "true",
        "limit": "10000",
        "offset": "0",
        "download": "true",
        "exchange": "nasdaq",
    }

    print(f"[{datetime.now().isoformat()}] جاري الاتصال بـ NASDAQ API...")

    response = requests.get(
        NASDAQ_SCREENER_URL,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"فشل الاتصال بـ NASDAQ API. "
            f"رمز الحالة: {response.status_code} — النص: {response.text[:500]}"
        )

    data = response.json()

    if "data" not in data or "rows" not in data["data"]:
        raise RuntimeError("بنية الاستجابة غير متوقعة.")

    rows = data["data"]["rows"]
    print(f"[{datetime.now().isoformat()}] تم جلب {len(rows)} سهماً بنجاح.")

    return rows


def normalize_symbol_data(raw_rows):
    normalized = []

    for row in raw_rows:
        try:
            symbol = row.get("symbol", "").strip()
            if not symbol:
                continue

            last_sale_raw = row.get("lastsale", "0").replace("$", "").strip()
            last_sale = float(last_sale_raw) if last_sale_raw else None

            pct_change_raw = row.get("pctchange", "0%").replace("%", "").strip()
            pct_change = float(pct_change_raw) if pct_change_raw else None

            market_cap_raw = row.get("marketCap", "0")
            market_cap = float(market_cap_raw) if market_cap_raw else 0.0

            volume_raw = row.get("volume", "0")
            volume = int(volume_raw) if volume_raw else 0

            normalized.append({
                "symbol": symbol,
                "name": row.get("name", "").strip(),
                "last_sale": last_sale,
                "net_change": row.get("netchange"),
                "change_percent": pct_change,
                "market_cap": market_cap,
                "country": row.get("country", "").strip(),
                "ipo_year": row.get("ipoyear"),
                "volume": volume,
                "sector": row.get("sector", "").strip() or None,
                "industry": row.get("industry", "").strip() or None,
            })

        except (ValueError, AttributeError) as e:
            print(f"تحذير: تجاهل صف بسبب خطأ بالتنسيق — {row.get('symbol', '?')}: {e}")
            continue

    return normalized


def save_to_file(data, filename):
    output = {
        "fetched_at": datetime.now().isoformat(),
        "total_symbols": len(data),
        "symbols": data,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().isoformat()}] تم حفظ {len(data)} سهماً بملف {filename}")


def main():
    try:
        raw_rows = fetch_nasdaq_symbols()
        normalized_data = normalize_symbol_data(raw_rows)

        if len(normalized_data) < 1000:
            raise RuntimeError(
                f"تحذير أمان: عدد الأسهم المُستلمة ({len(normalized_data)}) أقل بكثير من المتوقع."
            )

        save_to_file(normalized_data, OUTPUT_FILE)
        print("اكتمل الجلب بنجاح.")

    except Exception as e:
        print(f"خطأ فادح أثناء جلب بيانات NASDAQ: {e}")
        raise


if __name__ == "__main__":
    main()
