import csv
import json
import math
import re
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1] if (Path(__file__).parent.name == "engines") else Path(__file__).resolve().parent
DATA_DIR = ROOT / "daily-data"
RESULTS_DIR = ROOT / "results_combo"
RESULTS_DIR.mkdir(exist_ok=True)

HORIZONS = [10, 20]
MIN_HISTORY = 120
MIN_SIGNALS = 100
WINNER_POOL_SIZE = 8
COMBO_MAX_K = 4
TARGET_SCORE = 89.0
OOS_FRACTION = 0.25


# ---------- Data ----------

def load_data():
    rows, failed = [], []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            rec = obj.get("records", [])
            if not isinstance(rec, list):
                raise ValueError("records is not a list")
            rows.extend(rec)
        except Exception as exc:
            failed.append({"file": p.name, "error": str(exc)})

    if not rows:
        raise RuntimeError(f"No usable records in {DATA_DIR}")

    df = pd.DataFrame(rows)
    req = ["symbol", "date", "open", "high", "low", "close", "volume"]
    missing = [c for c in req if c not in df.columns]
    if missing:
        raise RuntimeError(f"Missing columns: {missing}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=req).copy()
    df = df[
        (df["open"] > 0) & (df["high"] > 0) &
        (df["low"] > 0) & (df["close"] > 0) &
        (df["volume"] >= 0)
    ]
    df = df[df["high"] >= df[["open", "close", "low"]].max(axis=1)]
    df = df[df["low"] <= df[["open", "close", "high"]].min(axis=1)]
    df = (
        df.drop_duplicates(["symbol", "date"], keep="last")
          .sort_values(["symbol", "date"])
          .reset_index(drop=True)
    )
    return df, failed


def rsi(close, period=14):
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(al != 0, 100)
    out = out.where(~((ag == 0) & (al == 0)), 50)
    return out


def adx(high, low, close, period=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    tr_avg = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_avg = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    minus_avg = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_avg / tr_avg.replace(0, np.nan)
    minus_di = 100 * minus_avg / tr_avg.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_features(g):
    g = g.sort_values("date").copy()
    o, h, l, c, v = [g[x] for x in ["open", "high", "low", "close", "volume"]]

    g["ema20"] = c.ewm(span=20, adjust=False, min_periods=20).mean()
    g["ema50"] = c.ewm(span=50, adjust=False, min_periods=50).mean()
    g["rsi14"] = rsi(c)
    g["adx14"] = adx(h, l, c)

    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    g["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    g["volume_ratio20"] = v / v.rolling(20, min_periods=20).mean().replace(0, np.nan)

    # Prior-window supports/resistances: today's candle is never used to define them.
    g["support20"] = l.shift(1).rolling(20, min_periods=20).min()
    g["support50"] = l.shift(1).rolling(50, min_periods=50).min()
    g["support100"] = l.shift(1).rolling(100, min_periods=100).min()
    g["resistance20"] = h.shift(1).rolling(20, min_periods=20).max()
    g["resistance50"] = h.shift(1).rolling(50, min_periods=50).max()
    g["resistance100"] = h.shift(1).rolling(100, min_periods=100).max()

    g["distance_support20"] = c / g["support20"] - 1
    g["distance_support50"] = c / g["support50"] - 1
    g["distance_support100"] = c / g["support100"] - 1
    g["distance_resistance20"] = c / g["resistance20"] - 1
    g["distance_resistance50"] = c / g["resistance50"] - 1
    g["distance_resistance100"] = c / g["resistance100"] - 1

    g["near_support"] = g["distance_support20"].between(0, 0.03)
    g["near_support20"] = g["near_support"]
    g["near_support50"] = g["distance_support50"].between(0, 0.05)
    g["near_support100"] = g["distance_support100"].between(0, 0.05)
    g["near_resistance"] = g["distance_resistance20"].between(-0.03, 0)
    g["near_resistance50"] = g["distance_resistance50"].between(-0.03, 0)
    g["near_resistance100"] = g["distance_resistance100"].between(-0.03, 0)

    g["rsi_oversold"] = g["rsi14"] < 30
    g["rsi_overbought"] = g["rsi14"] > 70
    g["above_ema20"] = c > g["ema20"]
    g["above_ema50"] = c > g["ema50"]

    g["volume_1_5x"] = g["volume_ratio20"] >= 1.5
    g["volume_2x"] = g["volume_ratio20"] >= 2.0

    g["momentum20"] = c / c.shift(20) - 1
    g["momentum_positive"] = g["momentum20"] > 0
    g["momentum_5pct"] = g["momentum20"] > 0.05
    g["momentum_10pct"] = g["momentum20"] > 0.10
    g["adx_strong"] = g["adx14"] >= 25

    lo14 = l.rolling(14, min_periods=14).min()
    hi14 = h.rolling(14, min_periods=14).max()
    g["stoch_k"] = 100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)
    g["stoch_oversold"] = g["stoch_k"] < 20

    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    g["hammer"] = (lower >= body * 2) & (upper <= body) & ((body / rng) <= 0.40)

    po, pc = o.shift(1), c.shift(1)
    g["bullish_engulfing"] = (pc < po) & (c > o) & (o <= pc) & (c >= po)

    prior10_low = l.shift(1).rolling(10, min_periods=10).min()
    prior20_low = l.shift(1).rolling(20, min_periods=20).min()
    g["higher_low"] = (l > prior10_low) & (c > c.shift(1)) & (c > g["support20"])
    g["bottom_reclaim"] = (c > prior20_low) & (c.shift(1) <= prior20_low.shift(1))

    g["support_rejection"] = (
        (l <= g["support20"] * 1.01) &
        (c > g["support20"]) &
        (c > o)
    )

    g["breakdown20"] = c < g["support20"]
    g["breakout20"] = c > g["resistance20"]
    g["breakout_retest"] = (
        (c > g["resistance20"]) &
        (l <= g["resistance20"] * 1.02) &
        (c > o)
    )
    g["resistance_rejection"] = (
        (h >= g["resistance20"] * 0.99) &
        (c < g["resistance20"]) &
        (c < o)
    )

    bb = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std(ddof=0)
    g["bb_lower"] = bb - 2 * sd
    g["below_bb_lower"] = c < g["bb_lower"]
    g["bb_reclaim"] = (c > g["bb_lower"]) & (c.shift(1) <= g["bb_lower"].shift(1))

    # Backtest entry: next session open after signal at T close.
    g["entry_open"] = o.shift(-1)
    for hz in HORIZONS:
        g[f"forward_return_{hz}"] = c.shift(-hz) / g["entry_open"] - 1

    return g


def build_features(df):
    parts = []
    for _, grp in df.groupby("symbol", sort=False):
        if len(grp) >= MIN_HISTORY + max(HORIZONS) + 1:
            parts.append(add_features(grp))
    if not parts:
        raise RuntimeError("No symbol has enough history.")
    return pd.concat(parts, ignore_index=True)


# ---------- Rule library ----------
# Names intentionally match the names used in the previous ttttt / strategy_tests output.

RULES = {
    "Trend: EMA50 + Momentum": lambda x: x["above_ema50"] & x["momentum_positive"],
    "Trend: EMA50 + Momentum + Volume": lambda x: x["above_ema50"] & x["momentum_positive"] & x["volume_1_5x"],
    "Trend: EMA50 + ADX": lambda x: x["above_ema50"] & x["adx_strong"],

    "Reversal: 50D Support + RSI": lambda x: x["near_support50"] & x["rsi_oversold"],
    "Reversal: 50D Support + Higher Low": lambda x: x["near_support50"] & x["higher_low"],
    "Reversal: 50D Support + Volume": lambda x: x["near_support50"] & x["volume_1_5x"],
    "Reversal: 50D Support + RSI + Higher Low": lambda x: x["near_support50"] & x["rsi_oversold"] & x["higher_low"],
    "Reversal: 50D Support + Hammer": lambda x: x["near_support50"] & x["hammer"],
    "Reversal: 50D Support + Bull Engulf": lambda x: x["near_support50"] & x["bullish_engulfing"],
    "Reversal: 50D Support + Bull Engulf + RSI": lambda x: x["near_support50"] & x["bullish_engulfing"] & x["rsi_oversold"],
    "Reversal: Support Rejection + RSI": lambda x: x["support_rejection"] & x["rsi_oversold"],
    "Reversal: Support Rejection + Volume": lambda x: x["support_rejection"] & x["volume_1_5x"],
    "Reversal: Support Rejection + RSI + Volume": lambda x: x["support_rejection"] & x["rsi_oversold"] & x["volume_1_5x"],
    "Reversal: 100D Support + RSI": lambda x: x["near_support100"] & x["rsi_oversold"],
    "Reversal: Bottom Reclaim + Support": lambda x: x["bottom_reclaim"] & x["near_support50"],
    "Reversal: RSI + 20D Support": lambda x: x["rsi_oversold"] & x["near_support"],
    "Reversal: RSI + Stoch + Support": lambda x: x["rsi_oversold"] & x["stoch_oversold"] & x["near_support"],
    "Reversal: Hammer + Support": lambda x: x["hammer"] & x["near_support"],
    "Reversal: Hammer + RSI + Support": lambda x: x["hammer"] & x["rsi_oversold"] & x["near_support"],
    "Reversal: Bull Engulf + Support": lambda x: x["bullish_engulfing"] & x["near_support"],
    "Reversal: Bull Engulf + RSI + Support": lambda x: x["bullish_engulfing"] & x["rsi_oversold"] & x["near_support"],

    "Secondary: Breakdown + RSI": lambda x: x["breakdown20"] & x["rsi_oversold"],
    "Secondary: Breakdown + RSI + Volume": lambda x: x["breakdown20"] & x["rsi_oversold"] & x["volume_1_5x"],
    "Secondary: Breakdown + 50D Support": lambda x: x["breakdown20"] & x["near_support50"],

    "Breakout: Breakout + Volume": lambda x: x["breakout20"] & x["volume_1_5x"],
    "Breakout: Breakout + EMA50 + Volume": lambda x: x["breakout20"] & x["above_ema50"] & x["volume_1_5x"],
    "Breakout: Retest + Volume": lambda x: x["breakout_retest"] & x["volume_1_5x"],
    "Resistance: Rejection + RSI": lambda x: x["resistance_rejection"] & x["rsi_overbought"],
}


# ---------- Read previous winners ----------

def locate_previous_results():
    candidates = [
        ROOT / "ttttt.txt",
        ROOT / "ttttt.csv",
        ROOT / "results" / "strategy_tests.csv",
        ROOT / "results" / "ttttt.txt",
        ROOT / "results" / "ttttt.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Previous test not found. Put ttttt.txt (or strategy_tests.csv) in the repository."
    )


def read_previous_results(path):
    # The ttttt.txt shown in the project is CSV text with this header.
    text = path.read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"Empty previous result file: {path}")

    try:
        rows = list(csv.DictReader(lines))
        df = pd.DataFrame(rows)
    except Exception as exc:
        raise RuntimeError(f"Cannot parse previous results {path}: {exc}") from exc

    if "rule" not in df.columns or "horizon_days" not in df.columns:
        # Support an alternate older output where the first columns may differ.
        raise RuntimeError(
            f"{path.name} must contain at least 'rule' and 'horizon_days'. "
            f"Columns found: {list(df.columns)}"
        )

    for c in df.columns:
        if c in {"rule"}:
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["rule"] = df["rule"].astype(str).str.strip()
    return df


def select_winners(previous, horizon):
    x = previous[previous["horizon_days"] == horizon].copy()

    # Use the previous engine's own robustness_score when present.
    score_col = "robustness_score" if "robustness_score" in x.columns else None
    if score_col is None:
        raise RuntimeError("Previous results do not contain robustness_score.")

    required = ["signals", "average_return", "median_return", score_col]
    for c in required:
        if c not in x.columns:
            raise RuntimeError(f"Previous results missing {c}")

    x = x[
        (x["signals"] >= MIN_SIGNALS) &
        (x["average_return"] > 0) &
        (x["median_return"] > 0) &
        (x[score_col] > 0)
    ].copy()

    # Keep only rules we can reproduce exactly with formula masks.
    x = x[x["rule"].isin(RULES)].copy()

    x = x.sort_values(
        [score_col, "average_return", "win_rate", "signals"],
        ascending=False
    ).drop_duplicates("rule")

    return x.head(WINNER_POOL_SIZE).reset_index(drop=True)


# ---------- Evaluation ----------

def returns_for(mask, f, horizon):
    return f.loc[mask, f"forward_return_{horizon}"].dropna().astype(float)


def stats(returns):
    r = pd.Series(returns).dropna().astype(float)
    if r.empty:
        return None

    wins = r[r > 0]
    losses = r[r < 0]
    gp = wins.sum()
    gl = -losses.sum()
    pf = gp / gl if gl > 0 else np.inf

    win = float((r > 0).mean())
    mean = float(r.mean())
    median = float(r.median())

    # A transparent research score, not a probability.
    sample = min(1.0, len(r) / 1000.0)
    pf_f = min(1.0, max(0.0, pf / 1.5))
    win_f = min(1.0, max(0.0, (win - 0.45) / 0.15))
    med_f = min(1.0, max(0.0, median / 0.02))
    mean_f = min(1.0, max(0.0, mean / 0.02))

    n = max(1, int(len(r) * 0.01)) if len(r) >= 20 else 0
    robust = float(r.sort_values().iloc[:-n].mean()) if n else mean
    robust_f = min(1.0, max(0.0, robust / 0.02))

    score = 100 * sample * (
        0.25 * pf_f +
        0.25 * win_f +
        0.15 * med_f +
        0.20 * mean_f +
        0.15 * robust_f
    )

    return {
        "signals": int(len(r)),
        "win_rate": win,
        "average_return": mean,
        "median_return": median,
        "profit_factor": None if not np.isfinite(pf) else float(pf),
        "worst_return": float(r.min()),
        "best_return": float(r.max()),
        "robust_mean": robust,
        "score": float(score),
    }


def mask_for(f, names, threshold=1):
    votes = sum(
        RULES[n](f).fillna(False).astype(int)
        for n in names
    )
    return votes >= threshold


def evaluate(f, names, horizon, threshold=1, oos_mask=None):
    m = mask_for(f, names, threshold)
    if oos_mask is not None:
        m = m & oos_mask
    return stats(returns_for(m, f, horizon))


# ---------- Combination search ----------

def combo_search(f, names, horizon, pool_name):
    rows = []
    max_k = min(COMBO_MAX_K, len(names))

    for k in range(1, max_k + 1):
        for combo in combinations(names, k):
            label = " + ".join(combo)

            # AND: all selected rules must agree.
            s = evaluate(f, list(combo), horizon, threshold=k)
            if s:
                rows.append({
                    "pool": pool_name,
                    "mode": "AND",
                    "horizon": horizon,
                    "strategy": label,
                    "components": k,
                    "threshold": k,
                    **s,
                })

            # Consensus voting: 1..k votes.
            for threshold in range(1, k + 1):
                if threshold == k:
                    continue
                s = evaluate(f, list(combo), horizon, threshold=threshold)
                if s:
                    rows.append({
                        "pool": pool_name,
                        "mode": "CONSENSUS",
                        "horizon": horizon,
                        "strategy": label,
                        "components": k,
                        "threshold": threshold,
                        **s,
                    })

    return pd.DataFrame(rows)


def cross_pool_search(f, winners10, winners20):
    rows = []

    # Pair each 10D winner with each 20D winner.
    for a in winners10:
        for b in winners20:
            if a == b:
                continue

            names = [a, b]
            label = f"10D:{a} + 20D:{b}"

            for horizon in HORIZONS:
                # Strong agreement: both rules fire.
                s = evaluate(f, names, horizon, threshold=2)
                if s:
                    rows.append({
                        "pool": "CROSS_10D_20D",
                        "mode": "AND",
                        "horizon": horizon,
                        "strategy": label,
                        "components": 2,
                        "threshold": 2,
                        **s,
                    })

                # Either pool signal fires.
                s = evaluate(f, names, horizon, threshold=1)
                if s:
                    rows.append({
                        "pool": "CROSS_10D_20D",
                        "mode": "OR",
                        "horizon": horizon,
                        "strategy": label,
                        "components": 2,
                        "threshold": 1,
                        **s,
                    })

    return pd.DataFrame(rows)


def make_oos_mask(f):
    z = f.copy()
    z["_i"] = z.groupby("symbol").cumcount()
    z["_n"] = z.groupby("symbol")["symbol"].transform("size")
    # Last 25% of each symbol = OOS.
    return z["_i"] >= (z["_n"] * (1 - OOS_FRACTION))


def oos_search(f, ranked):
    if ranked.empty:
        return pd.DataFrame()

    oos = make_oos_mask(f)
    rows = []

    for _, r in ranked.head(100).iterrows():
        # Cross-pool strategy labels are separately recomputed below.
        if r["pool"] == "CROSS_10D_20D":
            continue

        names = [x.strip() for x in str(r["strategy"]).split(" + ")]
        names = [x for x in names if x in RULES]
        if not names:
            continue

        s = evaluate(
            f,
            names,
            int(r["horizon"]),
            int(r["threshold"]),
            oos_mask=oos
        )
        if s:
            rows.append({
                "pool": r["pool"],
                "mode": r["mode"],
                "horizon": int(r["horizon"]),
                "strategy": r["strategy"],
                "threshold": int(r["threshold"]),
                "oos_score": s["score"],
                "oos_signals": s["signals"],
                "oos_win_rate": s["win_rate"],
                "oos_average_return": s["average_return"],
                "oos_median_return": s["median_return"],
                "oos_profit_factor": s["profit_factor"],
            })

    return pd.DataFrame(rows)


def rank_df(df):
    if df.empty:
        return df
    out = df.copy()
    out["target_89_gap"] = (TARGET_SCORE - out["score"]).clip(lower=0)
    out["meets_89"] = out["score"] >= TARGET_SCORE
    out = out.sort_values(
        ["meets_89", "score", "average_return", "win_rate", "signals"],
        ascending=[False, False, False, False, False]
    ).reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    return out


# ---------- Main ----------

def main():
    previous_path = locate_previous_results()
    previous = read_previous_results(previous_path)

    winners10_df = select_winners(previous, 10)
    winners20_df = select_winners(previous, 20)

    if winners10_df.empty:
        raise RuntimeError("No reproducible 10D winners found in previous results.")
    if winners20_df.empty:
        raise RuntimeError("No reproducible 20D winners found in previous results.")

    winners10 = winners10_df["rule"].tolist()
    winners20 = winners20_df["rule"].tolist()

    df, failed = load_data()
    features = build_features(df)

    # Re-test the selected winners on the raw data to avoid trusting only the old summary.
    base_rows = []
    for horizon, names in [(10, winners10), (20, winners20)]:
        for name in names:
            s = evaluate(features, [name], horizon, threshold=1)
            if s:
                base_rows.append({
                    "horizon": horizon,
                    "strategy": name,
                    **s,
                })
    base = pd.DataFrame(base_rows)

    combo10 = combo_search(features, winners10, 10, "WINNERS_10D")
    combo20 = combo_search(features, winners20, 20, "WINNERS_20D")
    cross = cross_pool_search(features, winners10, winners20)

    all_combo = pd.concat(
        [x for x in [combo10, combo20, cross] if not x.empty],
        ignore_index=True
    ) if any(not x.empty for x in [combo10, combo20, cross]) else pd.DataFrame()

    ranked = rank_df(all_combo)
    oos = oos_search(features, ranked)

    # Save machine-readable outputs.
    previous.to_csv(RESULTS_DIR / "source_previous_results.csv", index=False)
    winners10_df.to_csv(RESULTS_DIR / "winners_10d.csv", index=False)
    winners20_df.to_csv(RESULTS_DIR / "winners_20d.csv", index=False)
    base.to_csv(RESULTS_DIR / "winner_retest.csv", index=False)
    combo10.to_csv(RESULTS_DIR / "combo_10d.csv", index=False)
    combo20.to_csv(RESULTS_DIR / "combo_20d.csv", index=False)
    cross.to_csv(RESULTS_DIR / "combo_cross_10d_20d.csv", index=False)
    ranked.to_csv(RESULTS_DIR / "combo_ranked.csv", index=False)
    oos.to_csv(RESULTS_DIR / "combo_oos.csv", index=False)

    top89 = ranked[ranked["score"] >= TARGET_SCORE].head(20).to_dict("records") if not ranked.empty else []
    top = ranked.head(20).to_dict("records") if not ranked.empty else []

    summary = {
        "engine": "Nasdaq Combo Engine V2",
        "method": "previous-winners -> formula recomputation -> AND/OR/consensus -> OOS",
        "previous_results_file": str(previous_path.relative_to(ROOT)) if previous_path.is_relative_to(ROOT) else str(previous_path),
        "data_directory": "daily-data/",
        "horizons": HORIZONS,
        "entry_rule": "signal at T close -> T+1 open",
        "exit_rule": "T+horizon close",
        "winner_pool_size": WINNER_POOL_SIZE,
        "winner_pool_10d": winners10,
        "winner_pool_20d": winners20,
        "records": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "first_date": str(df["date"].min().date()),
        "last_date": str(df["date"].max().date()),
        "failed_files": len(failed),
        "target_score": TARGET_SCORE,
        "count_meeting_89": int((ranked["score"] >= TARGET_SCORE).sum()) if not ranked.empty else 0,
        "top_20": top,
        "top_89": top89,
        "warning": "Score 89 is a research ranking target, not an 89% probability of profit. OOS is a validation check, not a guarantee.",
    }

    (RESULTS_DIR / "combo_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )

    print("NASDAQ COMBO ENGINE V2 COMPLETE")
    print("SOURCE:", previous_path)
    print("10D WINNERS:", winners10)
    print("20D WINNERS:", winners20)
    print("COMBINATIONS:", len(ranked))
    print("SCORE >= 89:", int((ranked["score"] >= TARGET_SCORE).sum()) if not ranked.empty else 0)
    print("RESULTS:", RESULTS_DIR)


if __name__ == "__main__":
    main()

