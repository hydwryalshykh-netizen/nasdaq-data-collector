import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "daily-data"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

HORIZONS = [1, 3, 5, 10, 20]
MIN_HISTORY = 60


def load_data():
    rows = []
    files = sorted(DATA_DIR.glob("*.json"))
    failed = []

    for path in files:
        try:
            with path.open("r", encoding="utf-8") as f:
                obj = json.load(f)

            records = obj.get("records", [])
            if not isinstance(records, list):
                raise ValueError("records is not a list")

            rows.extend(records)

        except Exception as exc:
            failed.append({"file": path.name, "error": str(exc)})

    if not rows:
        raise RuntimeError("No usable records found in daily-data/")

    df = pd.DataFrame(rows)

    required = ["symbol", "date", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise RuntimeError(f"Missing required column: {col}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required).copy()

    df = df[
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
    ]

    # Basic OHLC validation.
    df = df[df["high"] >= df[["open", "close", "low"]].max(axis=1)]
    df = df[df["low"] <= df[["open", "close", "high"]].min(axis=1)]

    df = df.drop_duplicates(
        subset=["symbol", "date"],
        keep="last"
    )

    df = df.sort_values(
        ["symbol", "date"]
    ).reset_index(drop=True)

    return df, files, failed


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

    result = result.where(avg_loss != 0, 100)
    result = result.where(
        ~((avg_gain == 0) & (avg_loss == 0)),
        50
    )

    return result


def atr(high, low, close, period=14):
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def adx(high, low, close, period=14):
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0),
        0.0
    )

    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0),
        0.0
    )

    true_range = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1
    ).max(axis=1)

    tr_avg = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    plus_avg = plus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    minus_avg = minus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    plus_di = 100 * plus_avg / tr_avg.replace(0, np.nan)
    minus_di = 100 * minus_avg / tr_avg.replace(0, np.nan)

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    return dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def add_features(group):
    g = group.sort_values("date").copy()

    close = g["close"]
    high = g["high"]
    low = g["low"]
    open_ = g["open"]
    volume = g["volume"]

    # -----------------------------
    # Trend
    # -----------------------------
    g["ema20"] = close.ewm(
        span=20,
        adjust=False,
        min_periods=20
    ).mean()

    g["ema50"] = close.ewm(
        span=50,
        adjust=False,
        min_periods=50
    ).mean()

    g["sma20"] = close.rolling(
        20,
        min_periods=20
    ).mean()

    # -----------------------------
    # Momentum / volatility
    # -----------------------------
    g["rsi14"] = rsi(close, 14)

    g["atr14"] = atr(
        high,
        low,
        close,
        14
    )

    g["atr_pct"] = g["atr14"] / close

    g["momentum5"] = close / close.shift(5) - 1
    g["momentum20"] = close / close.shift(20) - 1

    # -----------------------------
    # Volume
    # -----------------------------
    g["volume_avg20"] = volume.rolling(
        20,
        min_periods=20
    ).mean()

    g["volume_ratio20"] = (
        volume
        / g["volume_avg20"].replace(0, np.nan)
    )

    # -----------------------------
    # Support / resistance
    #
    # IMPORTANT:
    # shift(1) means today's signal
    # cannot use today's high/low.
    # -----------------------------
    g["resistance20"] = high.shift(1).rolling(
        20,
        min_periods=20
    ).max()

    g["support20"] = low.shift(1).rolling(
        20,
        min_periods=20
    ).min()

    g["resistance50"] = high.shift(1).rolling(
        50,
        min_periods=50
    ).max()

    g["support50"] = low.shift(1).rolling(
        50,
        min_periods=50
    ).min()

    g["resistance100"] = high.shift(1).rolling(
        100,
        min_periods=100
    ).max()

    g["support100"] = low.shift(1).rolling(
        100,
        min_periods=100
    ).min()

    g["distance_support100"] = (
        close / g["support100"] - 1
    )

    g["distance_resistance50"] = (
        close / g["resistance50"] - 1
    )

    g["distance_resistance100"] = (
        close / g["resistance100"] - 1
    )

    g["distance_support20"] = (
        close / g["support20"] - 1
    )

    g["distance_resistance20"] = (
        close / g["resistance20"] - 1
    )

    g["distance_support50"] = (
        close / g["support50"] - 1
    )

    # -----------------------------
    # Breakout / breakdown
    # -----------------------------
    g["breakout20"] = (
        close > g["resistance20"]
    )

    g["breakdown20"] = (
        close < g["support20"]
    )

    # -----------------------------
    # Bollinger Bands
    # -----------------------------
    bb_mid = close.rolling(
        20,
        min_periods=20
    ).mean()

    bb_std = close.rolling(
        20,
        min_periods=20
    ).std(ddof=0)

    g["bb_mid"] = bb_mid
    g["bb_upper"] = bb_mid + 2 * bb_std
    g["bb_lower"] = bb_mid - 2 * bb_std

    bb_width = (
        g["bb_upper"] - g["bb_lower"]
    ).replace(0, np.nan)

    g["bb_position"] = (
        close - g["bb_lower"]
    ) / bb_width

    g["bb_reclaim"] = (
        (close > g["bb_lower"])
        & (close.shift(1) <= g["bb_lower"].shift(1))
    )

    # -----------------------------
    # ADX
    # -----------------------------
    g["adx14"] = adx(
        high,
        low,
        close,
        14
    )

    # -----------------------------
    # Stochastic
    # -----------------------------
    lowest14 = low.rolling(
        14,
        min_periods=14
    ).min()

    highest14 = high.rolling(
        14,
        min_periods=14
    ).max()

    denominator = (
        highest14 - lowest14
    ).replace(0, np.nan)

    g["stoch_k"] = (
        100
        * (close - lowest14)
        / denominator
    )

    g["stoch_d"] = g["stoch_k"].rolling(
        3,
        min_periods=3
    ).mean()

    g["stoch_rebound"] = (
        (g["stoch_k"] > g["stoch_d"])
        & (
            g["stoch_k"].shift(1)
            <= g["stoch_d"].shift(1)
        )
    )

    # -----------------------------
    # Previous-day Pivot
    # -----------------------------
    previous_high = high.shift(1)
    previous_low = low.shift(1)
    previous_close = close.shift(1)

    pivot = (
        previous_high
        + previous_low
        + previous_close
    ) / 3

    g["pivot"] = pivot

    g["pivot_r1"] = (
        2 * pivot
        - previous_low
    )

    g["pivot_s1"] = (
        2 * pivot
        - previous_high
    )

    g["pivot_r2"] = (
        pivot
        + (previous_high - previous_low)
    )

    g["pivot_s2"] = (
        pivot
        - (previous_high - previous_low)
    )

    # -----------------------------
    # Candlestick reversal signals
    # -----------------------------
    body = (close - open_).abs()

    candle_range = (
        high - low
    ).replace(0, np.nan)

    lower_wick = (
        np.minimum(open_, close) - low
    )

    upper_wick = (
        high - np.maximum(open_, close)
    )

    g["hammer"] = (
        (lower_wick >= body * 2)
        & (upper_wick <= body)
        & ((body / candle_range) <= 0.40)
    )

    previous_open = open_.shift(1)
    previous_close = close.shift(1)

    g["bullish_engulfing"] = (
        (previous_close < previous_open)
        & (close > open_)
        & (open_ <= previous_close)
        & (close >= previous_open)
    )

    # -----------------------------
    # Price-structure / bottom signals
    # -----------------------------
    # All rolling extrema use only prior bars for the signal decision.
    prior10_low = low.shift(1).rolling(10, min_periods=10).min()
    prior10_high = high.shift(1).rolling(10, min_periods=10).max()
    prior20_low = low.shift(1).rolling(20, min_periods=20).min()
    prior20_high = high.shift(1).rolling(20, min_periods=20).max()

    g["near_support100"] = (
        (g["distance_support100"] >= 0)
        & (g["distance_support100"] <= 0.05)
    )

    g["near_resistance50"] = (
        (g["distance_resistance50"] <= 0)
        & (g["distance_resistance50"] >= -0.03)
    )

    g["near_resistance100"] = (
        (g["distance_resistance100"] <= 0)
        & (g["distance_resistance100"] >= -0.03)
    )

    # Higher-low structure: today's low is above the prior 10-day low
    # while price has recently recovered from a local low.
    g["higher_low"] = (
        (low > prior10_low)
        & (close > close.shift(1))
        & (close > g["support20"])
    )

    # Recovery from a recent 20D low without using today's low to define it.
    g["bottom_reclaim"] = (
        (close > prior20_low)
        & (close.shift(1) <= prior20_low.shift(1))
    )

    # Rejection of support: intraday low reaches support area but closes back above it.
    g["support_rejection"] = (
        (low <= g["support20"] * 1.01)
        & (close > g["support20"])
        & (close > open_)
    )

    # Breakout confirmation / retest style conditions.
    g["breakout_retest"] = (
        (close > g["resistance20"])
        & (low <= g["resistance20"] * 1.02)
        & (close > open_)
    )

    g["resistance_rejection"] = (
        (high >= g["resistance20"] * 0.99)
        & (close < g["resistance20"])
        & (close < open_)
    )

    # -----------------------------
    # Individual research signals
    # -----------------------------
    g["rsi_oversold"] = g["rsi14"] < 30
    g["rsi_overbought"] = g["rsi14"] > 70

    g["above_ema20"] = close > g["ema20"]
    g["above_ema50"] = close > g["ema50"]

    g["volume_1_5x"] = g["volume_ratio20"] >= 1.5
    g["volume_2x"] = g["volume_ratio20"] >= 2.0

    g["momentum_positive"] = g["momentum20"] > 0
    g["momentum_5pct"] = g["momentum20"] > 0.05
    g["momentum_10pct"] = g["momentum20"] > 0.10

    g["adx_strong"] = g["adx14"] >= 25

    g["stoch_oversold"] = g["stoch_k"] < 20

    g["near_support"] = (
        (g["distance_support20"] >= 0)
        & (g["distance_support20"] <= 0.03)
    )

    g["near_resistance"] = (
        (g["distance_resistance20"] <= 0)
        & (g["distance_resistance20"] >= -0.03)
    )

    # Useful for breakdown/reversal research:
    # after breaking the 20D low, price can still be
    # close to the deeper 50D support.
    g["near_support50"] = (
        (g["distance_support50"] >= 0)
        & (g["distance_support50"] <= 0.05)
    )

    g["below_bb_lower"] = (
        close < g["bb_lower"]
    )

    g["above_pivot"] = (
        close > g["pivot"]
    )

    g["below_pivot"] = (
        close < g["pivot"]
    )

    # -----------------------------
    # Composite research scores
    #
    # These are NOT recommendations.
    # They exist to discover useful
    # combinations statistically.
    # -----------------------------
    g["trend_score"] = (
        g["above_ema20"].astype("int8")
        + g["above_ema50"].astype("int8")
        + g["momentum_positive"].astype("int8")
        + g["adx_strong"].astype("int8")
        + g["above_pivot"].astype("int8")
    )

    g["reversal_score"] = (
        g["rsi_oversold"].astype("int8")
        + g["stoch_oversold"].astype("int8")
        + g["near_support"].astype("int8")
        + g["below_bb_lower"].astype("int8")
        + g["hammer"].astype("int8")
        + g["bullish_engulfing"].astype("int8")
        + g["breakdown20"].astype("int8")
    )

    # Main candidate score.
    g["main_score"] = (
        g["above_ema20"].astype("int8")
        + g["above_ema50"].astype("int8")
        + g["momentum_positive"].astype("int8")
        + g["volume_1_5x"].astype("int8")
        + g["above_pivot"].astype("int8")
        + g["adx_strong"].astype("int8")
        + g["near_support"].astype("int8")
    )

    # -----------------------------
    # NEXT-DAY OPEN ENTRY
    #
    # Signal is known at T close.
    # Entry = T+1 OPEN.
    # Exit = T+horizon CLOSE.
    # -----------------------------
    g["entry_open"] = open_.shift(-1)

    for horizon in HORIZONS:
        g[f"exit_close_{horizon}"] = close.shift(
            -horizon
        )

        g[f"forward_return_{horizon}"] = (
            g[f"exit_close_{horizon}"]
            / g["entry_open"]
            - 1
        )

    return g


def build_features(df):
    pieces = []

    for _, group in df.groupby(
        "symbol",
        sort=False
    ):
        if len(group) < (
            MIN_HISTORY
            + max(HORIZONS)
            + 1
        ):
            continue

        pieces.append(
            add_features(group)
        )

    if not pieces:
        raise RuntimeError(
            "No symbol has enough history "
            "for the new test."
        )

    return pd.concat(
        pieces,
        ignore_index=True
    )


def summarize(mask, df, label):
    selected = df.loc[mask.fillna(False)].copy()
    rows = []

    for horizon in HORIZONS:
        returns = pd.to_numeric(
            selected[f"forward_return_{horizon}"],
            errors="coerce"
        ).dropna()

        if returns.empty:
            continue

        positive = returns[returns > 0]
        negative = returns[returns < 0]
        gross_profit = positive.sum()
        gross_loss = -negative.sum()
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else np.nan

        # Robustness diagnostics: determine whether the mean is dominated by
        # a tiny number of extreme winners. This is a research diagnostic,
        # not a trading performance guarantee.
        trim_n = max(1, int(len(returns) * 0.01)) if len(returns) >= 20 else 0
        if trim_n:
            without_top = returns.sort_values().iloc[:-trim_n]
            avg_ex_top1 = without_top.mean()
            top1_contribution = (
                positive.nlargest(trim_n).sum() / positive.sum()
                if positive.sum() > 0 else np.nan
            )
        else:
            avg_ex_top1 = returns.mean()
            top1_contribution = np.nan

        std_return = returns.std(ddof=1) if len(returns) > 1 else np.nan
        median = returns.median()

        # Conservative research score: rewards positive median/mean,
        # profit factor > 1 and win rate > 50%, while penalizing outlier
        # dependence and very small samples. It is only for ranking candidates.
        win_rate = (returns > 0).mean()
        sample_factor = min(1.0, len(returns) / 1000.0)
        pf_factor = min(1.5, max(0.0, profit_factor if np.isfinite(profit_factor) else 0.0)) / 1.5
        edge_factor = min(1.0, max(0.0, (win_rate - 0.45) / 0.15))
        median_factor = min(1.0, max(0.0, median / 0.02))
        robust_mean_factor = min(1.0, max(0.0, avg_ex_top1 / 0.02))
        outlier_penalty = (
            min(1.0, max(0.0, top1_contribution - 0.50) / 0.50)
            if np.isfinite(top1_contribution) else 0.0
        )
        robustness_score = 100 * sample_factor * (
            0.30 * pf_factor
            + 0.25 * edge_factor
            + 0.20 * median_factor
            + 0.25 * robust_mean_factor
        ) * (1 - 0.50 * outlier_penalty)

        rows.append({
            "rule": label,
            "horizon_days": horizon,
            "signals": int(len(returns)),
            "win_rate": float(win_rate),
            "loss_rate": float((returns < 0).mean()),
            "average_return": float(returns.mean()),
            "median_return": float(median),
            "p10_return": float(returns.quantile(0.10)),
            "p25_return": float(returns.quantile(0.25)),
            "p75_return": float(returns.quantile(0.75)),
            "p90_return": float(returns.quantile(0.90)),
            "best_return": float(returns.max()),
            "worst_return": float(returns.min()),
            "std_return": float(std_return) if np.isfinite(std_return) else None,
            "average_ex_top_1pct": float(avg_ex_top1),
            "top_1pct_profit_contribution": float(top1_contribution) if np.isfinite(top1_contribution) else None,
            "profit_factor": float(profit_factor) if np.isfinite(profit_factor) else None,
            "robustness_score": float(robustness_score),
        })

    return rows


def main():
    df, files, failed = load_data()

    features = build_features(df)

    # --------------------------------
    # Individual indicators
    # --------------------------------
    rules = {
        "RSI < 30":
            features["rsi_oversold"],

        "RSI > 70":
            features["rsi_overbought"],

        "Close > EMA20":
            features["above_ema20"],

        "Close > EMA50":
            features["above_ema50"],

        "Volume >= 1.5x":
            features["volume_1_5x"],

        "Volume >= 2x":
            features["volume_2x"],

        "20D Breakout":
            features["breakout20"],

        "20D Breakdown":
            features["breakdown20"],

        "20D Momentum > 0":
            features["momentum_positive"],

        "20D Momentum > 5%":
            features["momentum_5pct"],

        "20D Momentum > 10%":
            features["momentum_10pct"],

        "ADX >= 25":
            features["adx_strong"],

        "Stochastic < 20":
            features["stoch_oversold"],

        "Near 20D Support":
            features["near_support"],

        "Near 20D Resistance":
            features["near_resistance"],

        "Near 50D Support":
            features["near_support50"],

        "Near 100D Support":
            features["near_support100"],

        "Near 50D Resistance":
            features["near_resistance50"],

        "Near 100D Resistance":
            features["near_resistance100"],

        "Higher Low":
            features["higher_low"],

        "Bottom Reclaim":
            features["bottom_reclaim"],

        "Support Rejection":
            features["support_rejection"],

        "Breakout Retest":
            features["breakout_retest"],

        "Resistance Rejection":
            features["resistance_rejection"],

        "Below Bollinger Lower":
            features["below_bb_lower"],

        "Bollinger Reclaim":
            features["bb_reclaim"],

        "Bullish Hammer":
            features["hammer"],

        "Bullish Engulfing":
            features["bullish_engulfing"],

        "Above Previous Pivot":
            features["above_pivot"],

        "Below Previous Pivot":
            features["below_pivot"],
    }

    indicator_rows = []

    for label, mask in rules.items():
        indicator_rows.extend(
            summarize(
                mask,
                features,
                label
            )
        )

    pd.DataFrame(
        indicator_rows
    ).to_csv(
        RESULTS_DIR / "indicator_test.csv",
        index=False
    )

    # --------------------------------
    # Candidate strategy combinations
    # --------------------------------
    combinations = {
        # Trend candidates
        "Trend: EMA50 + Momentum":
            features["above_ema50"] & features["momentum_positive"],
        "Trend: EMA50 + Momentum + Volume":
            features["above_ema50"] & features["momentum_positive"] & features["volume_1_5x"],
        "Trend: EMA50 + ADX":
            features["above_ema50"] & features["adx_strong"],

        # Bottom / support candidates
        "Reversal: 50D Support + RSI":
            features["near_support50"] & features["rsi_oversold"],
        "Reversal: 50D Support + Higher Low":
            features["near_support50"] & features["higher_low"],
        "Reversal: 50D Support + Volume":
            features["near_support50"] & features["volume_1_5x"],
        "Reversal: 50D Support + RSI + Higher Low":
            features["near_support50"] & features["rsi_oversold"] & features["higher_low"],
        "Reversal: 50D Support + Hammer":
            features["near_support50"] & features["hammer"],
        "Reversal: 50D Support + Bull Engulf":
            features["near_support50"] & features["bullish_engulfing"],
        "Reversal: Support Rejection + RSI":
            features["support_rejection"] & features["rsi_oversold"],
        "Reversal: Support Rejection + Volume":
            features["support_rejection"] & features["volume_1_5x"],
        "Reversal: 100D Support + RSI":
            features["near_support100"] & features["rsi_oversold"],
        "Reversal: Bottom Reclaim + Support":
            features["bottom_reclaim"] & features["near_support50"],

        # Existing useful reversal candidates retained for comparison
        "Reversal: RSI + 20D Support":
            features["rsi_oversold"] & features["near_support"],
        "Reversal: RSI + Stoch + Support":
            features["rsi_oversold"] & features["stoch_oversold"] & features["near_support"],
        "Reversal: Hammer + Support":
            features["hammer"] & features["near_support"],
        "Reversal: Bull Engulf + Support":
            features["bullish_engulfing"] & features["near_support"],

        # Secondary breakdown trigger — not treated as the primary strategy
        "Secondary: Breakdown + RSI":
            features["breakdown20"] & features["rsi_oversold"],
        "Secondary: Breakdown + RSI + Volume":
            features["breakdown20"] & features["rsi_oversold"] & features["volume_1_5x"],
        "Secondary: Breakdown + 50D Support":
            features["breakdown20"] & features["near_support50"],

        # Breakout / resistance candidates
        "Breakout: Breakout + Volume":
            features["breakout20"] & features["volume_1_5x"],
        "Breakout: Breakout + EMA50 + Volume":
            features["breakout20"] & features["above_ema50"] & features["volume_1_5x"],
        "Breakout: Retest + Volume":
            features["breakout_retest"] & features["volume_1_5x"],
        "Resistance: Rejection + RSI":
            features["resistance_rejection"] & features["rsi_overbought"],
    }

    combination_rows = []

    for label, mask in combinations.items():
        combination_rows.extend(
            summarize(
                mask,
                features,
                label
            )
        )

    pd.DataFrame(
        combination_rows
    ).to_csv(
        RESULTS_DIR / "strategy_tests.csv",
        index=False
    )

    # --------------------------------
    # Main research score
    # --------------------------------
    main_mask = (
        features["main_score"] >= 4
    )

    baseline_rows = summarize(
        main_mask,
        features,
        "Main score >= 4"
    )

    pd.DataFrame(
        baseline_rows
    ).to_csv(
        RESULTS_DIR / "baseline_test.csv",
        index=False
    )

    # --------------------------------
    # Summary
    # --------------------------------
    summary = {
        "engine":
            "Nasdaq Backtest Engine V4 Research",

        "generated_utc":
            pd.Timestamp.now(
                tz="UTC"
            ).isoformat(),

        "source_directory":
            "daily-data/",

        "source_files_read":
            len(files),

        "failed_files":
            len(failed),

        "records_after_cleaning":
            int(len(df)),

        "unique_symbols":
            int(df["symbol"].nunique()),

        "first_date":
            str(df["date"].min().date()),

        "last_date":
            str(df["date"].max().date()),

        "forward_horizons":
            HORIZONS,

        "entry_rule":
            "Signal at T close; enter at T+1 open",

        "exit_rule":
            "Exit at T+horizon close",

        "minimum_history_days":
            MIN_HISTORY,

        "features_included": [
            "RSI 14",
            "EMA 20",
            "EMA 50",
            "SMA 20",
            "ATR 14",
            "Volume Ratio 20",
            "20D Support",
            "20D Resistance",
            "50D Support",
            "50D Resistance",
            "100D Support",
            "100D Resistance",
            "20D Breakout",
            "20D Breakdown",
            "5D Momentum",
            "20D Momentum",
            "Bollinger Bands",
            "ADX 14",
            "Stochastic",
            "Previous-day Pivot",
            "Pivot R1/R2",
            "Pivot S1/S2",
            "Hammer",
            "Bullish Engulfing",
            "Higher Low",
            "Bottom Reclaim",
            "Support Rejection",
            "Breakout Retest",
            "Resistance Rejection"
        ],

        "source_files_modified":
            False,

        "warning":
            "Research statistics only. "
            "No investment recommendation."
    }

    with (
        RESULTS_DIR
        / "summary.json"
    ).open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            summary,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        "Nasdaq Backtest Engine V4 Research completed."
    )

    print(
        f"Rows: {len(df):,}"
    )

    print(
        f"Symbols: {df['symbol'].nunique():,}"
    )

    print(
        "Results written to results/."
    )


if __name__ == "__main__":
    main()
