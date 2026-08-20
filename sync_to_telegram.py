#!/usr/bin/env python3
"""
سكربت دمج بيانات NASDAQ اليومية وإرسالها/تحديثها في بوت تلغرام.

الفكرة:
1. يقرأ كل ملفات daily-data/*.json من المستودع (محليًا، لأن الـ workflow
   يعمل checkout للمستودع قبل تشغيل هذا السكربت).
2. يدمجها في ملف واحد dict مرتب حسب التاريخ: merged-data.json
3. يقارن مع النسخة القديمة من merged-data.json (لو موجودة) عشان يعرف
   هل فيه تاريخ جديد فعلاً قبل ما يرسل شي لتلغرام (تقليل الرسائل الزايدة).
4. يرسل/يحدّث ملف واحد ثابت في تلغرام:
   - أول مرة: يرسل الملف برسالة جديدة ويخزن message_id في telegram-message-id.txt
   - المرات اللي بعدها: يستخدم editMessageMedia على نفس message_id
     (تحديث نفس الرسالة بدل إرسال رسالة جديدة).
"""

import json
import os
import sys
from pathlib import Path

import requests

# ---------- الإعدادات ----------
REPO_ROOT = Path(__file__).resolve().parent
DAILY_DATA_DIR = REPO_ROOT / "daily-data"
MERGED_FILE = REPO_ROOT / "merged-data.json"
MESSAGE_ID_FILE = REPO_ROOT / "telegram-message-id.txt"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_all_daily_files() -> dict:
    """يقرأ كل ملفات daily-data/*.json ويرجعها كـ dict {date: data}."""
    if not DAILY_DATA_DIR.exists():
        print(f"❌ المجلد غير موجود: {DAILY_DATA_DIR}", file=sys.stderr)
        sys.exit(1)

    merged = {}
    json_files = sorted(DAILY_DATA_DIR.glob("*.json"))

    if not json_files:
        print("❌ لا توجد ملفات JSON في daily-data/", file=sys.stderr)
        sys.exit(1)

    for file_path in json_files:
        date_key = file_path.stem  # مثال: 2024-08-19
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                merged[date_key] = json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️  تجاهل ملف تالف {file_path.name}: {e}", file=sys.stderr)
            continue

    print(f"✅ تم قراءة {len(merged)} ملف يومي")
    return merged


def load_previous_merged() -> dict | None:
    """يقرأ النسخة القديمة من الملف المدموج إذا كانت موجودة."""
    if MERGED_FILE.exists():
        try:
            with open(MERGED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None
    return None


def save_merged(merged: dict) -> None:
    """يحفظ الملف المدموج مرتبًا حسب التاريخ."""
    sorted_merged = dict(sorted(merged.items()))
    with open(MERGED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_merged, f, ensure_ascii=False, indent=2)
    print(f"💾 تم حفظ {MERGED_FILE.name} ({len(sorted_merged)} يوم)")


def get_new_dates(old: dict | None, new: dict) -> list[str]:
    """يرجع قائمة التواريخ الجديدة التي لم تكن موجودة سابقًا."""
    if old is None:
        return list(new.keys())
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    return sorted(new_keys - old_keys)


def get_saved_message_id() -> int | None:
    if MESSAGE_ID_FILE.exists():
        content = MESSAGE_ID_FILE.read_text(encoding="utf-8").strip()
        if content.isdigit():
            return int(content)
    return None


def save_message_id(message_id: int) -> None:
    MESSAGE_ID_FILE.write_text(str(message_id), encoding="utf-8")


def send_new_file_to_telegram(file_path: Path) -> int:
    """يرسل الملف كرسالة جديدة ويرجع message_id."""
    url = f"{TELEGRAM_API}/sendDocument"
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": "📊 بيانات NASDAQ المجمّعة (تحديث تلقائي يومي)",
            },
            files={"document": (file_path.name, f, "application/json")},
            timeout=60,
        )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        print(f"❌ فشل إرسال الملف: {result}", file=sys.stderr)
        sys.exit(1)
    message_id = result["result"]["message_id"]
    print(f"✅ تم إرسال رسالة جديدة، message_id={message_id}")
    return message_id


def update_existing_message(file_path: Path, message_id: int) -> bool:
    """
    يحاول تحديث نفس الرسالة القديمة بالملف الجديد.
    يرجع True لو نجح، False لو فشل (مثلاً الرسالة انحذفت أو قديمة جدًا).
    """
    url = f"{TELEGRAM_API}/editMessageMedia"
    media = {
        "type": "document",
        "media": "attach://document",
        "caption": "📊 بيانات NASDAQ المجمّعة (آخر تحديث يومي)",
    }
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "message_id": message_id,
                "media": json.dumps(media),
            },
            files={"document": (file_path.name, f, "application/json")},
            timeout=60,
        )
    result = resp.json()
    if result.get("ok"):
        print(f"✅ تم تحديث الرسالة الموجودة (message_id={message_id})")
        return True
    else:
        print(f"⚠️  تعذر تحديث الرسالة القديمة: {result.get('description')}", file=sys.stderr)
        return False


def send_text_notification(text: str) -> None:
    """رسالة نصية بسيطة (مثلاً لو ما فيه تحديثات جديدة، أو لتنبيه بالأخطاء)."""
    url = f"{TELEGRAM_API}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=30)


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ لازم تحط TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID كمتغيرات بيئة", file=sys.stderr)
        sys.exit(1)

    old_merged = load_previous_merged()
    new_merged = load_all_daily_files()
    new_dates = get_new_dates(old_merged, new_merged)

    save_merged(new_merged)

    if old_merged is not None and not new_dates:
        print("ℹ️  لا يوجد تاريخ جديد اليوم، لن يتم إرسال شيء لتلغرام.")
        return

    message_id = get_saved_message_id()

    if message_id is None:
        # أول مرة نرسل فيها
        new_id = send_new_file_to_telegram(MERGED_FILE)
        save_message_id(new_id)
    else:
        # نحاول نحدث نفس الرسالة
        success = update_existing_message(MERGED_FILE, message_id)
        if not success:
            # لو فشل (مثلاً الرسالة انحذفت يدويًا)، نرسل رسالة جديدة ونحدث الـ id
            print("↪️  إرسال رسالة جديدة بدل التحديث الفاشل...")
            new_id = send_new_file_to_telegram(MERGED_FILE)
            save_message_id(new_id)

    if new_dates:
        dates_preview = ", ".join(new_dates[:5])
        more = f" و {len(new_dates) - 5} غيرها" if len(new_dates) > 5 else ""
        print(f"📅 تواريخ جديدة أضيفت: {dates_preview}{more}")


if __name__ == "__main__":
    main()
