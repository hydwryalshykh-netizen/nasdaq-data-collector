"""
fetch-finnhub-ohlc.py
======================
الوظيفة: يجلب بيانات السعر الكاملة (الافتتاح، الأعلى، الأدنى، الإغلاق، الحجم) من Finnhub.
"""

import requests
import json
import os
import time
from datetime import datetime

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")

SYMBOLS_FILE = "symbols-reference.json"
OUTPUT_FILE = "ohlc-data-today.json"

DELAY_BETWEEN_REQUESTS_SECONDS = 1.05


def load_symbols():
    if not os.path.exists(SYMBOLS_FILE):
        raise FileNotFoundError(
            f"لم يُعثر على {SYMBOLS_FILE}. يجب تشغيل fetch-nasdaq-symbols.py أولاً."
        )

    with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data["symbols"]


def fetch_ohlc_for_symbol(symbol):
    params = {
        "symbol": symbol,
        "token": FINNHUB_API_KEY,
    }

    try:
        response = requests.get(FINNHUB_QUOTE_URL, params=params, timeout=10)

        if response.status_code == 429:
            print(f"تحذير: تجاوز حد الطلبات عند {symbol}، الانتظار 60 ثانية...")
            time.sleep(60)
            response = requests.get(FINNHUB_QUOTE_URL, params=params, timeout=10)

        if response.status_code != 200:
            print(f"تحذير: فشل جلب {symbol} — رمز الحالة: {response.status_code}")
            return None

        data = response.json()

        if data.get("c", 0) == 0 and data.get("o", 0) == 0:
            return None

        return {
            "open": data.get("o"),
            "high": data.get("h"),
            "low": data.get("l"),
            "close": data.get("c"),
            "previous_close": data.get("pc"),
            "timestamp": data.get("t"),
        }

    except requests.exceptions.RequestException as e:
        print(f"تحذير: خطأ اتصال عند جلب {symbol}: {e}")
        return None


def main():
    if not FINNHUB_API_KEY:
        raise EnvironmentError("متغيّر البيئة FINNHUB_API_KEY غير موجود.")

    symbols_data = load_symbols()
    total = len(symbols_data)
    print(f"[{datetime.now().isoformat()}] بدء جلب بيانات OHLC لـ {total} سهماً...")

    results = {}
    success_count = 0
    fail_count = 0

    for index, symbol_entry in enumerate(symbols_data, start=1):
        symbol = symbol_entry["symbol"]
        ohlc = fetch_ohlc_for_symbol(symbol)

        if ohlc is not None:
            results[symbol] = ohlc
            success_count += 1
        else:
            fail_count += 1

        if index % 100 == 0:
            print(
                f"[{datetime.now().isoformat()}] "
                f"التقدم: {index}/{total} (نجح: {success_count}, فشل: {fail_count})"
            )

        time.sleep(DELAY_BETWEEN_REQUESTS_SECONDS)

    failure_rate = fail_count / total if total > 0 else 1
    if failure_rate > 0.3:
        raise RuntimeError(f"نسبة فشل عالية جداً ({failure_rate:.0%}). لن يُحفظ الملف.")

    output = {
        "fetched_at": datetime.now().isoformat(),
        "total_requested": total,
        "total_success": success_count,
        "total_failed": fail_count,
        "ohlc_data": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(
        f"[{datetime.now().isoformat()}] اكتمل الجلب. "
        f"نجح: {success_count}/{total} — تم الحفظ بملف {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
