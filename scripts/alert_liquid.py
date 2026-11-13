"""
قائمة السيولة — محرك مستقل تماماً (لا يعتمد على المحركات السبعة ولا على alert_common).
شرط الدخول (بلا شرط سعر): (أ) متوسط حجم آخر 60 يوماً ≥ 1,000,000 **و** (ب) فحص ثانٍ ضد الطفرات: الحجم ≥ 1,000,000 في 5 أيام على الأقل من آخر 7 أيام تداول
(لأن يوماً واحداً بمئة مليون سهم يرفع المتوسط وحده ولا يعني تداولاً قوياً). ملف واحد بقسمين:
  1) كل الأسهم السائلة (القائمة الكاملة بمعلوماتها)
  2) منها: من يحقق RSI ≤ الأصفر الأدنى + 5 أو أقل  («أصفر أدنى - اقتراب (≤5 نقاط) أو أقل»)
بلا ذاكرة مخفية: القسم 2 = من يحقق الشرط اليوم فقط، وكل صف يُفحص من أعمدته.
RSI من مصدرين: الحالة + إعادة حساب مستقلة من إغلاقات daily-data. يدخل القسم 2 إن حقق الشرط أيٌّ منهما،
وعمود «درجة_التأكيد»: مؤكد (المصدران) / دقّق (مصدر واحد فقط) / مصدر واحد (التحقق غير ممكن).
متوسط الحجم = متوسط آخر 60 يوم تداول فعلية من daily-data. وتُكتشف الأيام الناقصة وتُذكر في تعليق تيليجرام.
"""
import glob, json, os, datetime as dt
import numpy as np
import pandas as pd
from rsi_engine import zone, norm_symbol

SHEET_ALL = '1_كل_الأسهم_حجم_1M'; SHEET_ALERT = '2_اصفر_ادنى_اقتراب_5'
MIN_AVG_VOL = 1_000_000
RECENT_DAYS = 7            # فحص الطفرات: آخر 7 أيام تداول
MIN_DAYS_OVER = 5          # يجب أن يكون الحجم ≥ 1M في 5 منها على الأقل
SPIKE_SHARE = 0.25         # أكبر يوم يشكل ≥ 25% من مجموع 60 يوماً = طفرة (للتنبيه في الملاحظة)
LIQUID_SET = None          # يملؤه run(): الأسهم القوية التداول (يستعملها المنسّق لتصفية المحركات القديمة)
NEAR = 5.0                 # RSI ≤ الأصفر الأدنى + 5 (ويشمل كل ما تحت الأصفر الأدنى)
LABEL = 'أصفر أدنى - اقتراب (≤5 نقاط) أو أقل'
VOL_DAYS = 60; MIN_VOL_DAYS = 20
MAX_FILES = 320
MIN_BARS_VERIFY = 100      # شموع كافية ليتقارب RSI المستقل (Wilder)
MIN_BARS_ANY = 15
VERIFY_TOL = 1.0
DEFAULT_YL, DEFAULT_YH = 24.4, 33.9
HOLIDAYS = {'2025-07-04', '2025-09-01', '2025-11-27', '2025-12-25', '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03',
            '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25'}
COLS_ALL = ['الرمز', 'السعر', 'RSI_الحالي', 'القاع_الاحمر', 'الاصفر_الادنى', 'الاصفر_الاعلى', 'المسافة_من_الاصفر_الادنى',
            'متوسط_حجم_60_يوم', 'أيام_فوق_مليون_من_7', 'وسيط_حجم_7_ايام', 'RSI_تحقق', 'فرق_التحقق', 'حالة_المصدرين', 'آخر_يوم_تداول', 'ملاحظة']
COLS_ALERT = ['الرمز', 'السعر', 'RSI_الحالي', 'نوع_التنبيه', 'المسافة_من_الاصفر_الادنى', 'القاع_الاحمر', 'الاصفر_الادنى', 'الاصفر_الاعلى',
              'متوسط_حجم_60_يوم', 'أيام_فوق_مليون_من_7', 'وسيط_حجم_7_ايام', 'RSI_تحقق', 'فرق_التحقق', 'درجة_التأكيد', 'آخر_يوم_تداول', 'ملاحظة']
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

def _read(path):
    try: d = json.load(open(path, encoding='utf-8'))
    except Exception: return None, []
    return (d.get('date') or os.path.basename(path)[:-5]), d.get('records', [])

def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def run(state, universe, last_date, daily_dir):
    """يرجع (DataFrame كل الأسهم القوية التداول، DataFrame التنبيه)."""
    files = sorted(glob.glob(os.path.join(daily_dir, '*.json')))[-MAX_FILES:]
    inuni = lambda t: universe is None or t in universe
    # --- مرور 1: آخر 60 ملفاً → أحجام وإغلاقات كل الأسهم (لتحديد السائلة)
    recent_files, older_files = files[-VOL_DAYS:], files[:-VOL_DAYS]
    vols, closes_recent, dates, vbd = {}, {}, [], {}
    for f in recent_files:
        date, recs = _read(f)
        if date is None: continue
        dates.append(date)
        for r in recs:
            k = norm_symbol(r.get('symbol', ''))
            if not k or not inuni(k): continue
            c = _num(r.get('close')); v = _num(r.get('volume')) or 0.0
            if c and c > 0:
                closes_recent.setdefault(k, []).append((date, c))
                if v > 0: vols.setdefault(k, []).append(v)
            vbd.setdefault(k, {})[date] = v
    # --- السائلة: (أ) متوسط 60 يوماً ≥ 1M  (ب) 5 من آخر 7 أيام ≥ 1M  — وإلا تُسجَّل في «المستبعدة» مع السبب
    last7 = sorted(set(dates))[-RECENT_DAYS:]; can7 = len(last7) >= RECENT_DAYS
    liquid, avgv, vnote, v7 = set(), {}, {}, {}
    for k in set(vols) | {t for t in state if inuni(t)}:
        vs = vols.get(k, [])
        if len(vs) >= MIN_VOL_DAYS: a = float(np.mean(vs[-VOL_DAYS:])); note = ''
        elif k in state and state[k].get('median_volume'): a = float(state[k]['median_volume']); note = 'الحجم من الحالة (daily-data أقل من 20 يوماً)'
        else: a = float(np.mean(vs)) if vs else 0.0; note = f'الحجم من {len(vs)} يوماً فقط'
        if a < MIN_AVG_VOL: continue
        v7k = [vbd.get(k, {}).get(d, 0.0) for d in last7]; over = sum(1 for v in v7k if v >= MIN_AVG_VOL)
        tail = vs[-VOL_DAYS:]; share = (max(tail) / sum(tail)) if tail else 0.0      # أكبر يوم ÷ مجموع 60 يوماً
        med7 = float(np.median(v7k)) if v7k else 0.0
        if can7 and over < MIN_DAYS_OVER: continue          # طفرة/تجميع يوم واحد: لا تداول قوي → يُستبعد بصمت
        if not can7: note = (note + ' | ' if note else '') + f'فحص 7 أيام غير ممكن (ملفات daily-data = {len(last7)})'
        elif share >= SPIKE_SHARE: note = (note + ' | ' if note else '') + f'⚠️ يوم واحد يشكل {share:.0%} من حجم 60 يوماً'
        liquid.add(k); avgv[k] = a; vnote[k] = note; v7[k] = (over if can7 else None, int(med7) if can7 else None)
    globals()['LIQUID_SET'] = set(liquid) if can7 else None      # بلا 7 ملفات لا يُفرَض الفلتر على المحركات القديمة
    # --- مرور 2: الملفات الأقدم للسائلة فقط (لإعادة حساب RSI المستقل)
    older = {k: [] for k in liquid}
    for f in older_files:
        date, recs = _read(f)
        if date is None: continue
        dates.append(date)
        for r in recs:
            k = norm_symbol(r.get('symbol', ''))
            if k in older:
                c = _num(r.get('close'))
                if c and c > 0: older[k].append((date, c))
    missing = _missing_days(dates)
    INFO.clear(); INFO.update(missing_days=missing, files=len(dates), liquid=len(liquid))
    rank = {'مؤكد (المصدران)': 0, 'مصدر واحد (التحقق غير ممكن)': 1, 'دقّق: مصدر واحد فقط يحقق الشرط': 2}
    rows_all, rows_alert = [], []
    for t in liquid:
        s = state.get(t); mature = s is not None and 'pending' not in s and s.get('rsi') is not None
        series = sorted(older.get(t, []) + closes_recent.get(t, []))
        closes = [c for _, c in series]
        price = s['last_close'] if s is not None else (closes[-1] if closes else None)
        if price is None: continue
        notes = [vnote[t]] if vnote.get(t) else []
        yl = yh = None
        if mature: yl, yh = zone(s)
        est = yl is None
        if est: yl, yh = DEFAULT_YL, DEFAULT_YH; notes.append('منطقة صفراء تقديرية (أقل من 3 لمسات)' if mature else 'سهم حديث: منطقة صفراء تقديرية')
        xs = float(s['rsi']) if mature else None
        xi = None
        if len(closes) >= MIN_BARS_ANY:
            if np.max(np.abs(np.diff(closes) / np.array(closes[:-1]))) < 0.5: xi = _rsi(closes)
            else: notes.append('RSI المستقل غير ممكن (قفزة سعرية في النافذة)')
        ind_ok = xi is not None and (len(closes) >= MIN_BARS_VERIFY or xs is None)
        if xi is not None and len(closes) < MIN_BARS_VERIFY: notes.append(f'RSI المستقل تقريبي (شموع daily-data = {len(closes)})')
        srcs = [x for x in (xs, xi if ind_ok else None) if x is not None]
        x = xs if xs is not None else (xi if ind_ok else None)
        diff = round(xs - xi, 2) if (xs is not None and ind_ok) else None
        if diff is not None and abs(diff) > VERIFY_TOL: notes.append(f'⚠️ فرق التحقق {diff:+.2f} (انظر الأيام الناقصة)')
        last_td = s['last_date'] if s is not None else (series[-1][0] if series else '')
        if last_td and last_td < last_date: notes.append(f'بيانات قديمة: آخر تداول {last_td}')
        hit, level, agree = False, '', 'لا RSI'
        if srcs:
            hits = [v <= yl + NEAR for v in srcs]; hit = any(hits)
            if len(srcs) == 2:
                agree = 'متفقان' if hits[0] == hits[1] else 'مختلفان (دقّق)'
                level = 'مؤكد (المصدران)' if all(hits) else 'دقّق: مصدر واحد فقط يحقق الشرط'
            else:
                agree = 'مصدر واحد (التحقق غير ممكن)'; level = 'مصدر واحد (التحقق غير ممكن)'
        base = {'الرمز': t, 'السعر': round(price, 4 if price < 1 else 2), 'RSI_الحالي': round(x, 2) if x is not None else None,
                'القاع_الاحمر': round(s['hist_low_rsi'], 2) if (mature and s.get('hist_low_rsi') is not None) else None,
                'الاصفر_الادنى': round(yl, 2), 'الاصفر_الاعلى': round(yh, 2),
                'المسافة_من_الاصفر_الادنى': round(x - yl, 2) if x is not None else None,
                'متوسط_حجم_60_يوم': int(avgv[t]), 'أيام_فوق_مليون_من_7': v7[t][0], 'وسيط_حجم_7_ايام': v7[t][1],
                'RSI_تحقق': round(xi, 2) if ind_ok else None, 'فرق_التحقق': diff,
                'حالة_المصدرين': agree, 'آخر_يوم_تداول': last_td, 'ملاحظة': ' | '.join(notes)}
        rows_all.append(base)
        if hit:
            a = dict(base); a.pop('حالة_المصدرين'); a['نوع_التنبيه'] = LABEL; a['درجة_التأكيد'] = level; a['_r'] = rank[level]
            rows_alert.append(a)
    df_all = pd.DataFrame(rows_all, columns=COLS_ALL)
    if len(df_all): df_all = df_all.sort_values(['المسافة_من_الاصفر_الادنى', 'الرمز'], na_position='last').reset_index(drop=True)
    df_al = pd.DataFrame(rows_alert)
    if len(df_al): df_al = df_al.sort_values(['_r', 'المسافة_من_الاصفر_الادنى', 'الرمز']).drop(columns='_r').reset_index(drop=True)
    else: df_al = pd.DataFrame(columns=COLS_ALERT)
    return df_all, df_al[COLS_ALERT]

def write_xlsx(path, df_all, df_alert):
    """ملف واحد بورقتين: القسم 1 كل الأسهم القوية التداول، القسم 2 من يحقق الشرط."""
    from openpyxl import Workbook
    from rsi_report import _sheet
    wb = Workbook(); wb.remove(wb.active)
    w = {'نوع_التنبيه': 30, 'درجة_التأكيد': 30, 'حالة_المصدرين': 26, 'ملاحظة': 44, 'آخر_يوم_تداول': 16, 'المسافة_من_الاصفر_الادنى': 18}
    _sheet(wb, SHEET_ALL, df_all, w); _sheet(wb, SHEET_ALERT, df_alert, w)
    wb.save(path)
