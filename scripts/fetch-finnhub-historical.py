"""
fetch-finnhub-historical.py
===========================

الوظيفة:
جلب يوم تاريخي واحد من بيانات NASDAQ من Finnhub.

طريقة العمل:
- يستخدم نفس symbols-reference.json الموجود في النظام الحالي.
- طلب واحد لكل رمز.
- تأخير 1.05 ثانية بين الطلبات.
- حد أقصى فعلي يقارب 57 طلب/دقيقة.
- يبدأ من 2026-08-07 لأن 2026-08-08 موجود بالفعل.
- يرجع يومًا واحدًا في كل تشغيل.
- يتوقف عند 2026-01-01.
- لا يلمس latest.json.
- لا يستخدم merge-and-cleanup.py.
- لا يغير الـDaily Workflow الحالي.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests


# ============================================================
# الإعدادات
# ============================================================

FINNHUB_URL = "https://finnhub.io/api/v1/stock/candle"

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")

SYMBOLS_FILE = "symbols-reference.json"

DAILY_DATA_DIR = "daily-data"

PROGRESS_FILE = "historical-progress.json"

START_DATE = datetime.strptime(
    "2026-08-07",
    "%Y-%m-%d"
).date()

END_DATE = datetime.strptime(
    "2026-01-01",
    "%Y-%m-%d"
).date()

DELAY_BETWEEN_REQUESTS_SECONDS = 1.05

REQUEST_TIMEOUT_SECONDS = 15

NEW_YORK_TZ = ZoneInfo("America/New_York")


# ============================================================
# أيام العطل الأمريكية في الفترة المطلوبة
# ============================================================

US_MARKET_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day observed
}


# ============================================================
# قراءة الرموز
# ============================================================

def load_symbols():
    if not os.path.exists(SYMBOLS_FILE):
        raise FileNotFoundError(
            f"لم يُعثر على {SYMBOLS_FILE}"
        )

    with open(
        SYMBOLS_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    symbols = data.get("symbols", [])

    if not symbols:
        raise RuntimeError(
            "قائمة الرموز فارغة."
        )

    return symbols


# ============================================================
# إدارة التاريخ الحالي
# ============================================================

def load_progress():
    """
    يقرأ آخر تاريخ تمت معالجته.

    إذا لم يوجد الملف:
    يبدأ من 2026-08-07 لأن 2026-08-08 موجود أصلًا.
    """

    if not os.path.exists(PROGRESS_FILE):
        return START_DATE

    try:
        with open(
            PROGRESS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        next_date = data.get("next_date")

        if not next_date:
            return START_DATE

        return datetime.strptime(
            next_date,
            "%Y-%m-%d"
        ).date()

    except Exception:
        print(
            "تحذير: تعذر قراءة ملف التقدم، "
            "سيبدأ من 2026-08-07."
        )

        return START_DATE


def save_progress(next_date):
    data = {
        "next_date": next_date.isoformat(),
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    with open(
        PROGRESS_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# هل اليوم يوم تداول؟
# ============================================================

def is_non_trading_day(target_date):
    date_string = target_date.isoformat()

    # السبت والأحد
    if target_date.weekday() >= 5:
        return True

    # العطل الرسمية
    if date_string in US_MARKET_HOLIDAYS_2026:
        return True

    return False


def previous_date(target_date):
    return target_date - timedelta(days=1)


# ============================================================
# تحويل التاريخ إلى Unix timestamp
# ============================================================

def get_day_timestamps(target_date):
    """
    نستخدم توقيت New York حتى نلتقط شمعة يوم السوق
    الصحيحة.
    """

    start_local = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        0,
        tzinfo=NEW_YORK_TZ
    )

    end_local = start_local + timedelta(days=1)

    start_timestamp = int(
        start_local.timestamp()
    )

    end_timestamp = int(
        end_local.timestamp()
    )

    return start_timestamp, end_timestamp


# ============================================================
# جلب البيانات التاريخية لسهم واحد
# ============================================================

def fetch_historical_for_symbol(
    symbol,
    target_date
):
    from_timestamp, to_timestamp = (
        get_day_timestamps(target_date)
    )

    params = {
        "symbol": symbol,
        "resolution": "D",
        "from": from_timestamp,
        "to": to_timestamp,
        "token": FINNHUB_API_KEY,
    }

    try:

        response = requests.get(
            FINNHUB_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        # ====================================================
        # تجاوز حد الطلبات
        # ====================================================

        if response.status_code == 429:

            print(
                f"تجاوز الحد عند {symbol}. "
                f"الانتظار 60 ثانية..."
            )

            time.sleep(60)

            response = requests.get(
                FINNHUB_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS
            )

        # ====================================================
        # صلاحية الـAPI
        # ====================================================

        if response.status_code == 401:
            raise RuntimeError(
                "مفتاح FINNHUB_API_KEY غير صالح."
            )

        if response.status_code == 403:
            raise RuntimeError(
                "Finnhub رفض الوصول إلى "
                "Historical Stock Candles (403). "
                "هذا يعني أن مفتاح API الحالي لا يملك "
                "صلاحية Historical Candles."
            )

        # ====================================================
        # أخطاء أخرى
        # ====================================================

        if response.status_code != 200:

            print(
                f"تحذير: فشل {symbol} — "
                f"HTTP {response.status_code}"
            )

            return None

        data = response.json()

        if data.get("s") != "ok":
            return None

        timestamps = data.get("t", [])
        opens = data.get("o", [])
        highs = data.get("h", [])
        lows = data.get("l", [])
        closes = data.get("c", [])
        volumes = data.get("v", [])

        if not timestamps:
            return None

        # ====================================================
        # البحث عن شمعة اليوم المطلوب
        # ====================================================

        for i, timestamp in enumerate(timestamps):

            candle_datetime = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc
            ).astimezone(NEW_YORK_TZ)

            candle_date = (
                candle_datetime.date()
            )

            if candle_date != target_date:
                continue

            return {
                "open": opens[i] if i < len(opens) else None,
                "high": highs[i] if i < len(highs) else None,
                "low": lows[i] if i < len(lows) else None,
                "close": closes[i] if i < len(closes) else None,
                "volume": volumes[i] if i < len(volumes) else None,
            }

        return None

    except requests.exceptions.RequestException as e:

        print(
            f"تحذير: خطأ اتصال عند {symbol}: {e}"
        )

        return None


# ============================================================
# بناء ملف اليوم
# ============================================================

def save_daily_file(
    target_date,
    records
):

    os.makedirs(
        DAILY_DATA_DIR,
        exist_ok=True
    )

    date_string = target_date.isoformat()

    filepath = os.path.join(
        DAILY_DATA_DIR,
        f"{date_string}.json"
    )

    # لا نستبدل ملفًا موجودًا
    if os.path.exists(filepath):

        print(
            f"الملف موجود مسبقًا: {filepath}"
        )

        return False

    output = {
        "date": date_string,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "total_records": len(records),
        "records": records,
    }

    with open(
        filepath,
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
        f"تم حفظ {filepath} "
        f"({len(records)} سجل)"
    )

    return True


# ============================================================
# البرنامج الرئيسي
# ============================================================

def main():

    if not FINNHUB_API_KEY:

        raise EnvironmentError(
            "متغير FINNHUB_API_KEY غير موجود."
        )

    symbols_data = load_symbols()

    total_symbols = len(symbols_data)

    target_date = load_progress()

    print("=" * 70)
    print(
        f"بدء جلب البيانات التاريخية: "
        f"{target_date}"
    )
    print(
        f"عدد الرموز: {total_symbols}"
    )
    print("=" * 70)

    # ========================================================
    # انتهاء الفترة
    # ========================================================

    if target_date < END_DATE:

        print(
            "اكتملت الفترة التاريخية المطلوبة "
            "حتى 2026-01-01."
        )

        return

    # ========================================================
    # إذا كان اليوم عطلة أو عطلة أسبوعية
    # ========================================================

    if is_non_trading_day(target_date):

        print(
            f"{target_date} ليس يوم تداول."
        )

        next_date = previous_date(
            target_date
        )

        save_progress(next_date)

        print(
            f"سيتم الانتقال إلى {next_date} "
            "في التشغيل القادم."
        )

        return

    # ========================================================
    # إذا كان الملف موجودًا
    # ========================================================

    filepath = os.path.join(
        DAILY_DATA_DIR,
        f"{target_date.isoformat()}.json"
    )

    if os.path.exists(filepath):

        print(
            f"البيانات موجودة مسبقًا "
            f"لليوم {target_date}."
        )

        next_date = previous_date(
            target_date
        )

        save_progress(next_date)

        return

    # ========================================================
    # الجلب
    # ========================================================

    records = []

    success_count = 0
    failed_count = 0

    started_at = datetime.now(
        timezone.utc
    )

    for index, symbol_entry in enumerate(
        symbols_data,
        start=1
    ):

        symbol = symbol_entry["symbol"]

        historical = (
            fetch_historical_for_symbol(
                symbol,
                target_date
            )
        )

        # ====================================================
        # نفس بنية daily-data الحالية
        # ====================================================

        if historical is not None:

            record = {
                "symbol": symbol,
                "date": target_date.isoformat(),

                "open": historical.get("open"),
                "high": historical.get("high"),
                "low": historical.get("low"),
                "close": historical.get("close"),
                "volume": historical.get("volume"),

                # بيانات المرجع الحالية
                "market_cap": symbol_entry.get(
                    "market_cap"
                ),

                "sector": symbol_entry.get(
                    "sector"
                ),

                "industry": symbol_entry.get(
                    "industry"
                ),

                "name": symbol_entry.get(
                    "name"
                ),
            }

            records.append(record)

            success_count += 1

        else:

            failed_count += 1

            # نحافظ على وجود السهم في ملف السوق
            record = {
                "symbol": symbol,
                "date": target_date.isoformat(),

                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": None,

                "market_cap": symbol_entry.get(
                    "market_cap"
                ),

                "sector": symbol_entry.get(
                    "sector"
                ),

                "industry": symbol_entry.get(
                    "industry"
                ),

                "name": symbol_entry.get(
                    "name"
                ),
            }

            records.append(record)

        # ====================================================
        # التقدم
        # ====================================================

        if index % 100 == 0:

            elapsed = (
                datetime.now(
                    timezone.utc
                ) - started_at
            ).total_seconds()

            print(
                f"[{datetime.now().isoformat()}] "
                f"{index}/{total_symbols} | "
                f"نجح: {success_count} | "
                f"فشل/لا بيانات: {failed_count} | "
                f"الوقت: {elapsed / 60:.1f} دقيقة"
            )

        # ====================================================
        # مهم جدًا:
        # 1.05 ثانية بين كل طلب
        # ====================================================

        time.sleep(
            DELAY_BETWEEN_REQUESTS_SECONDS
        )

    # ========================================================
    # فحص أمان
    # ========================================================

    if success_count == 0:

        raise RuntimeError(
            f"لم يتم الحصول على أي بيانات "
            f"لليوم {target_date}. "
            "لن يتم إنشاء ملف فارغ ولن يتم تحريك التقدم."
        )

    # ========================================================
    # حفظ اليوم
    # ========================================================

    save_daily_file(
        target_date,
        records
    )

    # ========================================================
    # الانتقال إلى اليوم السابق
    # ========================================================

    next_date = previous_date(
        target_date
    )

    save_progress(
        next_date
    )

    print("=" * 70)
    print(
        f"اكتمل يوم {target_date}"
    )
    print(
        f"نجح: {success_count}"
    )
    print(
        f"فشل/لا بيانات: {failed_count}"
    )
    print(
        f"اليوم القادم: {next_date}"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
