"""
fetch-nasdaq-symbols.py
=======================
جلب قائمة رموز NASDAQ من Nasdaq Screener API.
"""

import requests
import json
from datetime import datetime


NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks"
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
}

OUTPUT_FILE = "symbols-reference.json"


def clean_number(value):
    """
    يحول القيم الرقمية القادمة من Nasdaq
    سواء كانت:
      123456
      "123456"
      "123,456"
      "$123.45"
      "123.45%"
    """

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return value

    value = str(value).strip()

    if not value:
        return None

    value = (
        value
        .replace("$", "")
        .replace(",", "")
        .replace("%", "")
        .strip()
    )

    try:
        return float(value)
    except ValueError:
        return None


def fetch_nasdaq_symbols():

    params = {
        "tableonly": "true",
        "limit": "10000",
        "offset": "0",
        "download": "true",
        "exchange": "nasdaq",
    }

    print(
        f"[{datetime.now().isoformat()}] "
        "جاري الاتصال بـ NASDAQ API..."
    )

    response = requests.get(
        NASDAQ_SCREENER_URL,
        headers=HEADERS,
        params=params,
        timeout=60,
    )

    print(
        f"HTTP Status: {response.status_code}"
    )

    if response.status_code != 200:
        raise RuntimeError(
            "فشل الاتصال بـ NASDAQ API. "
            f"رمز الحالة: {response.status_code} — "
            f"النص: {response.text[:500]}"
        )

    try:
        data = response.json()
    except Exception as e:
        raise RuntimeError(
            f"استجابة NASDAQ ليست JSON صالحاً: {e}"
        )

    # =========================================================
    # Nasdaq قد يعيد البيانات بأكثر من بنية.
    # ندعم البنية القديمة والجديدة.
    # =========================================================

    rows = []

    root_data = data.get("data")

    if isinstance(root_data, dict):

        # البنية الأولى:
        # data -> rows
        if isinstance(
            root_data.get("rows"),
            list,
        ):
            rows = root_data["rows"]

        # البنية الشائعة:
        # data -> table -> rows
        elif isinstance(
            root_data.get("table"),
            dict,
        ):

            table = root_data["table"]

            if isinstance(
                table.get("rows"),
                list,
            ):
                rows = table["rows"]

    if not rows:
        raise RuntimeError(
            "لم يتم العثور على أسهم NASDAQ. "
            "بنية استجابة API غير متوقعة."
        )

    print(
        f"[{datetime.now().isoformat()}] "
        f"تم جلب {len(rows):,} صف من NASDAQ."
    )

    return rows


def normalize_symbol_data(raw_rows):

    normalized = []

    for row in raw_rows:

        try:

            symbol = str(
                row.get("symbol", "")
            ).strip()

            if not symbol:
                continue

            name = str(
                row.get("name", "")
            ).strip()

            last_sale = clean_number(
                row.get("lastsale")
            )

            net_change = clean_number(
                row.get("netchange")
            )

            pct_change = clean_number(
                row.get("pctchange")
            )

            market_cap = clean_number(
                row.get("marketCap")
            )

            volume_value = clean_number(
                row.get("volume")
            )

            if volume_value is not None:
                volume = int(
                    volume_value
                )
            else:
                volume = None

            normalized.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "last_sale": last_sale,
                    "net_change": net_change,
                    "change_percent": pct_change,
                    "market_cap": market_cap,
                    "country": str(
                        row.get(
                            "country",
                            "",
                        )
                    ).strip(),
                    "ipo_year": row.get(
                        "ipoyear"
                    ),
                    "volume": volume,
                    "sector": (
                        str(
                            row.get(
                                "sector",
                                "",
                            )
                        ).strip()
                        or None
                    ),
                    "industry": (
                        str(
                            row.get(
                                "industry",
                                "",
                            )
                        ).strip()
                        or None
                    ),
                    "exchange": "NASDAQ",
                }
            )

        except Exception as e:

            print(
                "تحذير: تجاهل صف "
                f"{row.get('symbol', '?')}: {e}"
            )

            continue

    return normalized


def save_to_file(data):

    output = {
        "fetched_at": datetime.now().isoformat(),
        "total_symbols": len(data),
        "symbols": data,
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"[{datetime.now().isoformat()}] "
        f"تم حفظ {len(data):,} سهماً "
        f"في {OUTPUT_FILE}"
    )


def main():

    try:

        raw_rows = fetch_nasdaq_symbols()

        normalized_data = normalize_symbol_data(
            raw_rows
        )

        if len(normalized_data) < 1000:

            raise RuntimeError(
                "تحذير أمان: عدد أسهم NASDAQ "
                f"بعد المعالجة ({len(normalized_data):,}) "
                "أقل بكثير من المتوقع."
            )

        save_to_file(
            normalized_data
        )

        print(
            "اكتمل جلب NASDAQ بنجاح."
        )

    except Exception as e:

        print(
            f"خطأ فادح أثناء جلب بيانات NASDAQ: {e}"
        )

        raise


if __name__ == "__main__":
    main()
