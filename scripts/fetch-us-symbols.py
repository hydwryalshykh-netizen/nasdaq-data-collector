import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone

import requests


SYMBOLS_FILE = "symbols-reference.json"

NYSE_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
)


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def run_existing_nasdaq_script():
    """
    لا نعيد بناء نظام NASDAQ القديم.
    نشغل السكربت الموجود أصلاً والذي كان يعمل
    ونستخدم ناتجه كما هو.
    """

    print("تشغيل سكربت NASDAQ الموجود أصلاً...")

    subprocess.run(
        [
            sys.executable,
            "scripts/fetch-nasdaq-symbols.py",
        ],
        check=True,
    )

    with open(
        SYMBOLS_FILE,
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    symbols = data.get("symbols", [])

    if not symbols:
        raise RuntimeError(
            "سكربت NASDAQ الموجود لم يُرجع أي رموز."
        )

    print(
        f"NASDAQ الموجود: {len(symbols):,} رمز"
    )

    # نضيف exchange فقط بدون تغيير بيانات NASDAQ الأخرى.
    for item in symbols:
        item["exchange"] = "NASDAQ"

    return symbols


def fetch_nyse_symbols():
    print("جلب قائمة NYSE من Nasdaq Trader...")

    response = requests.get(
        NYSE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; NASDAQ-Data-Collector/1.0)"
            )
        },
        timeout=60,
    )

    response.raise_for_status()

    reader = csv.DictReader(
        io.StringIO(response.text),
        delimiter="|",
    )

    symbols = []

    for row in reader:

        exchange = clean_text(
            row.get("Exchange")
        )

        test_issue = clean_text(
            row.get("Test Issue")
        ).upper()

        # N = New York Stock Exchange
        if exchange != "N":
            continue

        # استبعاد Test Issues
        if test_issue == "Y":
            continue

        # نستخدم NASDAQ Symbol إذا كان موجوداً.
        symbol = clean_text(
            row.get("NASDAQ Symbol")
        )

        if not symbol:
            symbol = clean_text(
                row.get("CQS Symbol")
            )

        if not symbol:
            symbol = clean_text(
                row.get("ACT Symbol")
            )

        if not symbol:
            continue

        name = clean_text(
            row.get("Security Name")
        )

        # نستبعد ETF فقط.
        # لا نستخدم فلترة اسمية واسعة حتى لا نحذف
        # أسهماً صحيحة بالخطأ.
        etf = clean_text(
            row.get("ETF")
        ).upper()

        if etf == "Y":
            continue

        symbols.append(
            {
                "symbol": symbol,
                "name": name,
                "last_sale": None,
                "net_change": None,
                "change_percent": None,
                "market_cap": None,
                "country": None,
                "ipo_year": None,
                "volume": None,
                "sector": None,
                "industry": None,
                "exchange": "NYSE",
            }
        )

    if not symbols:
        raise RuntimeError(
            "لم يتم العثور على أسهم NYSE."
        )

    print(
        f"NYSE: {len(symbols):,} رمز"
    )

    return symbols


def merge_symbols(
    nasdaq_symbols,
    nyse_symbols,
):
    """
    نحافظ على NASDAQ كما هو أولاً.
    ثم نضيف NYSE.

    إذا كان نفس الرمز موجوداً في السوقين،
    لا نكرر الرمز لأن بقية النظام يعتمد على
    symbol كمفتاح.
    """

    merged = []
    seen = set()

    duplicate_count = 0

    for item in nasdaq_symbols:

        symbol = clean_text(
            item.get("symbol")
        )

        if not symbol:
            continue

        if symbol in seen:
            duplicate_count += 1
            continue

        seen.add(symbol)
        merged.append(item)

    for item in nyse_symbols:

        symbol = clean_text(
            item.get("symbol")
        )

        if not symbol:
            continue

        if symbol in seen:
            duplicate_count += 1
            continue

        seen.add(symbol)
        merged.append(item)

    if duplicate_count:
        print(
            f"تحذير: تم تجاهل "
            f"{duplicate_count:,} رمز مكرر."
        )

    return merged


def main():

    try:

        # =====================================================
        # 1. NASDAQ
        # نستخدم السكربت القديم الذي كان يعمل.
        # =====================================================

        nasdaq_symbols = (
            run_existing_nasdaq_script()
        )

        # =====================================================
        # 2. NYSE
        # =====================================================

        nyse_symbols = (
            fetch_nyse_symbols()
        )

        # =====================================================
        # 3. دمج NASDAQ + NYSE
        # =====================================================

        symbols = merge_symbols(
            nasdaq_symbols,
            nyse_symbols,
        )

        if not symbols:
            raise RuntimeError(
                "قائمة الرموز النهائية فارغة."
            )

        exchange_counts = {}

        for item in symbols:

            exchange = item.get(
                "exchange",
                "UNKNOWN",
            )

            exchange_counts[exchange] = (
                exchange_counts.get(
                    exchange,
                    0,
                )
                + 1
            )

        # =====================================================
        # 4. حفظ نفس symbols-reference.json
        # =====================================================

        output = {
            "fetched_at": datetime.now(
                timezone.utc
            ).isoformat(),

            "total_symbols": len(
                symbols
            ),

            "exchanges": exchange_counts,

            "symbols": symbols,
        }

        with open(
            SYMBOLS_FILE,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                output,
                f,
                ensure_ascii=False,
                indent=2,
            )

        print()
        print(
            "========================================"
        )
        print(
            "تم دمج NASDAQ + NYSE بنجاح"
        )
        print(
            "========================================"
        )
        print(
            f"NASDAQ: "
            f"{exchange_counts.get('NASDAQ', 0):,}"
        )
        print(
            f"NYSE: "
            f"{exchange_counts.get('NYSE', 0):,}"
        )
        print(
            f"TOTAL: "
            f"{len(symbols):,}"
        )

    except subprocess.CalledProcessError:
        print(
            "ERROR: سكربت NASDAQ الموجود فشل.",
            file=sys.stderr,
        )
        sys.exit(1)

    except Exception as e:
        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
