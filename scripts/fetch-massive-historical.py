import json
import os
import time
from datetime import date, timedelta, datetime, timezone

import requests


API_URL = "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks"

API_KEY = os.environ.get("MASSIVE_API_KEY")

SYMBOLS_FILE = "symbols-reference.json"
DAILY_DATA_DIR = "daily-data"

# الملف الموجود عندنا بالفعل
LATEST_EXISTING_DATE = date(2026, 8, 8)

# نريد الرجوع إلى بداية السنة
END_DATE = date(2026, 1, 1)

# الحد المجاني = 5 طلبات/دقيقة
# 12.5 ثانية = 4.8 طلب/دقيقة تقريبًا
REQUEST_DELAY = 12.5


def load_symbols():
    with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("symbols", data.get("records", []))
    else:
        items = []

    result = {}

    for item in items:
        if isinstance(item, str):
            symbol = item
            result[symbol] = {
                "symbol": symbol
            }
        elif isinstance(item, dict):
            symbol = item.get("symbol") or item.get("ticker")

            if symbol:
                result[symbol] = item

    return result


def is_weekend(d):
    return d.weekday() >= 5


def existing_file(d):
    return os.path.join(
        DAILY_DATA_DIR,
        f"{d.isoformat()}.json"
    )


def fetch_day(d):
    url = f"{API_URL}/{d.isoformat()}"

    params = {
        "apiKey": API_KEY,
        "adjusted": "true",
        "include_otc": "false"
    }

    response = requests.get(
        url,
        params=params,
        timeout=60
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Massive رفض مفتاح API. "
            "تأكد من MASSIVE_API_KEY."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Massive أعاد 403. "
            "تأكد من أن مفتاح Stocks Basic لديه صلاحية "
            "Daily Market Summary."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "تم تجاوز حد Massive: 5 طلبات/دقيقة."
        )

    response.raise_for_status()

    return response.json()


def build_daily_records(raw, symbols):
    records = []

    results = raw.get("results", [])

    for item in results:

        symbol = item.get("T")

        if not symbol:
            continue

        # فقط الرموز الموجودة في قائمة NASDAQ الخاصة بنا
        metadata = symbols.get(symbol)

        if metadata is None:
            continue

        record = {
            "symbol": symbol,
            "date": current_date.isoformat(),

            "open": item.get("o"),
            "high": item.get("h"),
            "low": item.get("l"),
            "close": item.get("c"),
            "volume": item.get("v"),

            "market_cap": metadata.get("market_cap", 0.0),
            "sector": metadata.get("sector"),
            "industry": metadata.get("industry"),
            "name": metadata.get("name")
        }

        records.append(record)

    return records


def save_day(d, records):
    os.makedirs(
        DAILY_DATA_DIR,
        exist_ok=True
    )

    path = existing_file(d)

    if os.path.exists(path):
        print(
            f"الملف موجود مسبقًا، لن نلمسه: {path}"
        )
        return

    output = {
        "date": d.isoformat(),
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "total_records": len(records),
        "records": records
    }

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"تم حفظ {path} "
        f"بعدد {len(records)} سجل"
    )


def main():

    global current_date

    if not API_KEY:
        raise RuntimeError(
            "MASSIVE_API_KEY غير موجود في GitHub Secrets."
        )

    symbols = load_symbols()

    print(
        f"تم تحميل {len(symbols)} رمزًا من "
        f"{SYMBOLS_FILE}"
    )

    current_date = LATEST_EXISTING_DATE - timedelta(days=1)

    while current_date >= END_DATE:

        d = current_date

        print()
        print("=" * 70)
        print(
            f"جلب البيانات التاريخية: {d}"
        )
        print("=" * 70)

        # السبت والأحد
        if is_weekend(d):
            print(
                f"{d} عطلة نهاية أسبوع - تخطي"
            )

            current_date -= timedelta(days=1)
            continue

        # إذا كان الملف موجودًا
        if os.path.exists(existing_file(d)):

            print(
                f"{d} موجود مسبقًا - تخطي"
            )

            current_date -= timedelta(days=1)
            continue

        # جلب السوق الأمريكي كاملًا
        raw = fetch_day(d)

        status = raw.get("status")

        if status != "OK":
            print(
                f"{d}: لا توجد بيانات - {status}"
            )

            current_date -= timedelta(days=1)

            time.sleep(REQUEST_DELAY)

            continue

        records = build_daily_records(
            raw,
            symbols
        )

        if not records:
            print(
                f"{d}: لم نجد رموز NASDAQ في النتائج."
            )

            current_date -= timedelta(days=1)

            time.sleep(REQUEST_DELAY)

            continue

        save_day(
            d,
            records
        )

        print(
            f"{d}: تم الحصول على "
            f"{len(records)} سجل NASDAQ"
        )

        # احترام حد 5 طلبات/دقيقة
        print(
            f"انتظار {REQUEST_DELAY} ثانية "
            "قبل الطلب التالي..."
        )

        time.sleep(REQUEST_DELAY)

        current_date -= timedelta(days=1)

    print()
    print("=" * 70)
    print("اكتمل الـHistorical Backfill")
    print("الفترة: 2026-01-01 → 2026-08-08")
    print("=" * 70)


if __name__ == "__main__":
    main()
