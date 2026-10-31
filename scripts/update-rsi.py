"""
update-rsi.py — يعمل داخل daily-collection.yml بعد merge-and-cleanup.py
يقرأ بيانات اليوم الجاهزة (latest.json + ohlc-data-today.json) — لا طلبات Finnhub إضافية —
يدمجها في rsi-state.json، يحسب RSI الجديد، ثم يرسل التنبيهات + ملف القائمة إلى تيليجرام.
أي أيام ناقصة في daily-data (بعد آخر يوم في الحالة) تُدمج تلقائياً بالترتيب (سدّ الفجوات).
"""
import os, sys, json, glob, datetime as dt
import requests
from rsi_engine import advance, N, norm_symbol, zone
from rsi_report import rows, write_alerts_xlsx, write_list_xlsx
from alert_common import run_engine, COLS
import pandas as pd

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

def load_universe(files):
    """أسهم المستودع = NASDAQ + NYSE: من symbols-reference.json؛ وإن تعذّر فمن أحدث ملف في daily-data."""
    try:
        sy = json.load(open(P('symbols-reference.json'), encoding='utf-8'))['symbols']
        u = {norm_symbol(x['symbol']) for x in sy if x.get('symbol')}
        if len(u) > 1000: return u, 'symbols-reference.json'
    except Exception as e:
        print('تعذّر قراءة symbols-reference.json:', e)
    fs = sorted(glob.glob(os.path.join(DAILY, '*.json')))
    if fs:
        d = json.load(open(fs[-1], encoding='utf-8'))
        return {norm_symbol(x['symbol']) for x in d['records'] if x.get('symbol')}, os.path.basename(fs[-1])
    return None, 'غير متاح'

def tg_text(text):
    if not TOK: print(text); return
    for i in range(0, len(text), 3800):
        requests.post(f'https://api.telegram.org/bot{TOK}/sendMessage', data={'chat_id': CHAT, 'text': text[i:i + 3800]}, timeout=30)

def tg_file(path, caption):
    if not TOK: print('[ملف]', path); return
    with open(path, 'rb') as f:
        requests.post(f'https://api.telegram.org/bot{TOK}/sendDocument', data={'chat_id': CHAT, 'caption': caption[:1000]}, files={'document': f}, timeout=120)

# كل قسم له محرك مستقل (ملف alert_sN.py). إن فشل أحدها لا يتأثر الباقي، ويظهر اسمه في الملف والتعليق.
ENGINES = [('alert_s1', '1_تنبيه_اقل_من_5', '<5$'), ('alert_s2', '2_تنبيه_5_الى_10', '5-10$'), ('alert_s3', '3_تنبيه_10_الى_20', '10-20$'),
           ('alert_s4', '4_تنبيه_20_الى_50', '20-50$'), ('alert_s5', '5_تنبيه_50_الى_100', '50-100$'),
           ('alert_s6', '6_تنبيه_100_الى_300', '100-300$'), ('alert_s7', '7_تنبيه_300_فاكثر', '300$+')]

def run_all_engines(state, universe, last):
    results, failed = [], []
    for mod, name, tier in ENGINES:
        try:
            n, t, df = run_engine(mod, state, universe, last); results.append((n, t, df, None))
        except Exception as e:
            err = f'{mod}.py — {type(e).__name__}: {e}'; failed.append(err)
            results.append((name, tier, pd.DataFrame(columns=COLS), err))
    return results, failed

def months_between(a, b):   # 'YYYY-MM'
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])

def main():
    state = json.load(open(STATE, encoding='utf-8')); meta = state.pop('__meta__', {})
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
    last = max(s['last_date'] for s in state.values())
    # القائمة المركزية: شهرياً (أول تشغيل في الشهر) أو عند الطلب SEND_LIST=1. أول تشغيل بلا سجل لا يرسلها (يُفترض أنها وصلتك).
    every = int(os.environ.get('LIST_EVERY_MONTHS', '1')); month = last[:7]
    send_list = os.environ.get('SEND_LIST') == '1' or ('list_month' in meta and months_between(meta['list_month'], month) >= every)
    if send_list or 'list_month' not in meta: meta['list_month'] = month
    universe, src = load_universe(files)
    live = {t: s for t, s in state.items() if 'pending' not in s}
    print(f'نطاق التنبيهات: {len(universe) if universe else "الكل"} سهم من {src} | أسهم القائمة داخله: {sum(1 for t in state if universe and t in universe)}')
    no_zone = sum(1 for t, s in live.items() if (not universe or t in universe) and zone(s)[0] is None)
    print(f'أسهم داخل النطاق بلا منطقة صفراء (تُقيَّم بمنطقة تقديرية في الأقسام 4–7): {no_zone}')
    results, failed = run_all_engines(state, universe, last)
    save = dict(state); save['__meta__'] = meta
    json.dump(save, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    out = P('rsi-alerts.xlsx'); cnt = write_alerts_xlsx(out, results)
    total = sum(v for v in cnt.values() if v)
    line = ' | '.join(f'{k}: {"❌" if v is None else v}' for k, v in cnt.items())
    caption = f'📊 تنبيهات RSI — {last}\nالمجموع: {total} (NASDAQ + NYSE)\n{line}'
    if failed: caption += '\n❌ محركات فشلت:\n' + '\n'.join(failed)
    print(caption)
    if total or failed: tg_file(out, caption)
    else: tg_text(f'📊 RSI {last} — لا توجد تنبيهات اليوم.')
    if send_list:
        df = rows(state); lst = P('rsi-list.xlsx'); write_list_xlsx(lst, df)
        tg_file(lst, f'📋 القائمة المركزية — {last} ({len(df):,} سهم، كل السوق الأمريكي)')

if __name__ == '__main__': main()
