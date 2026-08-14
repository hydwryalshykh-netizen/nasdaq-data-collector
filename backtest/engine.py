import json
import math
import re
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd


# ============================================================
# Nasdaq Backtest Engine V2
# IMPORTANT:
# This engine ONLY READS daily-data/.
# It does not modify or delete any source data.
# ============================================================

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "daily-data"
RESULTS_DIR = ROOT / "results"
CONFIG_FILE = ROOT / "backtest" / "config.json"


# ============================================================
# CONFIG
# ============================================================

DEFAULT_HORIZONS = [1, 3, 5, 10, 20]


# ============================================================
# LOAD DAILY FILES
# ============================================================

def load_daily_data():
    if not DATA_DIR.exists():
        raise FileNotFoundError(
            f"daily-data directory was not found: {DATA_DIR}"
        )

    files = sorted(DATA_DIR.glob("*.json"))

    if not files:
        raise FileNotFoundError(
            "No JSON files found inside daily-data/"
        )

    all_rows = []
    file_count = 0
    failed_files = []

    for file_path in files:

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            file_count += 1

        except Exception as e:
            failed_files.append({
                "file": file_path.name,
                "error": str(e)
            })
            continue

        # ----------------------------------------------------
        # Your exact structure:
        #
        # {
        #   "date": "2026-01-02",
        #   "records": [...]
        # }
        # ----------------------------------------------------

        file_date = data.get("date")

        records = data.get("records", [])

        if not isinstance(records, list):
            continue

        for record in records:

            if not isinstance(record, dict):
                continue

            symbol = record.get("symbol")

            if not symbol:
                continue

            row = {
                "date": record.get("date") or file_date,
                "symbol": str(symbol).upper().strip(),

                "open": record.get("open"),
                "high": record.get("high"),
                "low": record.get("low"),
                "close": record.get("close"),
                "volume": record.get("volume"),

                "market_cap": record.get("market_cap"),
                "sector": record.get("sector"),
                "industry": record.get("industry"),
                "name": record.get("name"),
            }

            all_rows.append(row)

    if not all_rows:
        raise RuntimeError(
            "No usable records were found in daily-data/"
        )

    df = pd.DataFrame(all_rows)

    return df, file_count, failed_files


# ============================================================
# CLEAN DATA
# ============================================================

def clean_data(df):

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "market_cap"
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    # Remove invalid records
    df = df.dropna(
        subset=[
            "date",
            "symbol",
            "close"
        ]
    )

    df = df[df["close"] > 0]

    # Fill missing OHLC conservatively
    df["open"] = df["open"].fillna(df["close"])
    df["high"] = df["high"].fillna(df["close"])
    df["low"] = df["low"].fillna(df["close"])

    df["volume"] = df["volume"].fillna(0)

    # Remove impossible OHLC records
    df = df[
        (df["high"] >= df["low"]) &
        (df["high"] >= df["close"]) &
        (df["high"] >= df["open"]) &
        (df["low"] <= df["close"]) &
        (df["low"] <= df["open"])
    ]

    # One record per symbol/day
    df = (
        df.sort_values(
            ["symbol", "date"]
        )
        .drop_duplicates(
            ["symbol", "date"],
            keep="last"
        )
    )

    return df.reset_index(drop=True)


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calculate_rsi(series, period=14):

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

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


def calculate_atr(group, period=14):

    previous_close = group["close"].shift(1)

    tr1 = group["high"] - group["low"]

    tr2 = (
        group["high"] - previous_close
    ).abs()

    tr3 = (
        group["low"] - previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    return atr


def add_indicators(df):

    df = df.sort_values(
        ["symbol", "date"]
    ).copy()

    groups = df.groupby(
        "symbol",
        group_keys=False
    )

    # ------------------------------
    # RSI
    # ------------------------------

    df["rsi14"] = groups["close"].transform(
        lambda x: calculate_rsi(x, 14)
    )

    # ------------------------------
    # EMA
    # ------------------------------

    df["ema20"] = groups["close"].transform(
        lambda x: x.ewm(
            span=20,
            adjust=False,
            min_periods=20
        ).mean()
    )

    df["ema50"] = groups["close"].transform(
        lambda x: x.ewm(
            span=50,
            adjust=False,
            min_periods=50
        ).mean()
    )

    df["ema200"] = groups["close"].transform(
        lambda x: x.ewm(
            span=200,
            adjust=False,
            min_periods=200
        ).mean()
    )

    # ------------------------------
    # SMA
    # ------------------------------

    df["sma20"] = groups["close"].transform(
        lambda x: x.rolling(20).mean()
    )

    df["sma50"] = groups["close"].transform(
        lambda x: x.rolling(50).mean()
    )

    # ------------------------------
    # Returns / Momentum
    # ------------------------------

    df["return_1d"] = groups["close"].pct_change(1)

    df["return_5d"] = groups["close"].pct_change(5)

    df["return_20d"] = groups["close"].pct_change(20)

    # ------------------------------
    # Volume
    # ------------------------------

    df["volume_avg20"] = groups["volume"].transform(
        lambda x: x.rolling(20).mean()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_avg20"].replace(0, np.nan)
    )

    # ------------------------------
    # ATR
    # ------------------------------

    df["atr14"] = groups.apply(
        lambda x: calculate_atr(x, 14),
        include_groups=False
    ).reset_index(
        level=0,
        drop=True
    )

    # ------------------------------
    # 20-day breakout
    #
    # IMPORTANT:
    # We use SHIFT(1).
    #
    # This prevents today's price from
    # being used to calculate today's
    # previous 20-day high.
    # ------------------------------

    df["previous_high20"] = groups["high"].transform(
        lambda x: x.shift(1).rolling(20).max()
    )

    df["previous_low20"] = groups["low"].transform(
        lambda x: x.shift(1).rolling(20).min()
    )

    df["breakout20"] = (
        df["close"] >
        df["previous_high20"]
    )

    df["breakdown20"] = (
        df["close"] <
        df["previous_low20"]
    )

    # ------------------------------
    # Trend
    # ------------------------------

    df["trend_up_ema50"] = (
        df["close"] >
        df["ema50"]
    )

    df["trend_up_ema200"] = (
        (df["close"] > df["ema200"]) &
        (df["ema50"] > df["ema200"])
    )

    return df


# ============================================================
# FUTURE RETURNS
# ============================================================

def add_future_returns(df, horizons):

    df = df.sort_values(
        ["symbol", "date"]
    ).copy()

    groups = df.groupby(
        "symbol",
        group_keys=False
    )

    for horizon in horizons:

        df[f"future_close_{horizon}d"] = groups[
            "close"
        ].shift(-horizon)

        df[f"future_return_{horizon}d"] = (
            df[f"future_close_{horizon}d"] /
            df["close"]
        ) - 1

        df[f"future_win_{horizon}d"] = (
            df[f"future_return_{horizon}d"] > 0
        )

    return df


# ============================================================
# INDIVIDUAL INDICATOR TESTS
# ============================================================

def test_indicator(
    df,
    name,
    condition,
    horizons
):

    selected = df.loc[
        condition
    ].copy()

    results = []

    for horizon in horizons:

        column = (
            f"future_return_{horizon}d"
        )

        values = selected[column].dropna()

        if len(values) == 0:
            continue

        positive = (
            values > 0
        ).sum()

        negative = (
            values <= 0
        ).sum()

        avg_return = values.mean()

        median_return = values.median()

        gross_profit = (
            values[values > 0].sum()
        )

        gross_loss = (
            -values[values < 0].sum()
        )

        if gross_loss > 0:
            profit_factor = (
                gross_profit /
                gross_loss
            )
        else:
            profit_factor = math.inf

        results.append({

            "rule": name,

            "horizon_days": horizon,

            "signals": len(values),

            "win_rate": positive / len(values),

            "loss_rate": negative / len(values),

            "average_return": avg_return,

            "median_return": median_return,

            "best_return": values.max(),

            "worst_return": values.min(),

            "profit_factor": profit_factor
        })

    return results


# ============================================================
# RUN INDICATOR RESEARCH
# ============================================================

def run_indicator_tests(df, horizons):

    tests = []

    rules = {

        "RSI < 30":
            df["rsi14"] < 30,

        "RSI > 70":
            df["rsi14"] > 70,

        "RSI 45-70":
            (
                (df["rsi14"] >= 45) &
                (df["rsi14"] <= 70)
            ),

        "Close > EMA20":
            df["close"] > df["ema20"],

        "Close > EMA50":
            df["close"] > df["ema50"],

        "EMA50 > EMA200":
            df["ema50"] > df["ema200"],

        "Volume >= 1.5x":
            df["volume_ratio"] >= 1.5,

        "Volume >= 2x":
            df["volume_ratio"] >= 2,

        "20D Breakout":
            df["breakout20"] == True,

        "20D Momentum > 0":
            df["return_20d"] > 0,

        "20D Momentum > 5%":
            df["return_20d"] > 0.05,

        "20D Momentum > 10%":
            df["return_20d"] > 0.10,

        "20D Breakdown":
            df["breakdown20"] == True
    }

    for name, condition in rules.items():

        valid_condition = (
            condition &
            df["close"].notna()
        )

        tests.extend(
            test_indicator(
                df,
                name,
                valid_condition,
                horizons
            )
        )

    return pd.DataFrame(tests)


# ============================================================
# COMBINED SIGNAL TEST
# ============================================================

def create_baseline_signal(df):

    score = pd.Series(
        0,
        index=df.index,
        dtype="int64"
    )

    # Trend
    score += (
        df["close"] >
        df["ema50"]
    ).astype(int)

    # Momentum
    score += (
        df["return_20d"] > 0
    ).astype(int)

    # Volume
    score += (
        df["volume_ratio"] >= 1.5
    ).astype(int)

    # Breakout
    score += (
        df["breakout20"]
    ).astype(int)

    # Healthy RSI
    score += (
        (df["rsi14"] >= 45) &
        (df["rsi14"] <= 70)
    ).astype(int)

    df["baseline_score"] = score

    # Require at least 3 independent conditions
    df["baseline_signal"] = (
        score >= 3
    )

    return df


def test_baseline(df, horizons):

    selected = df[
        df["baseline_signal"]
    ].copy()

    results = []

    for horizon in horizons:

        column = (
            f"future_return_{horizon}d"
        )

        values = selected[
            column
        ].dropna()

        if len(values) == 0:
            continue

        results.append({

            "strategy":
                "Baseline score >= 3",

            "horizon_days":
                horizon,

            "signals":
                len(values),

            "win_rate":
                float((values > 0).mean()),

            "average_return":
                float(values.mean()),

            "median_return":
                float(values.median()),

            "best_return":
                float(values.max()),

            "worst_return":
                float(values.min())
        })

    return pd.DataFrame(results)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("NASDAQ BACKTEST ENGINE V2")
    print("=" * 70)

    print()
    print("SOURCE:")
    print(DATA_DIR)

    print()
    print("IMPORTANT:")
    print("Source files are READ ONLY.")
    print("Nothing inside daily-data/ will be changed.")

    # --------------------------------------------------------
    # Configuration
    # --------------------------------------------------------

    horizons = DEFAULT_HORIZONS

    if CONFIG_FILE.exists():

        try:

            with open(
                CONFIG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                config = json.load(f)

            horizons = config.get(
                "forward_days",
                DEFAULT_HORIZONS
            )

        except Exception as e:

            print(
                "[WARNING] Could not read config:",
                e
            )

    print()
    print(
        "Forward horizons:",
        horizons
    )

    # --------------------------------------------------------
    # Load
    # --------------------------------------------------------

    print()
    print("Loading daily files...")

    df, file_count, failed_files = (
        load_daily_data()
    )

    print(
        f"Files successfully read: {file_count}"
    )

    print(
        f"Raw records: {len(df):,}"
    )

    # --------------------------------------------------------
    # Clean
    # --------------------------------------------------------

    print()
    print("Cleaning data...")

    df = clean_data(df)

    print(
        f"Clean records: {len(df):,}"
    )

    print(
        f"Unique symbols: {df['symbol'].nunique():,}"
    )

    print(
        f"First date: {df['date'].min().date()}"
    )

    print(
        f"Last date: {df['date'].max().date()}"
    )

    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    print()
    print("Calculating indicators...")

    df = add_indicators(df)

    # --------------------------------------------------------
    # Future outcomes
    # --------------------------------------------------------

    print(
        "Calculating future outcomes..."
    )

    df = add_future_returns(
        df,
        horizons
    )

    # --------------------------------------------------------
    # Indicator tests
    # --------------------------------------------------------

    print()
    print("Testing individual indicators...")

    indicator_results = (
        run_indicator_tests(
            df,
            horizons
        )
    )

    # --------------------------------------------------------
    # Baseline
    # --------------------------------------------------------

    print()
    print(
        "Testing combined baseline..."
    )

    df = create_baseline_signal(
        df
    )

    baseline_results = test_baseline(
        df,
        horizons
    )

    # --------------------------------------------------------
    # Results directory
    # --------------------------------------------------------

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Save indicator results
    # --------------------------------------------------------

    indicator_results.to_csv(
        RESULTS_DIR /
        "indicator_test.csv",
        index=False
    )

    # --------------------------------------------------------
    # Save baseline
    # --------------------------------------------------------

    baseline_results.to_csv(
        RESULTS_DIR /
        "baseline_test.csv",
        index=False
    )

    # --------------------------------------------------------
    # Save detailed research data
    # --------------------------------------------------------

    useful_columns = [

        "date",
        "symbol",

        "open",
        "high",
        "low",
        "close",
        "volume",

        "market_cap",
        "sector",
        "industry",
        "name",

        "rsi14",

        "ema20",
        "ema50",
        "ema200",

        "sma20",
        "sma50",

        "atr14",

        "volume_ratio",

        "return_1d",
        "return_5d",
        "return_20d",

        "breakout20",
        "breakdown20",

        "baseline_score",
        "baseline_signal"
    ]

    for horizon in horizons:

        useful_columns.extend([
            f"future_close_{horizon}d",
            f"future_return_{horizon}d",
            f"future_win_{horizon}d"
        ])

    useful_columns = [
        c for c in useful_columns
        if c in df.columns
    ]

    df[useful_columns].to_csv(
        RESULTS_DIR /
        "research_dataset.csv",
        index=False
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    summary = {

        "engine":
            "Nasdaq Backtest Engine V2",

        "generated_utc":
            datetime.utcnow().isoformat() + "Z",

        "source_directory":
            "daily-data/",

        "source_files_read":
            file_count,

        "failed_files":
            len(failed_files),

        "records_after_cleaning":
            int(len(df)),

        "unique_symbols":
            int(df["symbol"].nunique()),

        "first_date":
            str(df["date"].min().date()),

        "last_date":
            str(df["date"].max().date()),

        "forward_horizons":
            horizons,

        "source_files_modified":
            False,

        "warning":
            "Research statistics only. No investment recommendation."
    }

    with open(
        RESULTS_DIR / "summary.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            summary,
            f,
            indent=2
        )

    # --------------------------------------------------------
    # Print results
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)

    print()

    print(
        "Results saved to:"
    )

    print(
        "results/indicator_test.csv"
    )

    print(
        "results/baseline_test.csv"
    )

    print(
        "results/research_dataset.csv"
    )

    print(
        "results/summary.json"
    )

    print()

    if not indicator_results.empty:

        display = (
            indicator_results
            .sort_values(
                [
                    "horizon_days",
                    "average_return"
                ],
                ascending=[
                    True,
                    False
                ]
            )
            .head(30)
        )

        print(
            "TOP INDICATOR RESULTS:"
        )

        print(
            display.to_string(
                index=False
            )
        )

    print()

    if not baseline_results.empty:

        print(
            "BASELINE RESULTS:"
        )

        print(
            baseline_results.to_string(
                index=False
            )
        )

    print()
    print(
        "Finished safely. daily-data/ was not modified."
    )


if __name__ == "__main__":
    main()
