import json
import os
import time
from datetime import date, timedelta, datetime, timezone

import requests


API_URL = "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks"

API_KEY = os.environ.get("MASSIVE_API_KEY")

SYMBOLS_FILE = "symbols-reference.json"
DAILY_DATA_DIR = "daily-data"

# ============================================================
# نطاق التشغيل يأتي من GitHub Actions
#
# مثال التشغيل الأول:
# BACKFILL_FROM = 2025-01-01
# BACKFILL_TO   = 2026-01-01
#
# الـ TO غير شامل.
# أي أن التشغيل الأول يجلب:
# 2025-01-01 → 2025-12-31
# ============================================================

BACKFILL_FROM = os.environ.get(
    "BACKFILL_FROM",
    "2025-01-01"
)

BACKFILL_TO = os.environ.get(
    "BACKFILL_TO",
    "2026-01-01"
)

# الحد المجاني المفترض:
# 5 طلبات / دقيقة
# 12.5 ثانية ≈ 4.8 طلب / دقيقة
REQUEST_DELAY = 12.5


def parse_date(value, variable_name):

    try:
        return date.fromisoformat(value)

    except ValueError:
        raise RuntimeError(
            f"{variable_name} يجب أن يكون بصيغة YYYY-MM-DD. "
            f"القيمة الحالية: {value}"
        )


def load_symbols():

    with open(
        SYMBOLS_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if isinstance(data, list):

        items = data

    elif isinstance(data, dict):

        items = data.get(
            "symbols",
            data.get("records", [])
        )

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

            symbol = (
                item.get("symbol")
                or item.get("ticker")
            )

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
            "تأكد من صلاحية Daily Market Summary."
        )

    if response.status_code == 429:

        raise RuntimeError(
            "تم تجاوز حد Massive: "
            "5 طلبات/دقيقة."
        )

    response.raise_for_status()

    return response.json()


def build_daily_records(
    raw,
    symbols,
    d
):

    records = []

    results = raw.get(
        "results",
        []
    )

    for item in results:

        symbol = item.get("T")

        if not symbol:
            continue

        # فقط الرموز الموجودة في
        # symbols-reference.json
        metadata = symbols.get(symbol)

        if metadata is None:
            continue

        record = {
            "symbol": symbol,
            "date": d.isoformat(),

            "open": item.get("o"),
            "high": item.get("h"),
            "low": item.get("l"),
            "close": item.get("c"),
            "volume": item.get("v"),

            "market_cap": metadata.get(
                "market_cap",
                0.0
            ),

            "sector": metadata.get(
                "sector"
            ),

            "industry": metadata.get(
                "industry"
            ),

            "name": metadata.get(
                "name"
            )
        }

        records.append(record)

    return records


def save_day(
    d,
    records
):

    os.makedirs(
        DAILY_DATA_DIR,
        exist_ok=True
    )

    path = existing_file(d)

    # لا نلمس أي ملف موجود
    if os.path.exists(path):

        print(
            f"{d}: الملف موجود مسبقًا - تخطي"
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
        f"{d}: تم الحفظ - "
        f"{len(records)} سجل"
    )


def main():

    if not API_KEY:

        raise RuntimeError(
            "MASSIVE_API_KEY غير موجود "
            "في GitHub Secrets."
        )

    from_date = parse_date(
        BACKFILL_FROM,
        "BACKFILL_FROM"
    )

    to_date = parse_date(
        BACKFILL_TO,
        "BACKFILL_TO"
    )

    if from_date >= to_date:

        raise RuntimeError(
            "BACKFILL_FROM يجب أن يكون "
            "أقدم من BACKFILL_TO."
        )

    symbols = load_symbols()

    print()
    print("=" * 70)
    print("NASDAQ HISTORICAL BACKFILL")
    print("=" * 70)

    print(
        f"عدد الرموز: {len(symbols)}"
    )

    print(
        f"من: {from_date}"
    )

    print(
        f"إلى: {to_date}"
    )

    print(
        f"سيتم جلب الأيام: "
        f"{from_date} → {to_date - timedelta(days=1)}"
    )

    print("=" * 70)

    # نبدأ من اليوم السابق لـ BACKFILL_TO
    #
    # مثال:
    # BACKFILL_TO = 2026-01-01
    #
    # البداية الفعلية:
    # 2025-12-31
    #
    # ونستمر إلى:
    # 2025-01-01
    current_date = (
        to_date - timedelta(days=1)
    )

    downloaded_days = 0
    skipped_days = 0
    empty_days = 0

    while current_date >= from_date:

        d = current_date

        print()
        print("=" * 70)
        print(
            f"التاريخ: {d}"
        )
        print("=" * 70)

        # السبت والأحد
        if is_weekend(d):

            print(
                f"{d}: عطلة نهاية الأسبوع - تخطي"
            )

            skipped_days += 1

            current_date -= timedelta(days=1)

            continue

        # الملف موجود مسبقًا
        if os.path.exists(
            existing_file(d)
        ):

            print(
                f"{d}: موجود مسبقًا - لن نلمسه"
            )

            skipped_days += 1

            current_date -= timedelta(days=1)

            continue

        try:

            raw = fetch_day(d)

        except Exception as e:

            print()
            print(
                f"فشل جلب {d}: {e}"
            )

            print(
                "تم إيقاف التشغيل. "
                "عند إعادة تشغيل Workflow "
                "سيتم تخطي الملفات التي تم حفظها."
            )

            raise

        status = raw.get(
            "status"
        )

        if status != "OK":

            print(
                f"{d}: لا توجد بيانات - {status}"
            )

            empty_days += 1

            current_date -= timedelta(days=1)

            time.sleep(
                REQUEST_DELAY
            )

            continue

        records = build_daily_records(
            raw,
            symbols,
            d
        )

        if not records:

            print(
                f"{d}: لم نجد رموز NASDAQ."
            )

            empty_days += 1

            current_date -= timedelta(days=1)

            time.sleep(
                REQUEST_DELAY
            )

            continue

        save_day(
            d,
            records
        )

        downloaded_days += 1

        print(
            f"{d}: تم الحصول على "
            f"{len(records)} سجل NASDAQ"
        )

        # انتظار احترامًا لحد API
        print(
            f"انتظار {REQUEST_DELAY} ثانية..."
        )

        time.sleep(
            REQUEST_DELAY
        )

        current_date -= timedelta(days=1)

    print()
    print("=" * 70)
    print("اكتمل التشغيل")
    print("=" * 70)

    print(
        f"النطاق: "
        f"{from_date} → "
        f"{to_date - timedelta(days=1)}"
    )

    print(
        f"أيام تم جلبها: "
        f"{downloaded_days}"
    )

    print(
        f"أيام تم تخطيها: "
        f"{skipped_days}"
    )

    print(
        f"أيام بدون بيانات: "
        f"{empty_days}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
