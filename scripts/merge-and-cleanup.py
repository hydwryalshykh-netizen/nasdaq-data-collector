"""
merge-and-cleanup.py
======================
الوظيفة: يدمج بيانات NASDAQ مع بيانات OHLC من Finnhub بسجل JSON واحد موحّد لكل سهم.

===============================================================================
ملاحظة مهمة جداً حول طريقة حساب التاريخ (بعد حادثة تداخل بيانات 27/28 أغسطس 2026):
===============================================================================
كانت النسخة السابقة من هذا الملف تحسب "تاريخ التداول" بالاعتماد على أقصى قيمة
timestamp راجعة من Finnhub بين كل الأسهم (get_market_date القديمة). هذا الأسلوب
كان يبدو منطقياً، لكنه تسبب فعلياً بخلط بيانات يومين مختلفين ببعض:

  - بعض رموز الأسهم (خصوصاً قليلة السيولة) ترجع من Finnhub بقيمة timestamp
    غير متسقة مع بقية السوق (أحياناً متأخرة، ونادراً متقدمة بشكل غير متوقع).
  - الاعتماد على max() بين آلاف القيم يجعل قيمة شاذة واحدة (outlier) من سهم
    واحد كافية لإفساد "تاريخ اليوم" للملف بأكمله.
  - النتيجة الفعلية: تشغيل يوم الخميس 27/8 حسب تاريخاً خاطئاً (28/8) وكتب
    الملف باسم غلط، ثم تشغيل الجمعة 28/8 "صحّح" التسمية بطريقة خاطئة أدت
    لضياع بيانات الخميس الحقيقية بالكامل تحت اسم يوم آخر.

الحل المعتمد الآن: نحسب "تاريخ التداول" بأنفسنا من وقت تشغيل السكربت نفسه
(وقت التشغيل الفعلي في UTC، محوّلاً لتوقيت أمريكا/نيويورك)، وليس من بيانات
API خارجية غير متجانسة. نتحقق أيضاً أن هذا اليوم فعلاً يوم تداول (وليس
سبت/أحد أو عطلة رسمية معروفة)، ونرفض التنفيذ بوضوح بدل الكتابة فوق ملف
بتاريخ خاطئ إن لم يكن كذلك.
===============================================================================
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

# عطلات NASDAQ الرسمية لعام 2026 (بالإضافة لأيام السبت والأحد، التي تُستبعد
# تلقائياً). التحقق من هذه القائمة يمنع تشغيل السكربت بالخطأ في يوم لا يوجد
# فيه تداول فعلي، وبالتالي يمنع تسجيل بيانات إغلاق سابقة تحت تاريخ خاطئ.
# المصدر: NYSE/NASDAQ Holiday Calendar 2026 (تم التحقق: أغسطس 2026).
# يُرجى تحديث هذه القائمة في بداية كل سنة جديدة.
NASDAQ_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day
}


def load_json_file(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"الملف المطلوب غير موجود: {filepath}.")

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_market_date():
    """
    يحدد تاريخ التداول بالاعتماد على وقت تشغيل السكربت الفعلي، محوّلاً لتوقيت
    نيويورك (توقيت سوق NASDAQ). هذا أسلوب موثوق وثابت، بعكس الاعتماد على
    timestamp قادم من بيانات Finnhub لكل سهم على حدة (انظر الشرح أعلى الملف).

    يرفض (raise) إن كان اليوم الحالي بتوقيت نيويورك ليس يوم تداول (سبت/أحد
    أو عطلة رسمية)، بدل أن يكتب بصمت بيانات قديمة تحت تاريخ خاطئ.
    """
    now_ny = datetime.now(NY_TZ)
    date_str = now_ny.strftime("%Y-%m-%d")
    weekday = now_ny.weekday()  # الاثنين=0 ... الأحد=6

    if weekday >= 5:
        raise RuntimeError(
            f"تاريخ اليوم {date_str} بتوقيت نيويورك هو عطلة أسبوعية "
            f"(يوم {'السبت' if weekday == 5 else 'الأحد'}). لا يوجد تداول، "
            f"لن يُكتب أي ملف لتجنب تسجيل بيانات قديمة بتاريخ خاطئ."
        )

    if date_str in NASDAQ_HOLIDAYS_2026:
        raise RuntimeError(
            f"تاريخ اليوم {date_str} هو عطلة NASDAQ رسمية معروفة. لا يوجد "
            f"تداول، لن يُكتب أي ملف لتجنب تسجيل بيانات قديمة بتاريخ خاطئ."
        )

    return date_str


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

    # حماية إضافية: إن وُجد ملف بنفس التاريخ مسبقاً وعدد سجلاته مختلف بشكل
    # كبير عن الملف الجديد (مؤشر محتمل على بيانات يومين مختلفين تحت نفس
    # الاسم)، نطبع تحذيراً واضحاً بدل الاستبدال الصامت.
    if os.path.exists(filepath):
        try:
            existing = load_json_file(filepath)
            existing_count = existing.get("total_records", 0)
            new_count = len(merged_records)
            if existing_count and abs(existing_count - new_count) > (existing_count * 0.05):
                print(
                    f"تحذير: الملف {filepath} موجود مسبقاً بعدد سجلات مختلف "
                    f"بشكل ملحوظ (قديم: {existing_count}, جديد: {new_count}). "
                    f"سيتم الاستبدال، لكن يُنصح بمراجعة السبب يدوياً."
                )
        except (json.JSONDecodeError, KeyError):
            pass

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

    # يُحسب أولاً ويُتحقق منه قبل أي عملية جلب أو دمج، حتى نفشل بوضوح ومبكراً
    # إن كان اليوم ليس يوم تداول، بدل هدر وقت التشغيل ثم كتابة بيانات خاطئة.
    market_date = get_market_date()
    print(f"تاريخ التداول المُحدد (بتوقيت نيويورك): {market_date}")

    symbols_data = load_json_file(SYMBOLS_FILE)["symbols"]
    ohlc_data = load_json_file(OHLC_FILE)["ohlc_data"]

    merged_records = merge_data(symbols_data, ohlc_data, market_date)

    daily_output = save_daily_file(merged_records, market_date)
    update_latest_file(daily_output)

    cleanup_old_files()

    print("اكتملت عملية الدمج والتنظيف بنجاح.")


if __name__ == "__main__":
    main()
