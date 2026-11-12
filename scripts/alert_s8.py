"""
القسم 8 — قائمة منفردة دقيقة: كل الشركات بسعر ≥ 50$ ومتوسط حجم ≥ 1 مليون، RSI ≤ الأصفر الأدنى + 5.
محرك مستقل بالكامل، بلا ذاكرة مخفية: القائمة = من يحقق الشرط اليوم فقط، وكل صف يمكن فحصه من أعمدته.
  • لا يترك أسهماً: يشمل أسهم الحالة، ويضيف أسهماً غير موجودة في الحالة (إدراج حديث) من daily-data.
  • مصدران لـ RSI: (1) الحالة (2) إعادة حساب مستقلة من إغلاقات daily-data. يدخل السهم إن حقق الشرط أيٌّ منهما،
    وعمود «درجة_التأكيد» يبيّن: مؤكد (المصدران) / دقّق (مصدر واحد فقط) / مصدر واحد (التحقق غير ممكن).
  • متوسط الحجم = متوسط آخر 60 يوم تداول فعلية من daily-data. وتُكتشف الأيام الناقصة وتُذكر في تعليق تيليجرام.
"""
import glob, json, os, datetime as dt
import numpy as np
import pandas as pd
from rsi_engine import zone, norm_symbol

NAME = '8_دقيقة_50$_فاكثر_حجم_1M'; TIER = '50$+ حجم≥1M'
MIN_PRICE = 50.0
MIN_AVG_VOL = 1_000_000
NEAR = 5.0                 # RSI ≤ الأصفر الأدنى + 5
VOL_DAYS = 60
MAX_FILES = 320
MIN_BARS_VERIFY = 100      # شموع كافية ليتقارب RSI المستقل (Wilder) مع RSI الحالة
MIN_BARS_ANY = 15          # أقل شموع لحساب RSI لسهم غير موجود في الحالة
VERIFY_TOL = 1.0
DEFAULT_YL, DEFAULT_YH = 24.4, 33.9
HOLIDAYS = {'2025-07-04', '2025-09-01', '2025-11-27', '2025-12-25', '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03',
            '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25'}
COLS = ['الرمز', 'السعر', 'RSI_الحالي', 'نوع_التنبيه', 'المسافة_من_المرجع', 'القاع_الاحمر', 'الاصفر_الادنى', 'الاصفر_الاعلى',
        'متوسط_حجم_60_يوم', 'RSI_تحقق', 'فرق_التحقق', 'درجة_التأكيد', 'آخر_يوم_تداول', 'ملاحظة']
INFO = {}

def _rsi(closes, n=14):
    c = np.asarray(closes, float)
    if len(c) <= n: return None
    d = np.diff(c); g = np.where(d > 0, d, 0.0); l = np.where(d < 0, -d, 0.0)
    ag, al = float(g[:n].mean()), float(l[:n].mean())
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n; al = (al * (n - 1) + l[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

def _missing_days(dates):
    if not dates: return []
    ds = sorted(set(dates)); have = set(ds)
    a, b = dt.date.fromisoformat(ds[0]), dt.date.fromisoformat(ds[-1]); out = []; d = a
    while d <= b:
        s = d.isoformat()
        if d.weekday() < 5 and s not in HOLIDAYS and s not in have: out.append(s)
        d += dt.timedelta(days=1)
    recent_from = (b - dt.timedelta(days=90)).isoformat()
    return [x for x in out if x >= recent_from]

def _records(path):
    try: d = json.load(open(path, encoding='utf-8'))
    except Exception: return None, []
    return (d.get('date') or os.path.basename(path)[:-5]), d.get('records', [])

def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def run(state, universe, last_date, daily_dir):
    files = sorted(glob.glob(os.path.join(daily_dir, '*.json')))[-MAX_FILES:]
    # --- المرشَّحون: سعر ≥ 50$ — من الحالة، ومن أحدث ملف لأسهم غير موجودة/مؤقتة في الحالة (إدراج حديث)
    latest = {}
    if files:
        _, recs = _records(files[-1])
        for r in recs:
            c = _num(r.get('close'))
            if c and c > 0: latest[norm_symbol(r.get('symbol', ''))] = c
    inuni = lambda t: universe is None or t in universe
    cand = {t for t, s in state.items() if inuni(t) and s['last_close'] >= MIN_PRICE}
    cand |= {t for t, c in latest.items() if c >= MIN_PRICE and inuni(t) and (t not in state or 'pending' in state[t])}
    # --- نافذة daily-data للمرشَّحين فقط
    ser = {t: [] for t in cand}; dates = []
    for f in files:
        date, recs = _records(f)
        if date is None: continue
        dates.append(date)
        for r in recs:
            k = norm_symbol(r.get('symbol', ''))
            if k in ser:
                c = _num(r.get('close')); v = _num(r.get('volume')) or 0.0
                if c and c > 0: ser[k].append((date, c, v))
    missing = _missing_days(dates)
    INFO.clear(); INFO.update(missing_days=missing, files=len(dates), candidates=len(cand))
    rank = {'مؤكد (المصدران)': 0, 'مصدر واحد (التحقق غير ممكن)': 1, 'دقّق: مصدر واحد فقط يحقق الشرط': 2}
    rows = []
    for t in cand:
        s = state.get(t); mature = s is not None and 'pending' not in s and s.get('rsi') is not None
        rec = ser.get(t, []); notes = []
        price = s['last_close'] if s is not None else latest.get(t)
        if price is None or price < MIN_PRICE: continue
        # الحجم: متوسط آخر 60 يوم تداول فعلية
        vols = [v for _, _, v in rec if v > 0][-VOL_DAYS:]
        if len(vols) >= 20: avg_v = float(np.mean(vols))
        elif s is not None and s.get('median_volume'): avg_v = float(s['median_volume']); notes.append('الحجم من الحالة (daily-data أقل من 20 يوماً)')
        else: avg_v = float(np.mean(vols)) if vols else 0.0; notes.append(f'الحجم من {len(vols)} يوماً فقط')
        if avg_v < MIN_AVG_VOL: continue
        # الأصفر
        yl = yh = None
        if mature: yl, yh = zone(s)
        if yl is None: yl, yh = DEFAULT_YL, DEFAULT_YH; notes.append('منطقة صفراء تقديرية (أقل من 3 لمسات)' if mature else 'سهم حديث: منطقة صفراء تقديرية')
        top = yl + NEAR
        # مصدرا RSI
        xs = float(s['rsi']) if mature else None
        closes = [c for _, c, _ in rec]; xi = None
        if len(closes) >= MIN_BARS_ANY:
            if np.max(np.abs(np.diff(closes) / np.array(closes[:-1]))) < 0.5: xi = _rsi(closes)
            else: notes.append('RSI المستقل غير ممكن (قفزة سعرية في النافذة)')
        ind_reliable = xi is not None and (len(closes) >= MIN_BARS_VERIFY or xs is None)
        if xi is not None and len(closes) < MIN_BARS_VERIFY: notes.append(f'RSI المستقل تقريبي (شموع daily-data = {len(closes)})')
        srcs = [x for x in (xs, xi if ind_reliable else None) if x is not None]
        if not srcs: continue
        hits = [x <= top for x in srcs]
        if not any(hits): continue
        if len(srcs) == 2: level = 'مؤكد (المصدران)' if all(hits) else 'دقّق: مصدر واحد فقط يحقق الشرط'
        else: level = 'مصدر واحد (التحقق غير ممكن)'
        x = xs if xs is not None else xi
        diff = round(xs - xi, 2) if (xs is not None and ind_reliable) else None
        if diff is not None and abs(diff) > VERIFY_TOL: notes.append(f'⚠️ فرق التحقق {diff:+.2f} (انظر الأيام الناقصة)')
        last_td = s['last_date'] if s is not None else (rec[-1][0] if rec else '')
        if last_td and last_td < last_date: notes.append(f'بيانات قديمة: آخر تداول {last_td}')
        rows.append({'الرمز': t, 'السعر': round(price, 2), 'RSI_الحالي': round(x, 2), 'نوع_التنبيه': 'أصفر أدنى - اقتراب (≤5 نقاط)',
                     'المسافة_من_المرجع': round(x - yl, 2),
                     'القاع_الاحمر': round(s['hist_low_rsi'], 2) if (mature and s.get('hist_low_rsi') is not None) else None,
                     'الاصفر_الادنى': round(yl, 2), 'الاصفر_الاعلى': round(yh, 2), 'متوسط_حجم_60_يوم': int(avg_v),
                     'RSI_تحقق': round(xi, 2) if ind_reliable else None, 'فرق_التحقق': diff, 'درجة_التأكيد': level,
                     'آخر_يوم_تداول': last_td, 'ملاحظة': ' | '.join(notes), '_r': rank[level]})
    df = pd.DataFrame(rows)
    if len(df): df = df.sort_values(['_r', 'المسافة_من_المرجع', 'الرمز']).drop(columns='_r').reset_index(drop=True)
    else: df = pd.DataFrame(columns=COLS)
    return df[COLS]
