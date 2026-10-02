"""
scripts/fetch-finnhub-historical.py
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "ctff501r01qnd3f095d0ctff501r01qnd3f095dg")
FINNHUB_CANDLE_URL = "https://finnhub.io/api/v1/stock/candle"

SYMBOLS_FILE = "symbols-reference.json"

# مجلد وملف حفظ معزول تماماً عن البيانات اليومية
OUTPUT_DIR = "historical-data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "finnhub_1y.json")

NOW = datetime.utcnow()
ONE_YEAR_AGO = NOW - timedelta(days=365)
FROM_TIMESTAMP = int(ONE_YEAR_AGO.timestamp())
TO_TIMESTAMP = int(NOW.timestamp())

REQUEST_DELAY = 1.05


def load_symbols() -> list:
    if not os.path.exists(SYMBOLS_FILE):
        raise FileNotFoundError(f"لم يتم العثور على الملف الأساسي: {SYMBOLS_FILE}")

    with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    symbols_data = data.get("symbols", [])
    return [item["symbol"] for item in symbols_data if "symbol" in item]


def fetch_stock_candles(symbol: str) -> dict:
    params = {
        "symbol": symbol,
        "resolution": "D",
        "from": FROM_TIMESTAMP,
        "to": TO_TIMESTAMP,
        "token": FINNHUB_API_KEY,
    }

    try:
        response = requests.get(FINNHUB_CANDLE_URL, params=params, timeout=15)
        if response.status_code == 429:
            time.sleep(10)
            return None

        if response.status_code != 200:
            return None

        data = response.json()
        if data.get("s") == "ok":
            return {
                "close": data.get("c", []),
                "high": data.get("h", []),
                "low": data.get("l", []),
                "open": data.get("o", []),
                "timestamp": data.get("t", []),
                "volume": data.get("v", []),
            }
    except Exception:
        pass

    return None


def main():
    # إنشاء المجلد المعزول تلقائياً إن لم يكن موجوداً
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    symbols = load_symbols()
    total_symbols = len(symbols)
    historical_dataset = {}
    success_count = 0
    failed_count = 0

    print(f"[{datetime.now().isoformat()}] بدء جلب البيانات التاريخية لـ {total_symbols} سهماً...")
    start_time = time.time()

    for idx, symbol in enumerate(symbols, 1):
        candles = fetch_stock_candles(symbol)

        if candles and len(candles["timestamp"]) > 0:
            historical_dataset[symbol] = candles
            success_count += 1
        else:
            failed_count += 1

        if idx % 50 == 0 or idx == total_symbols:
            elapsed = round((time.time() - start_time) / 60, 2)
            print(f"التقدم: {idx}/{total_symbols} ({round(idx/total_symbols*100, 1)}%) | نجاح: {success_count} | استغرق: {elapsed} دقيقة")

            # حفظ مرحلي معزول كل 500 سهم
            if idx % 500 == 0:
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(historical_dataset, f, ensure_ascii=False)

        time.sleep(REQUEST_DELAY)

    final_output = {
        "fetched_at": datetime.now().isoformat(),
        "total_symbols_processed": total_symbols,
        "successful_symbols": success_count,
        "failed_symbols": failed_count,
        "from_date": ONE_YEAR_AGO.strftime("%Y-%m-%d"),
        "to_date": NOW.strftime("%Y-%m-%d"),
        "data": historical_dataset,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)

    print(f"تم الحفظ بنجاح في المجلد المعزول: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
