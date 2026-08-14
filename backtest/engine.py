# Nasdaq Combo Engine V1
# Pure formula backtest: separates 10-day and 20-day winners,
# combines them with OR / consensus voting, then performs OOS checking.

import json
from itertools import combinations
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "daily-data"
RESULTS_DIR = ROOT / "results_combo"
RESULTS_DIR.mkdir(exist_ok=True)

HORIZONS = [10, 20]
MIN_HISTORY = 120
MIN_SIGNALS = 100
BASE_WIN_SCORE = 55.0

CANDIDATES = {
    "RSI + 20D Support": lambda x: x.rsi_oversold & x.near_support20,
    "RSI + Stoch + Support": lambda x: x.rsi_oversold & x.stoch_oversold & x.near_support20,
    "Hammer + Support": lambda x: x.hammer & x.near_support20,
    "Bull Engulf + Support": lambda x: x.bullish_engulfing & x.near_support20,
    "50D Support + RSI": lambda x: x.near_support50 & x.rsi_oversold,
    "50D Support + RSI + Higher Low": lambda x: x.near_support50 & x.rsi_oversold & x.higher_low,
    "50D Support + Hammer": lambda x: x.near_support50 & x.hammer,
    "Support Rejection + RSI": lambda x: x.support_rejection & x.rsi_oversold,
    "Bottom Reclaim + Support": lambda x: x.bottom_reclaim & x.near_support50,
    "100D Support + RSI": lambda x: x.near_support100 & x.rsi_oversold,
    "Breakdown + 50D Support": lambda x: x.breakdown20 & x.near_support50,
    "Breakdown + RSI": lambda x: x.breakdown20 & x.rsi_oversold,
    "BB Reclaim + RSI": lambda x: x.bb_reclaim & x.rsi_oversold,
    "Hammer + RSI + Support": lambda x: x.hammer & x.rsi_oversold & x.near_support20,
    "Bull Engulf + RSI + Support": lambda x: x.bullish_engulfing & x.rsi_oversold & x.near_support20,
    "Support Rejection + RSI + Volume": lambda x: x.support_rejection & x.rsi_oversold & x.volume_1_5x,
}

def load_data():
    rows, failed = [], []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            rec = obj.get("records", [])
            if not isinstance(rec, list): raise ValueError("records is not a list")
            rows.extend(rec)
        except Exception as e:
            failed.append({"file": p.name, "error": str(e)})
    if not rows: raise RuntimeError(f"No usable records in {DATA_DIR}")
    df = pd.DataFrame(rows)
    req = ["symbol","date","open","high","low","close","volume"]
    for c in req:
        if c not in df.columns: raise RuntimeError(f"Missing column: {c}")
    df.date = pd.to_datetime(df.date, errors="coerce")
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=req)
    df = df[(df.open>0)&(df.high>0)&(df.low>0)&(df.close>0)&(df.volume>=0)]
    df = df[df.high >= df[["open","close","low"]].max(axis=1)]
    df = df[df.low <= df[["open","close","high"]].min(axis=1)]
    df = df.drop_duplicates(["symbol","date"], keep="last").sort_values(["symbol","date"]).reset_index(drop=True)
    return df, failed

def rsi(c, n=14):
    d=c.diff(); gain=d.clip(lower=0); loss=-d.clip(upper=0)
    ag=gain.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    al=loss.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=ag/al.replace(0,np.nan)
    z=100-100/(1+rs)
    z=z.where(al!=0,100).where(~((ag==0)&(al==0)),50)
    return z

def add_features(g):
    g=g.sort_values("date").copy(); o,h,l,c,v=[g[x] for x in ["open","high","low","close","volume"]]
    g["rsi14"]=rsi(c)
    pc=c.shift(1)
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    g["atr14"]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    g["volume_ratio20"]=v/v.rolling(20,min_periods=20).mean().replace(0,np.nan)
    g["support20"]=l.shift(1).rolling(20,min_periods=20).min()
    g["support50"]=l.shift(1).rolling(50,min_periods=50).min()
    g["support100"]=l.shift(1).rolling(100,min_periods=100).min()
    g["dist_support20"]=c/g.support20-1; g["dist_support50"]=c/g.support50-1; g["dist_support100"]=c/g.support100-1
    g["near_support20"]=(g.dist_support20>=0)&(g.dist_support20<=.03)
    g["near_support50"]=(g.dist_support50>=0)&(g.dist_support50<=.05)
    g["near_support100"]=(g.dist_support100>=0)&(g.dist_support100<=.05)
    g["rsi_oversold"]=g.rsi14<30
    lo=l.rolling(14,min_periods=14).min(); hi=h.rolling(14,min_periods=14).max()
    g["stoch_k"]=100*(c-lo)/(hi-lo).replace(0,np.nan); g["stoch_oversold"]=g.stoch_k<20
    body=(c-o).abs(); rng=(h-l).replace(0,np.nan)
    lower=np.minimum(o,c)-l; upper=h-np.maximum(o,c)
    g["hammer"]=(lower>=body*2)&(upper<=body)&((body/rng)<=.40)
    po=o.shift(1); pc2=c.shift(1)
    g["bullish_engulfing"]=(pc2<po)&(c>o)&(o<=pc2)&(c>=po)
    p10=l.shift(1).rolling(10,min_periods=10).min()
    p20=l.shift(1).rolling(20,min_periods=20).min()
    g["higher_low"]=(l>p10)&(c>c.shift(1))&(c>g.support20)
    g["bottom_reclaim"]=(c>p20)&(c.shift(1)<=p20.shift(1))
    g["support_rejection"]=(l<=g.support20*1.01)&(c>g.support20)&(c>o)
    g["breakdown20"]=c<g.support20
    bb=c.rolling(20,min_periods=20).mean(); sd=c.rolling(20,min_periods=20).std(ddof=0)
    lowerbb=bb-2*sd
    g["bb_reclaim"]=(c>lowerbb)&(c.shift(1)<=lowerbb.shift(1))
    g["volume_1_5x"]=g.volume_ratio20>=1.5
    g["entry_open"]=o.shift(-1)
    for hzn in HORIZONS:
        g[f"forward_return_{hzn}"]=c.shift(-hzn)/g.entry_open-1
    return g

def build_features(df):
    parts=[]
    for _,grp in df.groupby("symbol",sort=False):
        if len(grp)>=MIN_HISTORY+max(HORIZONS)+1: parts.append(add_features(grp))
    if not parts: raise RuntimeError("No symbol has enough history.")
    return pd.concat(parts,ignore_index=True)

def stats(r):
    r=pd.Series(r).dropna().astype(float)
    if r.empty: return None
    wins=r[r>0]; losses=r[r<0]; gp=wins.sum(); gl=-losses.sum()
    pf=gp/gl if gl>0 else np.inf; win=float((r>0).mean()); mean=float(r.mean()); med=float(r.median())
    sample=min(1.,len(r)/1000.); pf_f=min(1.,max(0.,pf/1.5))
    win_f=min(1.,max(0.,(win-.45)/.15)); med_f=min(1.,max(0.,med/.02)); mean_f=min(1.,max(0.,mean/.02))
    n=max(1,int(len(r)*.01)) if len(r)>=20 else 0
    robust=float(r.sort_values().iloc[:-n].mean()) if n else mean
    robust_f=min(1.,max(0.,robust/.02))
    score=100*sample*(.25*pf_f+.25*win_f+.15*med_f+.20*mean_f+.15*robust_f)
    return dict(signals=len(r),win_rate=win,average_return=mean,median_return=med,
                profit_factor=None if not np.isfinite(pf) else float(pf),
                worst_return=float(r.min()),best_return=float(r.max()),robust_mean=robust,score=float(score))

def mask_for(f,names,threshold=1):
    votes=sum(CANDIDATES[n](f).fillna(False).astype(int) for n in names)
    return votes>=threshold

def evaluate(mask,f,h):
    return stats(f.loc[mask,f"forward_return_{h}"])

def base_tests(f):
    rows=[]
    for name,fn in CANDIDATES.items():
        m=fn(f)
        for h in HORIZONS:
            s=evaluate(m,f,h)
            if s: rows.append({"type":"base","horizon":h,"strategy":name,"threshold":1,**s})
    return pd.DataFrame(rows)

def select_winners(base):
    out={}
    for h in HORIZONS:
        x=base[(base.horizon==h)&(base.signals>=MIN_SIGNALS)&(base.average_return>0)&(base.median_return>0)&(base.score>=BASE_WIN_SCORE)]
        out[h]=x.sort_values(["score","average_return","win_rate"],ascending=False).head(8).strategy.tolist()
    return out

def combo_tests(f,names,h):
    rows=[]
    for k in range(1,min(4,len(names))+1):
        for combo in combinations(names,k):
            label=" + ".join(combo)
            for threshold in range(1,k+1):
                m=mask_for(f,list(combo),threshold)
                s=evaluate(m,f,h)
                if s: rows.append({"type":f"{h}d_vote","horizon":h,"strategy":label,"threshold":threshold,**s})
    return pd.DataFrame(rows)

def cross_pool(f,w10,w20):
    rows=[]
    for a in w10[:5]:
        for b in w20[:5]:
            if a==b: continue
            m=CANDIDATES[a](f).fillna(False)&CANDIDATES[b](f).fillna(False)
            for h in HORIZONS:
                s=evaluate(m,f,h)
                if s: rows.append({"type":"cross_pool_agreement","horizon":h,
                                   "strategy":f"10D:{a} + 20D:{b}","threshold":2,**s})
    return pd.DataFrame(rows)

def oos_top(f,ranked):
    if ranked.empty: return pd.DataFrame()
    z=f.copy(); z["i"]=z.groupby("symbol").cumcount(); z["n"]=z.groupby("symbol").symbol.transform("size")
    z["oos"]=z.i>=z.n*.75
    rows=[]
    for _,r in ranked.head(50).iterrows():
        names=[x.strip() for x in r.strategy.split(" + ") if x.strip() in CANDIDATES]
        if not names or r.type=="cross_pool_agreement": continue
        m=mask_for(z,names,int(r.threshold))&z.oos
        s=evaluate(m,z,int(r.horizon))
        if s: rows.append({"strategy":r.strategy,"horizon":int(r.horizon),"threshold":int(r.threshold),
                           "oos_score":s["score"],"oos_signals":s["signals"],"oos_win_rate":s["win_rate"],
                           "oos_average_return":s["average_return"],"oos_median_return":s["median_return"],
                           "oos_profit_factor":s["profit_factor"]})
    return pd.DataFrame(rows)

def main():
    df,failed=load_data(); f=build_features(df)
    base=base_tests(f); winners=select_winners(base)
    frames=[combo_tests(f,winners[h],h) for h in HORIZONS if winners[h]]
    combo=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    cross=cross_pool(f,winners[10],winners[20])
    ranked=combo.sort_values(["score","average_return","win_rate"],ascending=False).reset_index(drop=True) if not combo.empty else combo
    if not ranked.empty: ranked.insert(0,"rank",np.arange(1,len(ranked)+1))
    oos=oos_top(f,ranked)
    base.to_csv(RESULTS_DIR/"base_strategy_tests.csv",index=False)
    combo.to_csv(RESULTS_DIR/"combo_tests.csv",index=False)
    cross.to_csv(RESULTS_DIR/"cross_pool_tests.csv",index=False)
    oos.to_csv(RESULTS_DIR/"oos_top50.csv",index=False)
    summary={"engine":"Nasdaq Combo Engine V1","method":"formula_consensus",
             "horizons":HORIZONS,"entry_rule":"T close signal -> T+1 open",
             "exit_rule":"T+horizon close","winner_pool_10d":winners[10],"winner_pool_20d":winners[20],
             "records":len(df),"symbols":int(df.symbol.nunique()),
             "first_date":str(df.date.min().date()),"last_date":str(df.date.max().date()),
             "failed_files":len(failed),
             "top_10d":ranked[ranked.horizon==10].head(20).to_dict("records") if not ranked.empty else [],
             "top_20d":ranked[ranked.horizon==20].head(20).to_dict("records") if not ranked.empty else [],
             "top_oos":oos.head(20).to_dict("records") if not oos.empty else [],
             "warning":"Score 89 is a research ranking target, not an 89% probability of profit."}
    (RESULTS_DIR/"combo_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print("NASDAQ COMBO ENGINE V1 COMPLETE")
    print("10D WINNERS:", winners[10])
    print("20D WINNERS:", winners[20])
    print("Results:", RESULTS_DIR)

if __name__=="__main__":
    main()
