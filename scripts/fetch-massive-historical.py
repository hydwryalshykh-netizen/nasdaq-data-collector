import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests


MASSIVE_URL = (
    "https://api.massive.com/v2/aggs/grouped/"
    "locale/us/market/stocks"
)

API_KEY = os.getenv("MASSIVE_API_KEY")

SYMBOLS_FILE = "symbols-reference.json"
DAILY_DIR = "daily-data"

BACKFILL_FROM = os.getenv(
    "BACKFILL_FROM",
    "2025-01-01",
)

BACKFILL_TO = os.getenv(
    "BACKFILL_TO",
    "2026-10-01",
)

REQUEST_DELAY = 12.5


def load_symbols():
    with open(
        SYMBOLS_FILE,
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    result = {}

    for item in data.get("symbols", []):
        symbol = str(item.get("symbol", "")).strip()

        if not symbol:
            continue

        result[symbol] = item

    return result


def fetch_day(date_string):
    params = {
        "adjusted": "true",
        "apiKey": API_KEY,
    }

    url = f"{MASSIVE_URL}/{date_string}"

    response = requests.get(
        url,
        params=params,
        timeout=120,
    )

    if response.status_code != 200:
        print(
            f"HTTP {response.status_code} "
            f"for {date_string}"
        )
        return None

    try:
        return response.json()
    except Exception:
        print(
            f"Invalid JSON returned for {date_string}"
        )
        return None


def build_daily_records(raw, symbols, date_string):
    if not raw:
        return []

    results = raw.get("results", [])

    if not isinstance(results, list):
        return []

    records = []

    for item in results:
        symbol = str(
            item.get("T", "")
        ).strip()

        if not symbol:
            continue

        metadata = symbols.get(symbol)

        if not metadata:
            continue

        record = {
            "symbol": symbol,
            "date": date_string,
            "open": item.get("o"),
            "high": item.get("h"),
            "low": item.get("l"),
            "close": item.get("c"),
            "volume": item.get("v"),
            "market_cap": metadata.get("market_cap"),
            "sector": metadata.get("sector"),
            "industry": metadata.get("industry"),
            "name": metadata.get("name"),
            "exchange": metadata.get("exchange"),
        }

        records.append(record)

    return records


def load_existing_file(path):
    if not os.path.exists(path):
        return None

    try:
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception as e:
        print(
            f"تحذير: تعذر قراءة {path}: {e}"
        )
        return None


def merge_existing_file(
    path,
    new_records,
    date_string,
):
    """
    مهم جداً:

    إذا كان الملف موجوداً من NASDAQ فقط:
    - لا نحذفه.
    - لا نستبدل بياناته.
    - لا نعيد بناء الملف من الصفر.
    - نضيف فقط رموز NYSE الناقصة.
    - نضيف exchange للسجلات القديمة إذا كان
      معروفاً من symbols-reference.json.

    هذا يجعل إضافة NYSE آمنة على التاريخ القديم.
    """

    existing = load_existing_file(path)

    if existing is None:
        return False, len(new_records), 0

    existing_records = existing.get("data", [])

    if not isinstance(existing_records, list):
        print(
            f"تحذير: بنية {path} غير متوقعة."
        )
        return False, 0, 0

    existing_by_symbol = {}

    for record in existing_records:
        symbol = str(
            record.get("symbol", "")
        ).strip()

        if symbol:
            existing_by_symbol[symbol] = record

    added = 0
    enriched = 0

    for new_record in new_records:
        symbol = new_record["symbol"]

        if symbol not in existing_by_symbol:
            existing_records.append(new_record)
            existing_by_symbol[symbol] = new_record
            added += 1
            continue

        # لا نغيّر بيانات OHLCV القديمة.
        # فقط نضيف exchange إذا كان مفقوداً.
        existing_record = existing_by_symbol[symbol]

        if (
            "exchange" not in existing_record
            and new_record.get("exchange")
        ):
            existing_record["exchange"] = (
                new_record["exchange"]
            )
            enriched += 1

    if added == 0 and enriched == 0:
        return False, 0, 0

    # نحافظ على التاريخ.
    existing["date"] = date_string

    existing["total_records"] = len(
        existing_records
    )

    existing["data"] = existing_records

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            existing,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return True, added, enriched


def save_new_day(
    path,
    date_string,
    records,
):
    output = {
        "date": date_string,
        "total_records": len(records),
        "data": records,
    }

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )


def process_day(
    date_string,
    symbols,
):
    os.makedirs(
        DAILY_DIR,
        exist_ok=True,
    )

    path = os.path.join(
        DAILY_DIR,
        f"{date_string}.json",
    )

    existing = load_existing_file(path)

    # إذا كان الملف موجوداً، نفحص هل يحتاج إلى
    # إضافة NYSE أم أنه مكتمل بالفعل.
    if existing is not None:
        existing_records = existing.get(
            "data",
            [],
        )

        existing_symbols = set()

        for record in existing_records:
            symbol = str(
                record.get("symbol", "")
            ).strip()

            if symbol:
                existing_symbols.add(symbol)

        nyse_symbols = {
            symbol
            for symbol, metadata in symbols.items()
            if metadata.get("exchange") == "NYSE"
        }

        missing_nyse = nyse_symbols - existing_symbols

        # إذا كانت رموز NYSE موجودة بالفعل،
        # لا نستهلك طلباً من Massive بلا داعٍ.
        if not missing_nyse:
            print(
                f"{date_string}: موجود ومكتمل، تخطي."
            )
            return False

        print(
            f"{date_string}: ملف موجود لكن ينقصه "
            f"{len(missing_nyse):,} رمز NYSE، سيتم الدمج."
        )

    raw = fetch_day(date_string)

    if not raw:
        print(
            f"{date_string}: لا توجد بيانات."
        )
        return False

    new_records = build_daily_records(
        raw,
        symbols,
        date_string,
    )

    if not new_records:
        print(
            f"{date_string}: لا توجد سجلات مطابقة."
        )
        return False

    if existing is None:
        save_new_day(
            path,
            date_string,
            new_records,
        )

        print(
            f"{date_string}: تم إنشاء الملف "
            f"بـ {len(new_records):,} سجل."
        )

        return True

    changed, added, enriched = merge_existing_file(
        path,
        new_records,
        date_string,
    )

    if changed:
        print(
            f"{date_string}: تمت إضافة "
            f"{added:,} سجل جديد، "
            f"وإثراء {enriched:,} سجل."
        )
    else:
        print(
            f"{date_string}: لا توجد إضافات."
        )

    return changed


def daterange_desc(start_date, end_date):
    current = end_date - timedelta(days=1)

    while current >= start_date:
        yield current
        current -= timedelta(days=1)


def main():
    if not API_KEY:
        print(
            "ERROR: MASSIVE_API_KEY غير موجود.",
            file=sys.stderr,
        )
        sys.exit(1)

    symbols = load_symbols()

    if not symbols:
        print(
            "ERROR: symbols-reference.json فارغ.",
            file=sys.stderr,
        )
        sys.exit(1)

    exchange_counts = {}

    for metadata in symbols.values():
        exchange = metadata.get(
            "exchange",
            "UNKNOWN",
        )

        exchange_counts[exchange] = (
            exchange_counts.get(exchange, 0) + 1
        )

    print(
        f"إجمالي الرموز: {len(symbols):,}"
    )
    print(
        f"NASDAQ: {exchange_counts.get('NASDAQ', 0):,}"
    )
    print(
        f"NYSE: {exchange_counts.get('NYSE', 0):,}"
    )

    start_date = datetime.strptime(
        BACKFILL_FROM,
        "%Y-%m-%d",
    ).date()

    end_date = datetime.strptime(
        BACKFILL_TO,
        "%Y-%m-%d",
    ).date()

    if end_date <= start_date:
        print(
            "ERROR: BACKFILL_TO يجب أن يكون بعد BACKFILL_FROM.",
            file=sys.stderr,
        )
        sys.exit(1)

    processed = 0
    changed = 0
    skipped_weekends = 0

    for current in daterange_desc(
        start_date,
        end_date,
    ):
        date_string = current.isoformat()

        # السبت والأحد
        if current.weekday() >= 5:
            skipped_weekends += 1
            continue

        try:
            did_change = process_day(
                date_string,
                symbols,
            )

            processed += 1

            if did_change:
                changed += 1

        except Exception as e:
            print(
                f"ERROR {date_string}: {e}",
                file=sys.stderr,
            )

        time.sleep(REQUEST_DELAY)

    print()
    print("انتهى التاريخ التاريخي.")
    print(f"أيام التداول المعالجة: {processed:,}")
    print(f"أيام تم تعديلها/إنشاؤها: {changed:,}")
    print(f"أيام نهاية الأسبوع المتجاهلة: {skipped_weekends:,}")


if __name__ == "__main__":
    main()
