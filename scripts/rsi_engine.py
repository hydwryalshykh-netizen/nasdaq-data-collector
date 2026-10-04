"""
rsi_engine.py — محرك RSI (Wilder 14) خفيف الذاكرة: القاع الأحمر + المنطقة الصفراء + التنبيه.
لا يستبعد أي سهم. الحالة لكل سهم صغيرة: avg_gain / avg_loss / last_close + قيعان + لمسات.
"""
import numpy as np

N = 14
CAP = 35.0                      # لمسة = RSI تحت 35
RECOVER = 45.0                  # لمسة جديدة فقط بعد تعافي RSI فوق 45
H_KDE, A_LOW, A_HIGH = 1.5, 0.25, 0.7
WARM = 80                       # شموع الإحماء للتاريخ المبتور عند 2020-01-02 (لا يُعتمد عليها)
YEARS = [str(y) for y in range(2020, 2031)]

# ===================== 1) تنظيف (تصحيح بيانات، وليس استبعاد أسهم) =====================
def clean_ohlcv(dates, close, volume, last_date):
    """يبقي أيام التداول الفعلية فقط حتى آخر يوم مكتمل: إغلاق>0 وإما حجم>0 أو تغيّر السعر عن اليوم السابق.
    ياهو يملأ أيام عدم التداول بنفس الإغلاق وحجم 0 → كانت تُصفّر RSI (يوم بنفس السعر وبلا حجم ليس تداولاً)."""
    dates = np.asarray(dates).astype('U10'); c = np.asarray(close, float); v = np.nan_to_num(np.asarray(volume, float))
    ok = (dates <= last_date) & np.isfinite(c) & (c > 0)
    dates, c, v = dates[ok], c[ok], v[ok]
    if len(c) == 0: return dates, c, v
    prev = np.concatenate([[np.nan], c[:-1]])
    traded = (v > 0) | (c != prev)
    traded[0] = v[0] > 0 or len(c) == 1 or c[0] != c[1]
    return dates[traded], c[traded], v[traded]

def snap_ratio(ratio):
    Nn = round(ratio) if ratio >= 1 else 1.0 / round(1 / ratio)
    return Nn if abs(ratio / Nn - 1) < 0.35 else ratio

def detect_splits(c, v, big=4.0, dv_lo=0.1, dv_hi=10.0):
    """اندماج عكسي/تقسيم غير معدَّل: قفزة >=4x أو <=1/4 تُعدّ تقسيماً إذا قيمة التداول بالدولار (سعر×حجم)
    يوم القفزة متصلة مع آخر 10 أيام (0.1x–10x) والسعر الجديد ثابت. يرجع [(فهرس, نسبة)]"""
    ev = []
    if len(c) < 20: return ev
    r = c[1:] / c[:-1]
    for k in np.where((r >= big) | (r <= 1 / big))[0]:
        i = k + 1; lo = max(0, i - 10)
        pre = np.median(c[lo:i] * v[lo:i])
        if pre <= 0: continue
        nxt = c[i:i + 6]
        persist = np.median(nxt) / c[i] if len(nxt) >= 2 else 1.0
        if dv_lo <= c[i] * v[i] / pre <= dv_hi and 0.5 <= persist <= 2.0:
            ev.append((i, float(r[k])))
    return ev

def back_adjust(c, v, events):
    c = c.copy(); v = v.copy()
    for i, ratio in sorted(events, key=lambda e: -e[0]):
        f = snap_ratio(ratio); c[:i] *= f; v[:i] /= f
    return c, v

# ===================== 2) RSI =====================
def wilder_series(close, n=N):
    """(rsi[], avg_gain_last, avg_loss_last) — Wilder بذرة SMA لأول n فروق (= ta.rsi في TradingView)."""
    from scipy.signal import lfilter
    c = np.asarray(close, float); L = len(c)
    r = np.full(L, np.nan)
    if L <= n: return r, None, None
    d = np.diff(c); g = np.where(d > 0, d, 0.); l = np.where(d < 0, -d, 0.)
    a = 1.0 / n
    def sm(x):
        s0 = x[:n].mean(); y = np.empty(len(x) - n + 1); y[0] = s0
        if len(x) > n:
            z, _ = lfilter([a], [1, -(1 - a)], x[n:], zi=[(1 - a) * s0]); y[1:] = z
        return y
    ag, al = sm(g), sm(l)
    with np.errstate(divide='ignore', invalid='ignore'):
        v = np.where(al == 0, 100.0, 100 - 100 / (1 + ag / al))
        v = np.where((al == 0) & (ag == 0), 50.0, v)
    r[n:] = v
    return r, float(ag[-1]), float(al[-1])

def _rsi(ag, al):
    if al == 0: return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)

def step_rsi(ag, al, prev_close, close, n=N):
    d = close - prev_close
    ag = (ag * (n - 1) + max(d, 0.)) / n
    al = (al * (n - 1) + max(-d, 0.)) / n
    return ag, al, _rsi(ag, al)

# ===================== 3) لمسات + منطقة صفراء =====================
def episodes(r, thr=CAP):
    below = np.isfinite(r) & (r < thr)
    if not below.any(): return []
    b = np.concatenate([[0], below.view(np.int8), [0]])
    st = np.where(np.diff(b) == 1)[0]; en = np.where(np.diff(b) == -1)[0] - 1
    return [(s, e, float(np.min(r[s:e + 1])), s + int(np.argmin(r[s:e + 1]))) for s, e in zip(st, en)]

def touches(r, recover=RECOVER, thr=CAP):
    out = []
    for s_, e, m, i in episodes(r, thr):
        if out and np.nanmax(r[out[-1][1]:s_ + 1]) < recover:
            ps, pe, pm, pi = out[-1]; out[-1] = (ps, e, min(pm, m), i if m < pm else pi)
        else: out.append((s_, e, m, i))
    return out

def yellow_zone(mins, h=H_KDE, a_low=A_LOW, a_high=A_HIGH, cap=CAP):
    """منطقة (لا خط): كثافة غاوسية لنقاط الارتداد؛ من قمة الكثافة للأسفل حتى 25% منها وللأعلى حتى 70%."""
    m = np.sort(np.asarray(mins, float))
    if len(m) < 3: return None, None
    g = np.linspace(0, cap, int(cap / 0.05) + 1)
    d = np.exp(-0.5 * ((g[:, None] - m[None, :]) / h) ** 2).sum(1)
    pk = int(d.argmax()); mx = d[pk]
    lo = pk
    while lo > 0 and d[lo - 1] >= a_low * mx: lo -= 1
    hi = pk
    while hi < len(g) - 1 and d[hi + 1] >= a_high * mx: hi += 1
    ins = m[(m >= g[lo]) & (m <= g[hi])]
    if len(ins) == 0: return None, None
    return float(ins.min()), float(ins.max())

# ===================== 4) الحالة المضغوطة =====================
def build_state(dates, close, volume, truncated, last_date, from_date='2020-01-01'):
    """تاريخ سهم كامل → حالة. لا يرفض أي سهم له يوم تداول واحد على الأقل (أقل من 15 يوماً = حالة 'pending')."""
    dates, c, v = clean_ohlcv(dates, close, volume, last_date)
    if len(c) == 0: return None
    base = {'last_close': float(c[-1]), 'last_date': dates[-1], 'median_volume': float(np.median(v[-60:])),
            'dv10': [float(x) for x in (c[-10:] * v[-10:])], 'splits': []}
    if len(c) <= N:
        base['pending'] = [float(x) for x in c]; return base
    ev = detect_splits(c, v)
    if ev:
        c, v = back_adjust(c, v, ev)
        base['splits'] = [(dates[i], round(rt, 4)) for i, rt in ev]
        base['last_close'] = float(c[-1]); base['median_volume'] = float(np.median(v[-60:]))
    r, ag, al = wilder_series(c)
    if truncated:                       # إحماء: نهمل أول 80 شمعة إلا إذا لم يتبقَّ شيء (سهم نادر التداول) فنُبقي القيم
        r2 = r.copy(); r2[:WARM] = np.nan
        if np.isfinite(r2).any(): r = r2
    r[dates < from_date] = np.nan
    if not np.isfinite(r).any():
        base.update(avg_gain=ag, avg_loss=al, rsi=float(_rsi(ag, al)), hist_low_rsi=None, hist_low_date=None,
                    year_lows={}, tmins=[], in_ep=False, peak_since=-1.0); return base
    ki = int(np.nanargmin(r)); yrs = np.array([s[:4] for s in dates])
    ylows = {y: round(float(np.nanmin(r[yrs == y])), 4) for y in YEARS if (yrs == y).any() and np.isfinite(r[yrs == y]).any()}
    tmins = [round(x[2], 4) for x in touches(r)]
    eps = episodes(r); in_ep = bool(np.isfinite(r[-1]) and r[-1] < CAP)
    peak = float(np.nanmax(r[eps[-1][1] + 1:])) if (eps and not in_ep and eps[-1][1] + 1 < len(r)) else -1.0
    base.update(avg_gain=ag, avg_loss=al, rsi=float(r[-1]) if np.isfinite(r[-1]) else float(_rsi(ag, al)),
                hist_low_rsi=float(r[ki]), hist_low_date=dates[ki], year_lows=ylows, tmins=tmins, in_ep=in_ep, peak_since=peak)
    return base

def _seed_from_pending(s, date):
    cl = s.pop('pending'); d = np.diff(cl)[:N]
    ag = float(np.maximum(d, 0).mean()); al = float(np.maximum(-d, 0).mean())
    for j in range(N + 1, len(cl)): ag, al, _ = step_rsi(ag, al, cl[j - 1], cl[j])
    x = _rsi(ag, al)
    s.update(avg_gain=ag, avg_loss=al, rsi=x, hist_low_rsi=x, hist_low_date=date, year_lows={date[:4]: x},
             tmins=[x] if x < CAP else [], in_ep=x < CAP, peak_since=-1.0)

def advance(s, close, date, volume=None, prev_close=None):
    """دمج يوم جديد. prev_close (من Finnhub) يكشف أي تقسيم/اندماج عكسي: إن خالف آخر إغلاق عندنا نعيد القياس
    (avg_gain, avg_loss, last_close × النسبة) فلا تتولد قفزة وهمية. بدونه: كشف بقفزة>=4x واتصال قيمة التداول.
    يرجع True إذا دُمج اليوم."""
    if not (close and close > 0) or date <= s['last_date']: return False
    dv = close * volume if volume else None
    f = None
    if prev_close and prev_close > 0 and abs(prev_close / s['last_close'] - 1) > 0.02:
        f = prev_close / s['last_close']
    elif not prev_close:
        ratio = close / s['last_close']
        if (ratio >= 4 or ratio <= 0.25) and dv and s.get('dv10'):
            med = float(np.median(s['dv10']))
            if med > 0 and 0.1 <= dv / med <= 10: f = snap_ratio(ratio)
    if f:
        if 'pending' in s: s['pending'] = [x * f for x in s['pending']]
        else: s['avg_gain'] *= f; s['avg_loss'] *= f
        s['last_close'] *= f; s.setdefault('splits', []).append((date, round(f, 4)))
    if dv: s['dv10'] = (s.get('dv10', []) + [dv])[-10:]
    if 'pending' in s:
        s['pending'].append(float(close)); s['last_close'] = float(close); s['last_date'] = date
        if len(s['pending']) > N: _seed_from_pending(s, date)
        return True
    ag, al, x = step_rsi(s['avg_gain'], s['avg_loss'], s['last_close'], close)
    s.update(avg_gain=ag, avg_loss=al, last_close=float(close), last_date=date, rsi=x)
    if s['hist_low_rsi'] is None or x < s['hist_low_rsi']: s['hist_low_rsi'] = x; s['hist_low_date'] = date   # قاع جديد = هو التاريخي
    yl = s['year_lows']; yl[date[:4]] = min(yl.get(date[:4], 999.0), x)
    tm = s['tmins']
    if x < CAP:
        if not s['in_ep']:
            s['in_ep'] = True
            if tm and s['peak_since'] < RECOVER: tm[-1] = min(tm[-1], x)
            else: tm.append(x)
        else: tm[-1] = min(tm[-1], x)
        s['peak_since'] = -1.0
    else:
        s['in_ep'] = False; s['peak_since'] = max(s['peak_since'], x)
    s['median_volume'] = 0.9 * s['median_volume'] + 0.1 * volume if volume else s['median_volume']
    return True

# ===================== 5) تصنيف وتنبيه =====================
def zone(s):
    return yellow_zone(s.get('tmins', [])) if 'pending' not in s else (None, None)

def classify(x, red, yl, yh):
    if x is None: return '-'
    if yl is None: return 'أحمر (قريب جداً من القاع)' if (red is not None and x <= red + 1.0) else '-'
    if x < yl - 1e-9: return 'أحمر (قريب جداً من القاع)' if x <= red + 1.0 else 'أحمر (تحت الأصفر)'
    return 'أصفر' if x <= yh + 1e-9 else '-'

def alert(price, rsi, red, yl):
    """سعر>=12$: قبل الأصفر الأدنى بنقطتين أو أقل (أو داخله) | سعر<12$: قبل القاع الأحمر بنقطتين أو أقل.
    القاع الأحمر يُعتمد فقط إذا كان فعلاً قاعاً (تحت 35)؛ سهم لم ينزل تحت 35 قط (مثل SPAC جديد) لا يوجد له أحمر حقيقي."""
    if price >= 12: ref, name = yl, 'أصفر أدنى'
    else: ref, name = (red, 'قاع أحمر') if (red is not None and red < CAP) else (None, None)
    if ref is None or rsi is None: return None
    if rsi <= ref: return (f'{name} - داخل المنطقة', 0.0, name)
    if rsi <= ref + 2: return (f'{name} - اقتراب (≤2 نقطة)', round(rsi - ref, 2), name)
    return None

def norm_symbol(sym):
    """رموز Nasdaq/Finnhub → صيغة ياهو المستخدمة في الحالة: BRK/B, BRK.B → BRK-B ؛ ABR^D → ABR-PD"""
    sym = str(sym).strip().upper()
    if '^' in sym: a, b = sym.split('^', 1); return f'{a}-P{b}'
    return sym.replace('/', '-').replace('.', '-')
