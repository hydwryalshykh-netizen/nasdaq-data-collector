"""بناء الحالة (rsi-state.json) من ملفات السوق الكاملة CSV — مرة واحدة فقط.
python scripts/build-rsi-state.py US_MARKET_Part_1.csv US_MARKET_Part_2.csv US_MARKET_Part_3.csv 2026-10-01"""
import sys, json, pandas as pd
from rsi_engine import build_state

def load(files):
    parts = {}
    for f in files:
        for ch in pd.read_csv(f, usecols=['Date', 'Ticker', 'Close', 'Volume'], dtype={'Ticker': 'category'}, chunksize=500000):
            ch['Ticker'] = ch['Ticker'].astype(str); ch['Date'] = ch['Date'].str.slice(0, 10)
            for t, g in ch.groupby('Ticker', sort=False): parts.setdefault(t, []).append(g[['Date', 'Close', 'Volume']])
    return {t: pd.concat(p).drop_duplicates('Date', keep='last').sort_values('Date').reset_index(drop=True) for t, p in parts.items()}

if __name__ == '__main__':
    *files, last = sys.argv[1:]
    F = load(files); st = {}
    for t, d in F.items():
        s = build_state(d['Date'].values, d['Close'].values, d['Volume'].values, d['Date'].iloc[0] <= '2020-01-10', last)
        if s: st[t] = s
    json.dump(st, open('rsi-state.json', 'w'), ensure_ascii=False, separators=(',', ':'))
    print(len(F), 'سهم في الملفات |', len(st), 'في الحالة')
