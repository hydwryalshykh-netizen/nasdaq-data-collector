"""alert_common.py — السباكة المشتركة لمحركات التنبيه السبعة. لا تحوي أي قاعدة تنبيه (القواعد في alert_s1..s7)."""
import importlib
import pandas as pd
from rsi_engine import zone

COLS = ['الرمز', 'السعر', 'RSI_الحالي', 'نوع_التنبيه', 'المسافة_من_المرجع', 'القاع_الاحمر', 'الاصفر_الادنى', 'الاصفر_الاعلى',
        'نطاق_السعر', 'تنبيه_سيولة', 'متوسط_الحجم', 'آخر_يوم_تداول', 'ملاحظة']
WEAK_VOLUME = 50000

def _cut6(d):
    y, m, dd = d.split('-'); m = int(m) - 6; y = int(y)
    if m < 1: m += 12; y -= 1
    return f'{y}-{m:02d}-{dd}'

def run_engine(modname, state, universe, last_date):
    """يستورد المحرك بالاسم ويمرّر عليه أسهم نطاقه السعري. يرجع (NAME, TIER, DataFrame).
    الأسهم تُقيَّم على آخر RSI معروف: السهم يبقى في القائمة ما دام شرطه قائماً (ولو لم يتداول اليوم) ويخرج حين يرتفع فوق شرطه."""
    m = importlib.import_module(modname)
    cut = _cut6(last_date); rows = []
    for sym, s in state.items():
        if universe is not None and sym not in universe: continue
        if 'pending' in s or s.get('rsi') is None: continue
        p = s['last_close']
        if not (m.LO <= p < m.HI): continue
        yl, yh = zone(s)
        hit = m.check(s['rsi'], s.get('hist_low_rsi'), yl, yh)
        if not hit: continue
        label, dist = hit
        sp = [f'{d} ×{r}' for d, r in s.get('splits', []) if d >= cut]
        rows.append({'الرمز': sym, 'السعر': round(p, 4 if p < 1 else 2), 'RSI_الحالي': round(s['rsi'], 2), 'نوع_التنبيه': label,
                     'المسافة_من_المرجع': dist, 'القاع_الاحمر': round(s['hist_low_rsi'], 2) if s.get('hist_low_rsi') is not None else None,
                     'الاصفر_الادنى': round(yl, 2) if yl is not None else None, 'الاصفر_الاعلى': round(yh, 2) if yh is not None else None,
                     'نطاق_السعر': m.TIER, 'تنبيه_سيولة': '⚠️ سيولة ضعيفة - أهمية أقل' if s['median_volume'] < WEAK_VOLUME else '',
                     'متوسط_الحجم': int(s['median_volume']), 'آخر_يوم_تداول': s['last_date'],
                     'ملاحظة': ('تقسيم/اندماج مُعدَّل: ' + ' ، '.join(sp)) if sp else ''})
    df = pd.DataFrame(rows, columns=COLS)
    if len(df):
        df['_l'] = df['تنبيه_سيولة'].ne('').astype(int)
        df = df.sort_values(['_l', 'المسافة_من_المرجع', 'الرمز']).drop(columns='_l').reset_index(drop=True)
    return m.NAME, m.TIER, df
