"""
update-rsi.py — يعمل داخل daily-collection.yml بعد merge-and-cleanup.py
يقرأ بيانات اليوم الجاهزة (latest.json + ohlc-data-today.json) — لا طلبات Finnhub إضافية —
يدمجها في rsi-state.json، يحسب RSI الجديد، ثم يرسل التنبيهات + ملف القائمة إلى تيليجرام.
أي أيام ناقصة في daily-data (بعد آخر يوم في الحالة) تُدمج تلقائياً بالترتيب (سدّ الفجوات).
"""
import os, sys, json, glob, datetime as dt
import requests
from rsi_engine import advance, N, norm_symbol
from rsi_report import rows, alerts, write_xlsx

ROOT = os.environ.get('REPO_ROOT', '.')
P = lambda *a: os.path.join(ROOT, *a)
STATE = P('rsi-state.json'); DAILY = P('daily-data'); OHLC = P('ohlc-data-today.json')
TOK = os.environ.get('TELEGRAM_BOT_TOKEN', ''); CHAT = os.environ.get('TELEGRAM_CHAT_ID', '')

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def traded(rec, s):
    c = num(rec.get('close'))
    if not c or c <= 0: return False
    v = num(rec.get('volume')); h = num(rec.get('high')); l = num(rec.get('low'))
    return bool((v and v > 0) or (h is not None and l is not None and h != l) or (s is not None and c != s['last_close']))

def new_state(rec, date):
    c = float(rec['close']); v = num(rec.get('volume')) or 0.0
    return {'last_close': c, 'last_date': date, 'median_volume': v, 'dv10': [c * v] if v else [], 'splits': [], 'pending': [c]}

def apply_day(state, date, records, pcs=None):
    n = new = 0
    for rec in records:
        sym = norm_symbol(rec['symbol']) if rec.get('symbol') else None; s = state.get(sym)
        if not sym or not traded(rec, s): continue
        c = float(rec['close']); v = num(rec.get('volume'))
        if s is None:
            state[sym] = new_state(rec, date); new += 1; continue
        if advance(s, c, date, v, (pcs or {}).get(sym)): n += 1
    return n, new

def tg_text(text):
    if not TOK: print(text); return
    for i in range(0, len(text), 3800):
        requests.post(f'https://api.telegram.org/bot{TOK}/sendMessage', data={'chat_id': CHAT, 'text': text[i:i + 3800]}, timeout=30)

def tg_file(path, caption):
    if not TOK: print('[ملف]', path); return
    with open(path, 'rb') as f:
        requests.post(f'https://api.telegram.org/bot{TOK}/sendDocument', data={'chat_id': CHAT, 'caption': caption[:1000]}, files={'document': f}, timeout=120)

def main():
    state = json.load(open(STATE, encoding='utf-8'))
    base = max(s['last_date'] for s in state.values())
    files = sorted(f for f in glob.glob(os.path.join(DAILY, '*.json')) if os.path.basename(f)[:-5] > base)
    print(f'آخر يوم في الحالة: {base} | أيام جديدة للدمج: {len(files)}')
    pcs = {}
    if os.path.exists(OHLC):
        o = json.load(open(OHLC, encoding='utf-8')).get('ohlc_data', {})
        pcs = {norm_symbol(k): v.get('previous_close') for k, v in o.items() if v.get('previous_close')}
    for i, f in enumerate(files):
        d = json.load(open(f, encoding='utf-8')); date = d['date']
        n, new = apply_day(state, date, d['records'], pcs if i == len(files) - 1 else None)   # previous_close متاح ليوم التشغيل فقط
        print(f'  {date}: دُمج {n} سهم، جديد {new}')
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    last = max(s['last_date'] for s in state.values())
    df = rows(state); ad = alerts(state, last)
    out = P('rsi-list.xlsx'); write_xlsx(out, df, ad, last)
    lo = ad[ad['السعر'] >= 12] if len(ad) else ad; hi = ad[ad['السعر'] < 12] if len(ad) else ad
    def line(r): return f"{r['الرمز']}  ${r['السعر']}  RSI {r['RSI_الحالي']}  | {r['نوع_التنبيه']} | أحمر {r['القاع_الاحمر']} ، أصفر {r['الاصفر_الادنى']}-{r['الاصفر_الاعلى']}" + ('  ⚠️سيولة' if r['تنبيه_سيولة'] else '') + (f"  🔁{r['ملاحظة']}" if r['ملاحظة'] else '')
    msg = [f'📊 RSI — {last}  |  أسهم القائمة {len(df):,}  |  تنبيهات {len(ad)}']
    if len(lo): msg += ['', f'🟡 سعر ≥ 12$ — قبل الأصفر الأدنى ({len(lo)}):'] + [line(r) for _, r in lo.iterrows()]
    if len(hi): msg += ['', f'🔴 سعر < 12$ — قبل القاع الأحمر ({len(hi)}):'] + [line(r) for _, r in hi.iterrows()]
    if not len(ad): msg += ['', 'لا توجد تنبيهات اليوم.']
    tg_text('\n'.join(msg)); tg_file(out, f'قائمة RSI كاملة — {last}')

if __name__ == '__main__': main()
