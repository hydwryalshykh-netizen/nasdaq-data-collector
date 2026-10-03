"""rsi_report.py — يبني القائمة والتنبيهات (xlsx) من الحالة. كل أسهم الحالة تظهر في القائمة بلا استبعاد."""
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from rsi_engine import zone, classify, alert, YEARS

def rows(state):
    out = []
    for t, s in state.items():
        yl, yh = zone(s); x = s.get('rsi') if 'pending' not in s else None; red = s.get('hist_low_rsi')
        ok = x is not None
        out.append({'الرمز': t, 'RSI_الحالي': round(x, 2) if ok else None,
          'السعر_الحالي': round(s['last_close'], 4 if s['last_close'] < 1 else 2),
          'القاع_الاحمر': round(red, 2) if red is not None else None, 'تاريخ_القاع_الاحمر': s.get('hist_low_date'),
          'الاصفر_ادنى': round(yl, 2) if yl is not None else None, 'الاصفر_اعلى': round(yh, 2) if yh is not None else None,
          'قريب_من_المنطقة_الآن': ('نعم' if (yh is not None and x <= yh + 1e-9) else 'لا') if ok else None,
          'نوع_المنطقة_الحالية': classify(x, red, yl, yh) if ok else '-',
          **{f'قاع_RSI_{y}': (round(s['year_lows'][y], 2) if ok and y in s.get('year_lows', {}) else None) for y in YEARS[:7]},
          'متوسط_حجم_التداول': int(s.get('median_volume', 0))})
    df = pd.DataFrame(out)
    df['_k'] = df['نوع_المنطقة_الحالية'].map(lambda z: 0 if z.startswith('أحمر') else (1 if z == 'أصفر' else 2))
    return df.sort_values(['_k', 'RSI_الحالي', 'الرمز'], na_position='last').drop(columns='_k').reset_index(drop=True)

def alerts(state, fresh_date):
    """تنبيه فقط للأسهم المحدَّثة في يوم التداول الأخير (fresh_date) كي لا تتكرر تنبيهات أسهم توقف تداولها."""
    out = []
    for t, s in state.items():
        if 'pending' in s or s['last_date'] != fresh_date: continue
        yl, yh = zone(s); p = s['last_close']; a = alert(p, s['rsi'], s['hist_low_rsi'], yl)
        if not a: continue
        sp = [f"{d} ×{r}" for d, r in s.get('splits', []) if d >= cut6(fresh_date)]
        out.append({'الرمز': t, 'السعر': round(p, 4 if p < 1 else 2), 'RSI_الحالي': round(s['rsi'], 2), 'نوع_التنبيه': a[0],
          'المسافة_من_المرجع': a[1], 'القاع_الاحمر': round(s['hist_low_rsi'], 2),
          'الاصفر_الادنى': round(yl, 2) if yl is not None else None, 'الاصفر_الاعلى': round(yh, 2) if yh is not None else None,
          'نطاق_السعر': '<12$' if p < 12 else ('12-50$' if p <= 50 else '>50$'),
          'تنبيه_سيولة': '⚠️ سيولة ضعيفة - أهمية أقل' if s['median_volume'] < 50000 else '',
          'متوسط_الحجم': int(s['median_volume']), 'مرجع_الدخول': a[2],
          'ملاحظة': ('تقسيم/اندماج مُعدَّل: ' + ' ، '.join(sp)) if sp else ''})
    df = pd.DataFrame(out)
    if len(df):
        df['_l'] = df['تنبيه_سيولة'].ne('').astype(int)
        df = df.sort_values(['_l', 'المسافة_من_المرجع', 'الرمز']).drop(columns='_l').reset_index(drop=True)
    return df

def cut6(d):
    y, m, dd = d.split('-'); m = int(m) - 6; y = int(y)
    if m < 1: m += 12; y -= 1
    return f'{y}-{m:02d}-{dd}'

def write_xlsx(path, df, ad, last_date, notes=None):
    F = lambda **k: Font(name='Arial', size=10, **k)
    wb = Workbook(); ws = wb.active; ws.title = 'ملاحظات_المنهجية'; ws.sheet_view.rightToLeft = True
    notes = notes or [f'قائمة RSI — آخر يوم مكتمل: {last_date}', f'عدد الأسهم: {len(df):,} | التنبيهات: {len(ad):,}']
    for i, t in enumerate(notes, 1):
        c = ws.cell(i, 1, t); c.font = F(bold=(i == 1 or t.endswith(':'))); c.alignment = Alignment(wrap_text=True, vertical='top', horizontal='right')
    ws.column_dimensions['A'].width = 150
    fills = {k: 'FFE699' for k in ['الاصفر_ادنى', 'الاصفر_اعلى', 'الاصفر_الادنى', 'الاصفر_الاعلى']}
    fills.update({'القاع_الاحمر': 'F4B6B6', 'تاريخ_القاع_الاحمر': 'F4B6B6'})
    def sheet(name, d, widths):
        w = wb.create_sheet(name); w.sheet_view.rightToLeft = True
        for j, col in enumerate(d.columns, 1):
            c = w.cell(1, j, col); c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.fill = PatternFill('solid', fgColor=fills.get(col, '1F3864')); c.font = F(bold=True, color='000000' if col in fills else 'FFFFFF')
            w.column_dimensions[get_column_letter(j)].width = widths.get(col, 14)
        for i, row in enumerate(d.itertuples(index=False), 2):
            for j, v in enumerate(row, 1):
                if pd.isna(v): v = None
                c = w.cell(i, j, v); c.font = F(); c.alignment = Alignment(horizontal='center')
        w.row_dimensions[1].height = 32; w.freeze_panes = 'B2'
        w.auto_filter.ref = f'A1:{get_column_letter(max(1, len(d.columns)))}{len(d) + 1}'
    sheet('1_معدل_النجاح_المركزي', df, {'نوع_المنطقة_الحالية': 24, 'تاريخ_القاع_الاحمر': 14, 'متوسط_حجم_التداول': 16})
    sheet('3_قسم_التنبيه', ad if len(ad) else pd.DataFrame(columns=['الرمز', 'السعر', 'RSI_الحالي', 'نوع_التنبيه']), {'نوع_التنبيه': 30, 'تنبيه_سيولة': 26, 'ملاحظة': 36})
    wb.save(path)
