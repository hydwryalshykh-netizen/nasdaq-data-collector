"""
merge-and-cleanup.py
======================
الوظيفة: يدمج بيانات NASDAQ مع بيانات OHLC من Finnhub بسجل JSON واحد موحّد لكل سهم.

ملاحظة مهمة حول التاريخ:
-------------------------
لا نعتمد على datetime.now() لتحديد "تاريخ اليوم"، لأن GitHub Actions runner يعمل
بتوقيت UTC، بينما بيانات Finnhub تعكس آخر إغلاق فعلي لسوق NASDAQ (توقيت نيويورك).
الفرق بين التوقيتين (4-5 ساعات) كان يسبب أحياناً تسجيل بيانات يوم معين تحت تاريخ
اليوم التالي (أو العكس)، مما أدى لتكرار/فقدان أيام في الأرشيف.

الحل: نشتق "تاريخ التداول" من حقل timestamp الحقيقي الراجع من Finnhub لكل سهم
(محوّلاً لتوقيت America/New_York)، وليس من ساعة تشغيل السكربت.
"""

import json
import os
import glob
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SYMBOLS_FILE = "symbols-reference.json"
OHLC_FILE = "ohlc-data-today.json"
DAILY_DATA_DIR = "daily-data"
LATEST_FILE = "latest.json"

RETENTION_DAYS = 365

NY_TZ = ZoneInfo("America/New_York")


def load_json_file(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"الملف المطلوب غير موجود: {filepath}.")

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_market_date(ohlc_data):
    """
    يحدد تاريخ التداول الفعلي بالاعتماد على أحدث timestamp حقيقي من Finnhub
    بين كل الأسهم، محوّلاً لتوقيت نيويورك (توقيت سوق NASDAQ).

    هذا يضمن أن السجل يُحفظ دائماً تحت تاريخ يوم التداول الصحيح، بغض النظر
    عن توقيت تشغيل GitHub Actions (UTC).
    """
    timestamps = [
        entry["timestamp"]
        for entry in ohlc_data.values()
        if entry and entry.get("timestamp")
    ]

    if not timestamps:
        # احتياطي فقط في حال عدم توفر أي timestamp صالح من Finnhub على الإطلاق
        print("تحذير: لا توجد أي timestamps صالحة من Finnhub، سيُستخدم وقت التشغيل كبديل.")
        return datetime.now(NY_TZ).strftime("%Y-%m-%d")

    # نأخذ أحدث timestamp (وليس أول واحد) لأنه الأقرب لحظة إغلاق السوق الفعلية
    latest_ts = max(timestamps)
    market_time = datetime.fromtimestamp(latest_ts, tz=NY_TZ)
    return market_time.strftime("%Y-%m-%d")


def merge_data(symbols_data, ohlc_data, market_date):
    merged_records = []

    for symbol_entry in symbols_data:
        symbol = symbol_entry["symbol"]
        ohlc = ohlc_data.get(symbol)

        record = {
            "symbol": symbol,
            "date": market_date,
            "open": ohlc["open"] if ohlc else None,
            "high": ohlc["high"] if ohlc else None,
            "low": ohlc["low"] if ohlc else None,
            "close": ohlc["close"] if ohlc else symbol_entry.get("last_sale"),
            "volume": symbol_entry.get("volume"),
            "market_cap": symbol_entry.get("market_cap"),
            "sector": symbol_entry.get("sector"),
            "industry": symbol_entry.get("industry"),
            "name": symbol_entry.get("name"),
        }

        merged_records.append(record)

    return merged_records


def save_daily_file(merged_records, market_date):
    os.makedirs(DAILY_DATA_DIR, exist_ok=True)

    filepath = os.path.join(DAILY_DATA_DIR, f"{market_date}.json")

    output = {
        "date": market_date,
        "generated_at": datetime.now(NY_TZ).isoformat(),
        "total_records": len(merged_records),
        "records": merged_records,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"تم حفظ سجل اليوم بملف: {filepath} ({len(merged_records)} سهماً)")

    return output


def update_latest_file(daily_output):
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(daily_output, f, ensure_ascii=False, indent=2)

    print(f"تم تحديث {LATEST_FILE}")


def cleanup_old_files():
    if not os.path.exists(DAILY_DATA_DIR):
        return

    cutoff_date = datetime.now(NY_TZ).replace(tzinfo=None) - timedelta(days=RETENTION_DAYS)
    deleted_count = 0

    for filepath in glob.glob(os.path.join(DAILY_DATA_DIR, "*.json")):
        filename = os.path.basename(filepath)
        date_str = filename.replace(".json", "")

        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if file_date < cutoff_date:
            os.remove(filepath)
            deleted_count += 1
            print(f"تم حذف الملف القديم: {filename}")

    if deleted_count == 0:
        print("لا توجد ملفات قديمة تحتاج حذفاً.")
    else:
        print(f"تم حذف {deleted_count} ملفاً قديماً بالإجمالي.")


def main():
    print("بدء عملية الدمج والتنظيف...")

    symbols_data = load_json_file(SYMBOLS_FILE)["symbols"]
    ohlc_data = load_json_file(OHLC_FILE)["ohlc_data"]

    market_date = get_market_date(ohlc_data)
    print(f"تاريخ التداول المُحدد من بيانات Finnhub: {market_date}")

    merged_records = merge_data(symbols_data, ohlc_data, market_date)

    daily_output = save_daily_file(merged_records, market_date)
    update_latest_file(daily_output)

    cleanup_old_files()

    print("اكتملت عملية الدمج والتنظيف بنجاح.")


if __name__ == "__main__":
    main()
