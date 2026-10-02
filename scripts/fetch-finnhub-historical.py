"""
scripts/fetch-finnhub-historical.py

الوظيفة: جلب البيانات التاريخية لسنة كاملة (OHLCV + RSI 14) لجميع أسهم NASDAQ و NYSE
مباشرة من API منصة Finnhub عبر نقطة النهاية /indicator بطلب واحد فقط لكل سهم.
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta

# جلب المفتاح من متغيرات بيئة GitHub Secrets
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_INDICATOR_URL = "https://finnhub.io/api/v1/indicator"

SYMBOLS_FILE = "symbols-reference.json"
OUTPUT_DIR = "historical-data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "finnhub_1y.json")

# نطاق سنة كاملة (365 يوماً)
NOW = datetime.utcnow()
ONE_YEAR_AGO = NOW - timedelta(days=365)
FROM_TIMESTAMP = int(ONE_YEAR_AGO.timestamp())
TO_TIMESTAMP = int(NOW.timestamp())

# التأخير بين الطلبات لتجنب تجاوز 60 طلب/دقيقة في الخطة المجانية
REQUEST_DELAY = 1.05


def load_symbols() -> list:
    if not os.path.exists(SYMBOLS_FILE):
        raise FileNotFoundError(f"لم يتم العثور على الملف الأساسي: {SYMBOLS_FILE}")

    with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    symbols_data = data.get("symbols", [])
    return [item["symbol"] for item in symbols_data if "symbol" in item]


def fetch_stock_data_from_finnhub(symbol: str, retries: int = 3) -> dict:
    """جلب الشمعات و RSI لسنة كاملة بطلب واحد مباشر من Finnhub"""
    params = {
        "symbol": symbol,
        "resolution": "D",
        "from": FROM_TIMESTAMP,
        "to": TO_TIMESTAMP,
        "indicator": "rsi",
        "timeperiod": 14,
        "token": FINNHUB_API_KEY,
    }

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(FINNHUB_INDICATOR_URL, params=params, timeout=15)

            # معالجة تجاوز حد الطلبات (Rate Limit 429)
            if response.status_code == 429:
                print(f"⚠️ تجاوز حد الطلبات عند السهم {symbol} (محاولة {attempt}). الانتظار 30 ثانية...")
                time.sleep(30)
                continue

            # إذا استجاب السيرفر برمز خطأ آخر (مثل 401 للمفتاح أو 403 للحظر)
            if response.status_code != 200:
                if attempt == 1:
                    print(f"❌ خطأ Finnhub للسهم {symbol}: رمز الاستجابة {response.status_code}")
                return None

            data = response.json()
            if data.get("s") == "ok":
                return {
                    "close": data.get("c", []),
                    "high": data.get("h", []),
                    "low": data.get("l", []),
                    "open": data.get("o", []),
                    "volume": data.get("v", []),
                    "timestamp": data.get("t", []),
                    "rsi": data.get("rsi", []),
                }
            elif data.get("s") == "no_data":
                return None

        except Exception as e:
            if attempt == retries:
                print(f"خطأ اتصال أثناء جلب {symbol}: {e}")

        time.sleep(1)

    return None


def main():
    if not FINNHUB_API_KEY:
        print("⚠️ تنبيه: لم يتم العثور على FINNHUB_API_KEY في متغيرات البيئة! تأكد من ضبط Secrets.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    symbols = load_symbols()
    total_symbols = len(symbols)
    historical_dataset = {}
    success_count = 0
    failed_count = 0

    print(f"[{datetime.now().isoformat()}] بدء جلب البيانات التاريخية و RSI من Finnhub لـ {total_symbols} سهماً...")
    start_time = time.time()

    for idx, symbol in enumerate(symbols, 1):
        stock_data = fetch_stock_data_from_finnhub(symbol)

        if stock_data and len(stock_data.get("timestamp", [])) > 0:
            historical_dataset[symbol] = stock_data
            success_count += 1
        else:
            failed_count += 1

        # طباعة التقدم كل 50 سهماً
        if idx % 50 == 0 or idx == total_symbols:
            elapsed = round((time.time() - start_time) / 60, 2)
            print(f"التقدم: {idx}/{total_symbols} ({round(idx/total_symbols*100, 1)}%) | نجاح: {success_count} | استغرق: {elapsed} دقيقة")

            # حفظ مرحلي معزول كل 500 سهم لحماية البيانات
            if idx % 500 == 0:
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(historical_dataset, f, ensure_ascii=False)

        time.sleep(REQUEST_DELAY)

    # الحفظ النهائي
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

    print(f"\nاكتمل الجلب المباشر من Finnhub! تم حفظ {success_count} سهماً في {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
