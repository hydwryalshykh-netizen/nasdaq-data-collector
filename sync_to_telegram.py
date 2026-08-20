#!/usr/bin/env python3
"""
سكربت أرشفة بيانات NASDAQ إلى تلغرام - أرشيف سنوي.

المنطق:
- يقرأ كل ملفات daily-data/*.json من المستودع
- يجمّعها حسب السنة → ملف منفصل لكل سنة
- لكل سنة: يرسل ملفها لتلغرام (أول مرة) أو يحدّث نفس الرسالة (المرات التالية)
- السنة الحالية تتحدث يومياً، السنوات المنتهية لا تُعاد إرسالها إذا لم تتغير
- يحفظ message_id لكل سنة في telegram-ids.json بالمستودع
"""

import gzip
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

import requests

# ── إعدادات ──────────────────────────────────────────────
REPO_ROOT      = Path(__file__).resolve().parent
DAILY_DATA_DIR = REPO_ROOT / "daily-data"
ARCHIVE_DIR    = REPO_ROOT / "yearly-archive"   # مجلد مؤقت للملفات المضغوطة (لا يُرفع لـ git)
IDS_FILE       = REPO_ROOT / "telegram-ids.json" # يحفظ message_id لكل سنة
HASHES_FILE    = REPO_ROOT / "telegram-hashes.json" # يحفظ hash لكل سنة (لتجنب إعادة الإرسال)

BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_BYTES      = 49 * 1024 * 1024   # 49 ميجا حد أمان

ARCHIVE_DIR.mkdir(exist_ok=True)


# ── قراءة البيانات ────────────────────────────────────────
def load_all_daily_files() -> dict[str, dict]:
    """يقرأ كل ملفات daily-data/*.json → {date: data}"""
    if not DAILY_DATA_DIR.exists():
        print(f"❌ المجلد غير موجود: {DAILY_DATA_DIR}", file=sys.stderr)
        sys.exit(1)

    all_data = {}
    files = sorted(DAILY_DATA_DIR.glob("*.json"))
    if not files:
        print("❌ لا توجد ملفات JSON في daily-data/", file=sys.stderr)
        sys.exit(1)

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                all_data[fp.stem] = json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️  تجاهل ملف تالف {fp.name}: {e}")

    print(f"✅ قُرئ {len(all_data)} يوم")
    return all_data


def group_by_year(all_data: dict) -> dict[str, dict]:
    """يجمّع البيانات حسب السنة → {year: {date: data}}"""
    by_year = defaultdict(dict)
    for date_key, data in all_data.items():
        year = date_key[:4]
        by_year[year][date_key] = data
    return dict(sorted(by_year.items()))


# ── ضغط ومعالجة ──────────────────────────────────────────
def make_gz(year: str, year_data: dict) -> Path:
    """يصنع ملف YYYY.json.gz مضغوط ويرجع مساره."""
    gz_path = ARCHIVE_DIR / f"{year}.json.gz"
    sorted_data = dict(sorted(year_data.items()))
    raw = json.dumps(sorted_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(gz_path, "wb", compresslevel=9) as f:
        f.write(raw)
    size_mb = gz_path.stat().st_size / 1024 / 1024
    days = len(sorted_data)
    print(f"🗜️  {year}.json.gz → {size_mb:.2f} MB ({days} يوم)")
    if gz_path.stat().st_size > MAX_BYTES:
        print(f"❌ {year}.json.gz ({size_mb:.1f} MB) أكبر من حد تلغرام 49 MB!", file=sys.stderr)
        sys.exit(1)
    return gz_path


def file_hash(path: Path) -> str:
    """هاش بسيط للملف لمعرفة إذا تغيّر."""
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest()


# ── تلغرام ───────────────────────────────────────────────
def load_ids() -> dict:
    if IDS_FILE.exists():
        try:
            return json.loads(IDS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_ids(ids: dict) -> None:
    IDS_FILE.write_text(json.dumps(ids, indent=2, ensure_ascii=False))


def load_hashes() -> dict:
    if HASHES_FILE.exists():
        try:
            return json.loads(HASHES_FILE.read_text())
        except Exception:
            pass
    return {}


def save_hashes(hashes: dict) -> None:
    HASHES_FILE.write_text(json.dumps(hashes, indent=2, ensure_ascii=False))


def send_file(gz_path: Path, year: str) -> int:
    """يرسل ملف جديد لتلغرام ويرجع message_id."""
    url = f"{TELEGRAM_API}/sendDocument"
    caption = f"📦 أرشيف NASDAQ سنة {year} — {gz_path.stat().st_size//1024} KB"
    with open(gz_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"document": (gz_path.name, f, "application/gzip")},
            timeout=120,
        )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        print(f"❌ فشل الإرسال: {result}", file=sys.stderr)
        sys.exit(1)
    mid = result["result"]["message_id"]
    print(f"✅ أُرسل {year}.json.gz كرسالة جديدة (message_id={mid})")
    return mid


def update_file(gz_path: Path, year: str, message_id: int) -> bool:
    """يحدّث نفس الرسالة القديمة بالملف الجديد."""
    url = f"{TELEGRAM_API}/editMessageMedia"
    caption = f"📦 أرشيف NASDAQ سنة {year} — {gz_path.stat().st_size//1024} KB (آخر تحديث)"
    media = json.dumps({
        "type": "document",
        "media": "attach://document",
        "caption": caption,
    })
    with open(gz_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "message_id": message_id, "media": media},
            files={"document": (gz_path.name, f, "application/gzip")},
            timeout=120,
        )
    result = resp.json()
    if result.get("ok"):
        print(f"✅ حُدِّثت رسالة {year} (message_id={message_id})")
        return True
    print(f"⚠️  فشل تحديث رسالة {year}: {result.get('description')}")
    return False


# ── الرئيسي ──────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ لازم تحط TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    all_data  = load_all_daily_files()
    by_year   = group_by_year(all_data)
    ids       = load_ids()
    hashes    = load_hashes()
    ids_dirty = False
    hsh_dirty = False

    import datetime
    current_year = str(datetime.date.today().year)

    for year, year_data in by_year.items():
        gz_path   = make_gz(year, year_data)
        new_hash  = file_hash(gz_path)
        old_hash  = hashes.get(year)
        is_current = (year == current_year)

        # السنوات المنتهية: نرسلها مرة واحدة فقط ولا نعيد الإرسال
        # السنة الحالية: نحدّثها كل مرة يتغير الهاش (= يوم جديد أضيف)
        if old_hash == new_hash and not is_current:
            print(f"⏭️  {year}: لم تتغير، تخطي الإرسال")
            continue

        if old_hash == new_hash and is_current:
            print(f"⏭️  {year} (سنة حالية): لا يوجد تغيير اليوم، تخطي")
            continue

        message_id = ids.get(year)

        if message_id is None:
            # إرسال جديد
            mid = send_file(gz_path, year)
            ids[year]    = mid
            ids_dirty    = True
        else:
            # تحديث الرسالة الموجودة
            success = update_file(gz_path, year, message_id)
            if not success:
                print(f"↪️  إرسال رسالة جديدة بدلاً من التحديث الفاشل لـ {year}...")
                mid = send_file(gz_path, year)
                ids[year] = mid
                ids_dirty = True

        hashes[year] = new_hash
        hsh_dirty    = True

    if ids_dirty:
        save_ids(ids)
        print("💾 تم حفظ telegram-ids.json")

    if hsh_dirty:
        save_hashes(hashes)
        print("💾 تم حفظ telegram-hashes.json")

    print("🎉 انتهى!")


if __name__ == "__main__":
    main()
