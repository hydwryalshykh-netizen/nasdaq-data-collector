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

def _threshold(m, red, yl, yh):
    """أعلى RSI يتحقق عنده شرط المحرك (الشرط دائماً x ≤ حد) — بالتنصيف، فلا نحتاج تعديل ملفات المحركات."""
    lo, hi = -1.0, 101.0
    if not m.check(lo, red, yl, yh): return None
    for _ in range(40):
        mid = (lo + hi) / 2
        if m.check(mid, red, yl, yh): lo = mid
        else: hi = mid
    return lo

def run_engine(modname, state, universe, last_date):
    """يستورد المحرك بالاسم ويمرّر عليه أسهم نطاقه السعري. يرجع (NAME, TIER, DataFrame).
    السهم يُقيَّم على آخر RSI معروف ويبقى ما دام شرطه قائماً (ولو لم يتداول اليوم).
    قاعدة الاستمرار: عند التنبيه نحفظ في الحالة s['alert_exit'] = أعلى RSI دخل عنده. فإذا عبر السعر إلى قسم آخر شرطه أضيق
    يبقى السهم ما دام RSI ≤ ذلك المستوى، ولا يخرج إلا حين يرتفع RSI فوقه."""
    m = importlib.import_module(modname)
    cut = _cut6(last_date); rows = []
    for sym, s in state.items():
        if universe is not None and sym not in universe: continue
        if 'pending' in s or s.get('rsi') is None: continue
        p = s['last_close']
        if not (m.LO <= p < m.HI): continue
        yl, yh = zone(s)
        x = s['rsi']; red = s.get('hist_low_rsi'); held = s.get('alert_exit')
        hit = m.check(x, red, yl, yh)
        if hit:
            T = _threshold(m, red, yl, yh)
            s['alert_exit'] = max(T, held) if (held is not None and x <= held and T is not None) else T
            label, dist = hit
        elif held is not None and x <= held:
            ref = yl if yl is not None else (red if red is not None else 0.0)
            label, dist = ('استمرار — دخل سابقاً ولم يرتفع بعد', round(x - ref, 2))
        else:
            if held is not None: s.pop('alert_exit', None)      # ارتفع فوق مستواه → خرج
            continue
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
