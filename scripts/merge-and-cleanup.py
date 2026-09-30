import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo


SYMBOLS_FILE = "symbols-reference.json"
OHLC_FILE = "ohlc-data-today.json"

DAILY_DIR = "daily-data"
LATEST_FILE = "latest.json"

RETENTION_DAYS = 365


# العطل الرسمية للسوق الأمريكي لعام 2026
# تبقى كما كانت في النظام السابق.
US_MARKET_HOLIDAYS_2026 = {
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25",
}


def get_market_date():
    now = datetime.now(
        ZoneInfo("America/New_York")
    )

    return now.date().isoformat()


def is_market_closed(date_string):
    date = datetime.strptime(
        date_string,
        "%Y-%m-%d",
    ).date()

    if date.weekday() >= 5:
        return True

    if date_string in US_MARKET_HOLIDAYS_2026:
        return True

    return False


def load_json(path):
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def merge_data():
    date_string = get_market_date()

    if is_market_closed(date_string):
        print(
            f"السوق مغلق في {date_string}."
        )
        return None

    if not os.path.exists(SYMBOLS_FILE):
        raise FileNotFoundError(
            SYMBOLS_FILE
        )

    if not os.path.exists(OHLC_FILE):
        raise FileNotFoundError(
            OHLC_FILE
        )

    symbols_data = load_json(
        SYMBOLS_FILE
    )

    ohlc_data = load_json(
        OHLC_FILE
    )

    symbols = symbols_data.get(
        "symbols",
        [],
    )

    ohlc_records = ohlc_data.get(
        "ohlc_data",
        [],
    )

    metadata = {}

    for item in symbols:
        symbol = str(
            item.get("symbol", "")
        ).strip()

        if not symbol:
            continue

        metadata[symbol] = item

    records = []

    for item in ohlc_records:
        symbol = str(
            item.get("symbol", "")
        ).strip()

        if not symbol:
            continue

        info = metadata.get(
            symbol,
            {},
        )

        record = {
            "symbol": symbol,
            "date": date_string,
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "close": item.get("close"),
            "volume": item.get("volume"),
            "market_cap": info.get(
                "market_cap"
            ),
            "sector": info.get(
                "sector"
            ),
            "industry": info.get(
                "industry"
            ),
            "name": info.get(
                "name"
            ),
            "exchange": info.get(
                "exchange"
            ),
        }

        records.append(record)

    return {
        "date": date_string,
        "total_records": len(records),
        "data": records,
    }


def save_daily_file(data):
    if not data:
        return

    os.makedirs(
        DAILY_DIR,
        exist_ok=True,
    )

    date_string = data["date"]

    path = os.path.join(
        DAILY_DIR,
        f"{date_string}.json",
    )

    old_data = None

    if os.path.exists(path):
        try:
            old_data = load_json(path)
        except Exception:
            old_data = None

    if old_data:
        old_count = len(
            old_data.get(
                "data",
                [],
            )
        )

        new_count = len(
            data.get(
                "data",
                [],
            )
        )

        if old_count > 0:
            difference = abs(
                new_count - old_count
            ) / old_count

            if difference > 0.05:
                print(
                    "WARNING: عدد السجلات تغير بأكثر من 5%."
                )
                print(
                    f"قديم: {old_count:,}"
                )
                print(
                    f"جديد: {new_count:,}"
                )

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"تم حفظ {path} "
        f"({data['total_records']:,} سجل)"
    )


def save_latest(data):
    if not data:
        return

    with open(
        LATEST_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"تم تحديث {LATEST_FILE}"
    )


def cleanup_old_files():
    if not os.path.isdir(DAILY_DIR):
        return

    files = []

    for filename in os.listdir(
        DAILY_DIR
    ):
        if not filename.endswith(
            ".json"
        ):
            continue

        if len(filename) != 15:
            continue

        date_string = filename[:-5]

        try:
            date = datetime.strptime(
                date_string,
                "%Y-%m-%d",
            ).date()

        except ValueError:
            continue

        files.append(
            (
                date,
                filename,
            )
        )

    files.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    for _, filename in files[
        RETENTION_DAYS:
    ]:
        path = os.path.join(
            DAILY_DIR,
            filename,
        )

        try:
            os.remove(path)
            print(
                f"تم حذف الملف القديم: {filename}"
            )
        except Exception as e:
            print(
                f"تعذر حذف {filename}: {e}"
            )


def main():
    data = merge_data()

    if data is None:
        return

    save_daily_file(data)
    save_latest(data)
    cleanup_old_files()


if __name__ == "__main__":
    main()
