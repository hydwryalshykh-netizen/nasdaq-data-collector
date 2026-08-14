# Nasdaq Combo-Only Engine V3
# Purpose:
#   Take ONLY the three already-proven winner rules and test whether
#   combining them produces a better strategy.
#
# IMPORTANT:
#   This engine does NOT re-test the three rules as standalone strategies.
#   Their previous scores are used only as the baseline:
#       A = 50D Support + Bull Engulf       71.89
#       B = 50D Support + Higher Low        68.02
#       C = Breakdown + 50D Support         64.48
#
# It tests only:
#   A+B
#   A+C
#   B+C
#   A+B+C
#   2-of-3 consensus
#   C -> B -> A ordered sequence
#
# Pure deterministic formulas. No AI/ML.

import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = (
    Path(__file__).resolve().parents[1]
    if Path(__file__).parent.name == "engines"
    else Path(__file__).resolve().parent
)

DATA_DIR = ROOT / "daily-data"
RESULTS_DIR = ROOT / "results_combo_v3"
RESULTS_DIR.mkdir(exist_ok=True)

HORIZONS = [10, 20]
MIN_HISTORY = 120
MIN_SIGNALS = 100
OOS_FRACTION = 0.25

BASELINE = {
    "A_50D_SUPPORT_BULL_ENGULF": 71.89,
    "B_50D_SUPPORT_HIGHER_LOW": 68.02,
    "C_BREAKDOWN_50D_SUPPORT": 64.48,
}

SUPPORT_TOLERANCE_50D = 0.05
SUPPORT_TOLERANCE_20D = 0.03

# Sequence gaps to test only for the combined C -> B -> A setup.
SEQ_WINDOWS = [3, 5, 10, 20]


# ============================================================
# DATA
# ============================================================

def load_data():
    rows = []
    failed = []

    if not DATA_DIR.exists():
        raise RuntimeError(f"Missing data directory: {DATA_DIR}")

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

    required = [
        "symbol", "date", "open",
        "high", "low", "close", "volume"
    ]

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Missing columns: {missing}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=required).copy()

    df = df[
        (df["open"] > 0) &
        (df["high"] > 0) &
        (df["low"] > 0) &
        (df["close"] > 0) &
        (df["volume"] >= 0)
    ]

    df = df[
        df["high"] >=
        df[["open", "close", "low"]].max(axis=1)
    ]

    df = df[
        df["low"] <=
        df[["open", "close", "high"]].min(axis=1)
    ]

    df = (
        df.drop_duplicates(["symbol", "date"], keep="last")
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )

    return df, failed


# ============================================================
# FEATURES REQUIRED BY THE THREE EXISTING WINNERS
# ============================================================

def add_features(g):
    g = g.sort_values("date").copy()

    o = g["open"]
    h = g["high"]
    l = g["low"]
    c = g["close"]

    # Prior 50D support. Signal day is excluded.
    g["support50"] = (
        l.shift(1)
        .rolling(50, min_periods=50)
        .min()
    )

    g["near_support50"] = (
        (c >= g["support50"]) &
        (c <= g["support50"] * (1 + SUPPORT_TOLERANCE_50D))
    )

    # Prior 20D support for the Breakdown component.
    g["support20"] = (
        l.shift(1)
        .rolling(20, min_periods=20)
        .min()
    )

    # A: Bullish Engulfing.
    po = o.shift(1)
    pc = c.shift(1)

    g["bullish_engulfing"] = (
        (pc < po) &
        (c > o) &
        (o <= pc) &
        (c >= po)
    )

    # B: Higher Low.
    prior10_low = (
        l.shift(1)
        .rolling(10, min_periods=10)
        .min()
    )

    g["higher_low"] = (
        (l > prior10_low) &
        (c > c.shift(1)) &
        (c > g["support50"])
    )

    # C: Breakdown + 50D Support.
    g["breakdown20"] = c < g["support20"]

    # The original C rule requires BOTH:
    # breakdown20 AND near_support50.
    g["A"] = (
        g["near_support50"] &
        g["bullish_engulfing"]
    )

    g["B"] = (
        g["near_support50"] &
        g["higher_low"]
    )

    g["C"] = (
        g["breakdown20"] &
        g["near_support50"]
    )

    # Entry = next session open after the signal.
    g["entry_open"] = o.shift(-1)

    for hz in HORIZONS:
        g[f"forward_return_{hz}"] = (
            c.shift(-hz) / g["entry_open"] - 1
        )

    return g


def build_features(df):
    parts = []

    for _, grp in df.groupby("symbol", sort=False):
        if len(grp) >= MIN_HISTORY + max(HORIZONS) + 1:
            parts.append(add_features(grp))

    if not parts:
        raise RuntimeError("No symbol has enough history.")

    return pd.concat(parts, ignore_index=True)


# ============================================================
# STATISTICS
# ============================================================

def max_consecutive_losses(returns):
    streak = 0
    best = 0

    for x in pd.Series(returns).dropna():
        if x < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0

    return int(best)


def stats(returns):
    r = pd.Series(returns).dropna().astype(float)

    if len(r) < MIN_SIGNALS:
        return None

    wins = r[r > 0]
    losses = r[r < 0]

    gross_profit = wins.sum()
    gross_loss = -losses.sum()

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else np.inf
    )

    win_rate = float((r > 0).mean())
    mean_return = float(r.mean())
    median_return = float(r.median())

    trim_n = max(1, int(len(r) * 0.01))

    robust_mean = float(
        r.sort_values().iloc[:-trim_n].mean()
        if len(r) > trim_n
        else r.mean()
    )

    # Same style of research score as the previous engine,
    # but ONLY for combined candidates.
    sample_factor = min(1.0, len(r) / 1000.0)

    pf_factor = min(
        1.0,
        max(0.0, pf / 1.5)
    )

    win_factor = min(
        1.0,
        max(0.0, (win_rate - 0.45) / 0.15)
    )

    median_factor = min(
        1.0,
        max(0.0, median_return / 0.02)
    )

    mean_factor = min(
        1.0,
        max(0.0, mean_return / 0.02)
    )

    robust_factor = min(
        1.0,
        max(0.0, robust_mean / 0.02)
    )

    score = 100 * sample_factor * (
        0.25 * pf_factor +
        0.20 * win_factor +
        0.15 * median_factor +
        0.20 * mean_factor +
        0.20 * robust_factor
    )

    return {
        "signals": int(len(r)),
        "win_rate": win_rate,
        "average_return": mean_return,
        "median_return": median_return,
        "profit_factor":
            None if not np.isfinite(pf) else float(pf),
        "worst_return": float(r.min()),
        "best_return": float(r.max()),
        "robust_mean": robust_mean,
        "max_consecutive_losses":
            max_consecutive_losses(r),
        "score": float(score),
    }


def evaluate_mask(f, mask, horizon, oos_mask=None):
    m = mask.fillna(False).astype(bool)

    if oos_mask is not None:
        m &= oos_mask

    returns = f.loc[m, f"forward_return_{horizon}"]

    return stats(returns)


# ============================================================
# ONLY THE COMBINATIONS
# ============================================================

def get_masks(f):
    return {
        "A_50D_SUPPORT_BULL_ENGULF":
            f["A"].fillna(False).astype(bool),

        "B_50D_SUPPORT_HIGHER_LOW":
            f["B"].fillna(False).astype(bool),

        "C_BREAKDOWN_50D_SUPPORT":
            f["C"].fillna(False).astype(bool),
    }


def evaluate_combo(f, names, horizon, mode):
    masks = get_masks(f)

    if mode == "AND":
        m = masks[names[0]].copy()

        for name in names[1:]:
            m &= masks[name]

        return evaluate_mask(f, m, horizon)

    if mode == "OR":
        m = masks[names[0]].copy()

        for name in names[1:]:
            m |= masks[name]

        return evaluate_mask(f, m, horizon)

    if mode == "CONSENSUS_2_OF_3":
        votes = sum(
            masks[name].astype(int)
            for name in names
        )
        return evaluate_mask(
            f, votes >= 2, horizon
        )

    if mode == "CONSENSUS_3_OF_3":
        votes = sum(
            masks[name].astype(int)
            for name in names
        )
        return evaluate_mask(
            f, votes >= 3, horizon
        )

    raise ValueError(f"Unknown mode: {mode}")


# ============================================================
# ORDERED C -> B -> A
# ============================================================

def event_after(previous_event, next_event, max_gap):
    """
    Returns TRUE on next_event when previous_event occurred
    1..max_gap sessions earlier.
    """

    a = previous_event.fillna(False).astype(bool).to_numpy()
    b = next_event.fillna(False).astype(bool).to_numpy()

    result = np.zeros(len(a), dtype=bool)

    for i in range(len(a)):
        if not b[i]:
            continue

        start = max(0, i - max_gap)

        if a[start:i].any():
            result[i] = True

    return pd.Series(
        result,
        index=previous_event.index
    )


def sequence_cba(f, max_gap):
    masks = get_masks(f)

    c_then_b = event_after(
        masks["C_BREAKDOWN_50D_SUPPORT"],
        masks["B_50D_SUPPORT_HIGHER_LOW"],
        max_gap,
    )

    b_then_a = event_after(
        c_then_b,
        masks["A_50D_SUPPORT_BULL_ENGULF"],
        max_gap,
    )

    return b_then_a.fillna(False).astype(bool)


# ============================================================
# OOS
# ============================================================

def make_oos_mask(f):
    z = f.copy()

    z["_i"] = z.groupby("symbol").cumcount()
    z["_n"] = z.groupby("symbol")["symbol"].transform("size")

    return z["_i"] >= (
        z["_n"] * (1 - OOS_FRACTION)
    )


# ============================================================
# RANKING / BASELINE COMPARISON
# ============================================================

def add_baseline_comparison(df):
    if df.empty:
        return df

    x = df.copy()

    # The baseline is NOT recomputed here.
    # These are the already-known scores supplied by the user.
    x["baseline_best_score"] = 71.89

    x["improvement_vs_best_baseline"] = (
        x["score"] - x["baseline_best_score"]
    )

    x["beats_best_baseline"] = (
        x["score"] > x["baseline_best_score"]
    )

    x["baseline_A_score"] = BASELINE[
        "A_50D_SUPPORT_BULL_ENGULF"
    ]

    x["baseline_B_score"] = BASELINE[
        "B_50D_SUPPORT_HIGHER_LOW"
    ]

    x["baseline_C_score"] = BASELINE[
        "C_BREAKDOWN_50D_SUPPORT"
    ]

    return x.sort_values(
        [
            "beats_best_baseline",
            "score",
            "robust_mean",
            "median_return",
            "signals",
        ],
        ascending=False,
    ).reset_index(drop=True)


# ============================================================
# MAIN
# ============================================================

def main():
    print("NASDAQ COMBO-ONLY ENGINE V3")
    print("=" * 60)
    print("Standalone A/B/C tests are DISABLED.")
    print("Only combinations are evaluated.")
    print()

    df, failed = load_data()

    print(f"Records: {len(df):,}")
    print(f"Symbols: {df['symbol'].nunique():,}")
    print(
        f"Dates: {df['date'].min().date()} -> "
        f"{df['date'].max().date()}"
    )

    features = build_features(df)

    names = [
        "A_50D_SUPPORT_BULL_ENGULF",
        "B_50D_SUPPORT_HIGHER_LOW",
        "C_BREAKDOWN_50D_SUPPORT",
    ]

    rows = []

    # --------------------------------------------------------
    # PAIRS + ALL THREE
    # --------------------------------------------------------

    combos = [
        (("A_50D_SUPPORT_BULL_ENGULF",
          "B_50D_SUPPORT_HIGHER_LOW"), "AND"),

        (("A_50D_SUPPORT_BULL_ENGULF",
          "C_BREAKDOWN_50D_SUPPORT"), "AND"),

        (("B_50D_SUPPORT_HIGHER_LOW",
          "C_BREAKDOWN_50D_SUPPORT"), "AND"),

        (("A_50D_SUPPORT_BULL_ENGULF",
          "B_50D_SUPPORT_HIGHER_LOW",
          "C_BREAKDOWN_50D_SUPPORT"), "AND"),

        (tuple(names), "OR"),

        (tuple(names), "CONSENSUS_2_OF_3"),

        (tuple(names), "CONSENSUS_3_OF_3"),
    ]

    for combo, mode in combos:
        label = " + ".join(combo)

        for horizon in HORIZONS:
            s = evaluate_combo(
                features,
                combo,
                horizon,
                mode,
            )

            if s:
                rows.append({
                    "family": "COMBINATION",
                    "mode": mode,
                    "horizon": horizon,
                    "strategy": label,
                    "components": len(combo),
                    **s,
                })

    # --------------------------------------------------------
    # ONLY THE FULL ORDERED SCENARIO
    # C -> B -> A
    # --------------------------------------------------------

    for gap in SEQ_WINDOWS:
        mask = sequence_cba(features, gap)

        for horizon in HORIZONS:
            s = evaluate_mask(
                features,
                mask,
                horizon,
            )

            if s:
                rows.append({
                    "family": "ORDERED_SEQUENCE",
                    "mode": "C_TO_B_TO_A",
                    "horizon": horizon,
                    "strategy": (
                        "C Breakdown+50D Support -> "
                        "B Higher Low -> "
                        "A Bull Engulf"
                    ),
                    "components": 3,
                    "max_gap_sessions": gap,
                    **s,
                })

    combined = pd.DataFrame(rows)

    if combined.empty:
        raise RuntimeError(
            "No combined strategy produced enough signals."
        )

    combined = add_baseline_comparison(combined)

    # --------------------------------------------------------
    # OOS ONLY FOR COMBINED CANDIDATES
    # --------------------------------------------------------

    oos_mask = make_oos_mask(features)
    oos_rows = []

    for _, row in combined.iterrows():

        if row["family"] == "COMBINATION":
            combo = tuple(
                str(row["strategy"]).split(" + ")
            )
            mode = row["mode"]

            s = evaluate_combo(
                features,
                combo,
                int(row["horizon"]),
                mode,
            )

            # Rebuild exact mask for OOS.
            masks = get_masks(features)

            if mode == "AND":
                m = masks[combo[0]].copy()
                for n in combo[1:]:
                    m &= masks[n]

            elif mode == "OR":
                m = masks[combo[0]].copy()
                for n in combo[1:]:
                    m |= masks[n]

            elif mode.startswith("CONSENSUS"):
                votes = sum(
                    masks[n].astype(int)
                    for n in combo
                )

                threshold = (
                    2 if mode == "CONSENSUS_2_OF_3"
                    else 3
                )

                m = votes >= threshold

            else:
                continue

        else:
            gap = int(row["max_gap_sessions"])
            m = sequence_cba(features, gap)

        s_oos = evaluate_mask(
            features,
            m,
            int(row["horizon"]),
            oos_mask=oos_mask,
        )

        if s_oos:
            oos_rows.append({
                "family": row["family"],
                "mode": row["mode"],
                "horizon": row["horizon"],
                "strategy": row["strategy"],
                "max_gap_sessions":
                    row.get("max_gap_sessions"),
                "oos_signals": s_oos["signals"],
                "oos_win_rate": s_oos["win_rate"],
                "oos_average_return":
                    s_oos["average_return"],
                "oos_median_return":
                    s_oos["median_return"],
                "oos_profit_factor":
                    s_oos["profit_factor"],
                "oos_robust_mean":
                    s_oos["robust_mean"],
                "oos_score":
                    s_oos["score"],
                "oos_beats_71_89":
                    s_oos["score"] > 71.89,
            })

    oos = pd.DataFrame(oos_rows)

    # --------------------------------------------------------
    # CROSS-HORIZON: ONLY COMBINED STRATEGIES
    # --------------------------------------------------------

    stability_rows = []

    group_cols = [
        "family",
        "mode",
        "strategy",
    ]

    if "max_gap_sessions" in combined.columns:
        group_cols.append("max_gap_sessions")

    for key, g in combined.groupby(
        group_cols,
        dropna=False
    ):
        if set(g["horizon"].astype(int)) != {10, 20}:
            continue

        r10 = g[g["horizon"] == 10].iloc[0]
        r20 = g[g["horizon"] == 20].iloc[0]

        score10 = float(r10["score"])
        score20 = float(r20["score"])

        stability_rows.append({
            **{
                col: value
                for col, value in zip(
                    group_cols, key if isinstance(key, tuple)
                    else (key,)
                )
            },
            "score_10d": score10,
            "score_20d": score20,
            "average_score":
                (score10 + score20) / 2,
            "score_gap":
                abs(score10 - score20),
            "beats_71_89_both":
                score10 > 71.89 and score20 > 71.89,
            "improvement_10d":
                score10 - 71.89,
            "improvement_20d":
                score20 - 71.89,
        })

    stability = pd.DataFrame(stability_rows)

    if not stability.empty:
        stability = stability.sort_values(
            [
                "beats_71_89_both",
                "average_score",
                "score_gap",
            ],
            ascending=[False, False, True],
        ).reset_index(drop=True)

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    combined.to_csv(
        RESULTS_DIR / "combo_only_ranked.csv",
        index=False,
    )

    oos.to_csv(
        RESULTS_DIR / "combo_only_oos.csv",
        index=False,
    )

    stability.to_csv(
        RESULTS_DIR / "combo_only_cross_horizon.csv",
        index=False,
    )

    (RESULTS_DIR / "baseline.json").write_text(
        json.dumps(
            {
                "standalone_tests_not_run": True,
                "baseline_scores_are_previous_results": BASELINE,
                "best_previous_score": 71.89,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = {
        "engine":
            "Nasdaq Combo-Only Engine V3",
        "standalone_retests":
            False,
        "baseline":
            BASELINE,
        "tested_only": [
            "A+B",
            "A+C",
            "B+C",
            "A+B+C",
            "OR(A,B,C)",
            "2-of-3(A,B,C)",
            "3-of-3(A,B,C)",
            "C -> B -> A",
        ],
        "horizons": HORIZONS,
        "entry":
            "signal at T close -> T+1 open",
        "exit":
            "T+horizon close",
        "oos_fraction":
            OOS_FRACTION,
        "candidate_count":
            int(len(combined)),
        "top_candidates":
            combined.head(20).to_dict("records"),
        "top_oos":
            oos.sort_values(
                "oos_score",
                ascending=False
            ).head(20).to_dict("records")
            if not oos.empty else [],
        "cross_horizon":
            stability.head(20).to_dict("records")
            if not stability.empty else [],
        "warning":
            "Scores are research ranking scores, not probabilities.",
        "failed_files":
            len(failed),
    }

    (RESULTS_DIR / "combo_only_summary.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    if failed:
        pd.DataFrame(failed).to_csv(
            RESULTS_DIR / "failed_files.csv",
            index=False,
        )

    # --------------------------------------------------------
    # CONSOLE
    # --------------------------------------------------------

    print()
    print("COMPLETE")
    print(f"Combined candidates: {len(combined):,}")

    print()
    print("TOP COMBINED RESULTS")
    print(
        combined[
            [
                "family",
                "mode",
                "horizon",
                "strategy",
                "signals",
                "win_rate",
                "average_return",
                "median_return",
                "profit_factor",
                "robust_mean",
                "score",
                "improvement_vs_best_baseline",
                "beats_best_baseline",
            ]
        ].head(20).to_string(index=False)
    )

    print()
    print("TOP OOS")
    if not oos.empty:
        print(
            oos.sort_values(
                "oos_score",
                ascending=False
            ).head(20).to_string(index=False)
        )
    else:
        print("No OOS candidate reached the minimum signal count.")

    print()
    print("CROSS-HORIZON WINNERS")
    if not stability.empty:
        print(
            stability.head(20).to_string(index=False)
        )
    else:
        print("No strategy has valid results in both 10D and 20D.")

    print()
    print(f"Results: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
