"""
scripts/fetch-nasdaq-symbols.py

الوظيفة: جلب قائمة كل رموز وأسماء الشركات المُدرجة حالياً في بورصتي NASDAQ و NYSE،
مباشرة من الـ API الرسمي لـ nasdaq.com وتوحيدها في ملف symbols-reference.json.
"""

import requests
import json
import time
from datetime import datetime

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"

# هيدرز لمحاكاة متصفح حقيقي لتجنب الحظر (HTTP 403)
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}

OUTPUT_FILE = "symbols-reference.json"

# البورصات المستهدفة (NASDAQ + NYSE)
TARGET_EXCHANGES = ["nasdaq", "nyse"]


def fetch_symbols_for_exchange(exchange: str) -> list:
    """جلب بيانات الأسهم لبورصة محددة من الـ API الرسمي لـ NASDAQ"""
    params = {
        "tableonly": "true",
        "limit": "10000",
        "offset": "0",
        "download": "true",
        "exchange": exchange,
    }

    print(f"[{datetime.now().isoformat()}] جاري الاتصال بـ API لجلب بورصة: {exchange.upper()}...")

    try:
        response = requests.get(
            NASDAQ_SCREENER_URL,
            headers=HEADERS,
            params=params,
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"خطأ في الاتصال بـ NASDAQ API لبورصة {exchange.upper()}: {e}")

    if response.status_code != 200:
        raise RuntimeError(
            f"فشل جلب بورصة {exchange.upper()} — رمز الحالة: {response.status_code} "
            f"النص: {response.text[:300]}"
        )

    data = response.json()

    if "data" not in data or "rows" not in data["data"]:
        raise RuntimeError(f"بنية الاستجابة غير متوقعة عند جلب بورصة {exchange.upper()}.")

    rows = data["data"]["rows"]
    print(f"[{datetime.now().isoformat()}] نجاح: تم جلب {len(rows)} سهماً من بورصة {exchange.upper()}.")
    return rows


def normalize_symbol_data(raw_rows: list) -> list:
    """تنظيف وتنسيق البيانات وتحويل القيم العددية لمعالجة الشوائب"""
    normalized = []

    for row in raw_rows:
        try:
            symbol = str(row.get("symbol", "")).strip()
            if not symbol:
                continue

            # تنظيف السعر الأخير (إزالة علامة $ والفاصلات)
            last_sale_raw = str(row.get("lastsale", "0")).replace("$", "").replace(",", "").strip()
            last_sale = float(last_sale_raw) if last_sale_raw and last_sale_raw != "N/A" else None

            # تنظيف نسبة التغير (إزالة علامة %)
            pct_change_raw = str(row.get("pctchange", "0%")).replace("%", "").replace(",", "").strip()
            pct_change = float(pct_change_raw) if pct_change_raw and pct_change_raw != "N/A" else None

            # القيمة السوقية
            market_cap_raw = str(row.get("marketCap", "0")).replace(",", "").strip()
            market_cap = float(market_cap_raw) if market_cap_raw and market_cap_raw != "N/A" else 0.0

            # حجم التداول
            volume_raw = str(row.get("volume", "0")).replace(",", "").strip()
            volume = int(volume_raw) if volume_raw and volume_raw != "N/A" else 0

            # القطاع والصناعة
            sector = str(row.get("sector", "")).strip() or None
            industry = str(row.get("industry", "")).strip() or None

            normalized.append({
                "symbol": symbol,
                "name": str(row.get("name", "")).strip(),
                "last_sale": last_sale,
                "net_change": row.get("netchange"),
                "change_percent": pct_change,
                "market_cap": market_cap,
                "country": str(row.get("country", "")).strip(),
                "ipo_year": row.get("ipoyear"),
                "volume": volume,
                "sector": sector,
                "industry": industry,
            })

        except (ValueError, AttributeError) as e:
            continue

    return normalized


def save_to_file(data: list, filename: str):
    """حفظ البيانات الموحدة بنفس البنية المعتمدة للمستودع"""
    output = {
        "fetched_at": datetime.now().isoformat(),
        "total_symbols": len(data),
        "symbols": data,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().isoformat()}] تم حفظ {len(data)} سهماً (NASDAQ + NYSE) بملف {filename}")


def main():
    try:
        all_raw_rows = []

        # 1. جلب بيانات البورصتين
        for exchange in TARGET_EXCHANGES:
            rows = fetch_symbols_for_exchange(exchange)
            all_raw_rows.extend(rows)
            time.sleep(1)  # مهلة بين الطلبات لمنع الحظر

        # 2. إزالة التكرار بناءً على الرمز (Symbol)
        unique_rows_dict = {}
        for row in all_raw_rows:
            sym = row.get("symbol")
            if sym and sym not in unique_rows_dict:
                unique_rows_dict[sym] = row

        unique_raw_rows = list(unique_rows_dict.values())

        # 3. تنظيف وتوحيد الهيكل
        normalized_data = normalize_symbol_data(unique_raw_rows)

        # 4. فحص الأمان: البورصتان معاً يجب ألا تقلا عن 3,500 سهم
        if len(normalized_data) < 3500:
            raise RuntimeError(
                f"تحذير أمان: عدد الأسهم المُستلمة ({len(normalized_data)}) أقل بكثير من المتوقع للسوقين."
            )

        # 5. حفظ الملف النهائي
        save_to_file(normalized_data, OUTPUT_FILE)
        print("اكتمل جلب وتحديث قائمة السوقين (NASDAQ + NYSE) بنجاح.")

    except Exception as e:
        print(f"خطأ فادح أثناء جلب رموز الأسهم: {e}")
        raise


if __name__ == "__main__":
    main()
