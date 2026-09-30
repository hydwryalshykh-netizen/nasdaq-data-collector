import csv
import io
import json
import sys
from datetime import datetime, timezone

import requests


NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"

NYSE_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

OUTPUT_FILE = "symbols-reference.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NASDAQ-Data-Collector/1.0)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nasdaq.com/",
}


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def fetch_nasdaq_symbols():
    print("جلب قائمة NASDAQ...")

    params = {
        "tableonly": "true",
        "limit": 10000,
        "offset": 0,
        "download": "true",
        "exchange": "nasdaq",
    }

    response = requests.get(
        NASDAQ_URL,
        headers=HEADERS,
        params=params,
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    rows = (
        data.get("data", {})
        .get("table", {})
        .get("rows", [])
    )

    if not rows:
        raise RuntimeError("لم يتم العثور على أسهم NASDAQ.")

    symbols = []

    for row in rows:
        symbol = clean_text(row.get("symbol"))

        if not symbol:
            continue

        symbols.append({
            "symbol": symbol,
            "name": clean_text(row.get("name")),
            "last_sale": row.get("lastsale"),
            "net_change": row.get("netchange"),
            "change_percent": row.get("pctchange"),
            "market_cap": row.get("marketCap"),
            "country": clean_text(row.get("country")),
            "ipo_year": row.get("ipoyear"),
            "volume": row.get("volume"),
            "sector": clean_text(row.get("sector")),
            "industry": clean_text(row.get("industry")),
            "exchange": "NASDAQ",
        })

    if len(symbols) < 1000:
        raise RuntimeError(
            f"عدد أسهم NASDAQ منخفض بشكل غير طبيعي: {len(symbols)}"
        )

    print(f"NASDAQ: {len(symbols):,} رمز")

    return symbols


def fetch_nyse_symbols():
    print("جلب قائمة NYSE من Nasdaq Trader...")

    response = requests.get(
        NYSE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; NASDAQ-Data-Collector/1.0)"
        },
        timeout=60,
    )

    response.raise_for_status()

    text = response.text

    reader = csv.DictReader(
        io.StringIO(text),
        delimiter="|",
    )

    symbols = []

    for row in reader:
        exchange = clean_text(row.get("Exchange"))
        test_issue = clean_text(row.get("Test Issue")).upper()

        # N = New York Stock Exchange
        if exchange != "N":
            continue

        # استبعاد الاختبارات
        if test_issue == "Y":
            continue

        # نستخدم NASDAQ Symbol لأنه معرف السوق المستخدم في
        # بروتوكولات Nasdaq وبيانات السوق.
        symbol = clean_text(row.get("NASDAQ Symbol"))

        if not symbol:
            # fallback
            symbol = clean_text(row.get("CQS Symbol"))

        if not symbol:
            symbol = clean_text(row.get("ACT Symbol"))

        if not symbol:
            continue

        security_name = clean_text(row.get("Security Name"))

        # استبعاد أدوات واضحة ليست أسهماً عادية.
        # ETF = Y يعني ETF وليس سهماً.
        etf = clean_text(row.get("ETF")).upper()

        if etf == "Y":
            continue

        lower_name = security_name.lower()

        excluded_terms = [
            "warrant",
            "rights",
            " right ",
            "notes due",
            "debenture",
            "preferred stock",
            "depositary share",
            "unit,",
        ]

        if any(term in lower_name for term in excluded_terms):
            continue

        symbols.append({
            "symbol": symbol,
            "name": security_name,
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
        })

    if len(symbols) < 1000:
        raise RuntimeError(
            f"عدد أسهم NYSE منخفض بشكل غير طبيعي: {len(symbols)}"
        )

    print(f"NYSE: {len(symbols):,} رمز")

    return symbols


def merge_symbols(nasdaq_symbols, nyse_symbols):
    """
    دمج NASDAQ + NYSE مع منع تكرار الرمز.

    إذا ظهر نفس الرمز في السوقين، نحتفظ بأول نسخة
    حتى لا ينكسر النظام الحالي الذي يعتمد على symbol كمفتاح.
    """

    merged = []
    seen = set()
    duplicate_count = 0

    # NASDAQ أولاً حتى نحافظ على سلوك النظام السابق.
    for item in nasdaq_symbols:
        symbol = clean_text(item.get("symbol"))

        if not symbol:
            continue

        if symbol in seen:
            duplicate_count += 1
            continue

        seen.add(symbol)
        merged.append(item)

    for item in nyse_symbols:
        symbol = clean_text(item.get("symbol"))

        if not symbol:
            continue

        if symbol in seen:
            duplicate_count += 1
            continue

        seen.add(symbol)
        merged.append(item)

    if duplicate_count:
        print(
            f"تحذير: تم تجاهل {duplicate_count:,} رمز مكرر "
            "لأن النظام يعتمد على symbol كمفتاح."
        )

    return merged


def main():
    try:
        nasdaq_symbols = fetch_nasdaq_symbols()
        nyse_symbols = fetch_nyse_symbols()

        symbols = merge_symbols(
            nasdaq_symbols,
            nyse_symbols,
        )

        if len(symbols) < 2000:
            raise RuntimeError(
                f"إجمالي الرموز منخفض بشكل غير طبيعي: {len(symbols)}"
            )

        exchange_counts = {}

        for item in symbols:
            exchange = item.get("exchange", "UNKNOWN")
            exchange_counts[exchange] = (
                exchange_counts.get(exchange, 0) + 1
            )

        output = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_symbols": len(symbols),
            "exchanges": exchange_counts,
            "symbols": symbols,
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

        print()
        print("تم إنشاء symbols-reference.json بنجاح.")
        print(f"الإجمالي: {len(symbols):,}")
        print(f"NASDAQ: {exchange_counts.get('NASDAQ', 0):,}")
        print(f"NYSE: {exchange_counts.get('NYSE', 0):,}")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
