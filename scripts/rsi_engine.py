"""
rsi_engine.py — محرك RSI (Wilder 14) خفيف الذاكرة: القاع الأحمر + المنطقة الصفراء + التنبيه.
لا يستبعد أي سهم. الحالة لكل سهم صغيرة: avg_gain / avg_loss / last_close + قيعان + لمسات.
"""
import numpy as np

            ev.append((i, float(r[k])))
    return ev

def back_adjust(c, v, events):
    c = c.copy(); v = v.copy()
    for i, ratio in sorted(events, key=lambda e: -e[0]):
        f = snap_ratio(ratio); c[:i] *= f; v[:i] /= f
    return c, v

    with np.errstate(divide='ignore', invalid='ignore'):
        v = np.where(al == 0, 100.0, 100 - 100 / (1 + ag / al))
        v = np.where((al == 0) & (ag == 0), 50.0, v)
    r[n:] = v
    return r, float(ag[-1]), float(al[-1])

def _rsi(ag, al):
    yl = s['year_lows']; yl[date[:4]] = min(yl.get(date[:4], 999.0), x)
    tm = s['tmins']
    if x < CAP:
        if not s['in_ep']:
            s['in_ep'] = True
            if tm and s['peak_since'] < RECOVER: tm[-1] = min(tm[-1], x)
            else: tm.append(x)
