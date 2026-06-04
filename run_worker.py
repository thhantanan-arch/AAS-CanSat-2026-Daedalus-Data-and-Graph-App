from __future__ import annotations

import argparse
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from generate_all_cansat_graphs import generate_all, generate_altitude
from log_normalizer import normalize_log_file


STATE_MAP_NUMERIC = {
    "0": "LAUNCH_PAD",
    "1": "ASCENT",
    "2": "APOGEE",
    "3": "DESCENT",
    "4": "PAYLOAD_RELEASE",
    "5": "LANDED",
    "6": "PROBE_RELEASE",
}

STATE_KEYWORDS = [
    ("LAUNCH_PAD", ["LAUNCH_PAD", "LAUNCH PAD", "PAD", "READY", "IDLE"]),
    ("ASCENT", ["ASCENT", "ASCENDING", "LAUNCH", "BOOST", "ROCKET"]),
    ("APOGEE", ["APOGEE", "PEAK"]),
    ("DESCENT", ["DESCENT", "DESCENDING", "PARACHUTE", "FALLING"]),
    ("PROBE_RELEASE", ["PROBE_RELEASE", "PROBE RELEASE", "PROBE"]),
    ("PAYLOAD_RELEASE", ["PAYLOAD_RELEASE", "PAYLOAD RELEASE", "PAYLOAD"]),
    ("LANDED", ["LANDED", "LANDING", "GROUND"]),
]


COLUMN_ALIASES = {
    "PACKET_COUNT": ["PACKET_COUNT", "PACKET", "PACKETCOUNT", "PKT", "COUNT"],
    "STATE": ["STATE", "FLIGHT_STATE", "FLIGHT STATE", "MODE", "STATUS"],
    "ALTITUDE": ["ALTITUDE", "ALT", "BARO_ALTITUDE", "BARO ALTITUDE", "BMP_ALTITUDE", "ALTITUDE_M"],
    "VOLTAGE": ["VOLTAGE", "VOLT", "BATTERY_VOLTAGE", "BATTERY VOLTAGE", "V_BATT", "VBAT"],
    "TEMPERATURE": ["TEMPERATURE", "TEMP", "TEMP_C", "BMP_TEMP", "TEMPERATURE_C"],
    "GPS_LAT": ["GPS_LAT", "GPS LAT", "LAT", "LATITUDE"],
    "GPS_LON": ["GPS_LON", "GPS LON", "LON", "LONG", "LONGITUDE"],
    "GPS_ALT": ["GPS_ALT", "GPS ALT", "GPS_ALTITUDE", "GPS ALTITUDE"],
    "GYRO_R": ["GYRO_R", "GYRO R", "GYRO_ROLL", "ROLL_RATE", "GYRO_X"],
    "GYRO_P": ["GYRO_P", "GYRO P", "GYRO_PITCH", "PITCH_RATE", "GYRO_Y"],
    "GYRO_Y": ["GYRO_Y", "GYRO Y", "GYRO_YAW", "YAW_RATE", "GYRO_Z"],
    "ACCEL_R": ["ACCEL_R", "ACCEL R", "ACCEL_ROLL", "ACCEL_X", "ACC_X"],
    "ACCEL_P": ["ACCEL_P", "ACCEL P", "ACCEL_PITCH", "ACCEL_Y", "ACC_Y"],
    "ACCEL_Y": ["ACCEL_Y", "ACCEL Y", "ACCEL_YAW", "ACCEL_Z", "ACC_Z"],
    "TILT_ROLL_DERIVED": ["TILT_ROLL_DERIVED", "TILT ROLL", "TILT_ROLL", "ROLL"],
    "TILT_PITCH_DERIVED": ["TILT_PITCH_DERIVED", "TILT PITCH", "TILT_PITCH", "PITCH"],
    "SERVO_CURRENT_1": ["SERVO_CURRENT_1", "SERVO CURRENT 1", "SERVO1_CURRENT", "SERVO_CURRENT1"],
    "SERVO_CURRENT_2": ["SERVO_CURRENT_2", "SERVO CURRENT 2", "SERVO2_CURRENT", "SERVO_CURRENT2"],
    "SERVO_CURRENT_3": ["SERVO_CURRENT_3", "SERVO CURRENT 3", "SERVO3_CURRENT", "SERVO_CURRENT3"],
    "SERVO_TARGET_1": ["SERVO_TARGET_1", "SERVO TARGET 1", "SERVO1_TARGET", "SERVO_TARGET1"],
    "SERVO_TARGET_2": ["SERVO_TARGET_2", "SERVO TARGET 2", "SERVO2_TARGET", "SERVO_TARGET2"],
    "SERVO_TARGET_3": ["SERVO_TARGET_3", "SERVO TARGET 3", "SERVO3_TARGET", "SERVO_TARGET3"],
}


def clean_name(name: object) -> str:
    s = str(name).strip()
    s = re.sub(r"[\n\r\t]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def norm_key(name: object) -> str:
    s = clean_name(name).upper()
    s = re.sub(r"[^A-Z0-9]+", "_", s)
    return s.strip("_")


def read_any_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xlsm", ".xls"]:
        # pandas.read_excel supports Excel workbooks; openpyxl handles modern .xlsx/.xlsm.
        return pd.read_excel(path)
    if suffix in [".csv", ".txt"]:
        return pd.read_csv(path)
    raise ValueError(f"Unsupported log file type: {suffix}. Use .csv, .xlsx, .xlsm, or .xls")


def rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    original_cols = list(df.columns)
    normalized_lookup = {norm_key(c): c for c in original_cols}
    rename = {}

    for target, aliases in COLUMN_ALIASES.items():
        if target in df.columns:
            continue
        candidates = [target] + aliases
        for alias in candidates:
            k = norm_key(alias)
            if k in normalized_lookup:
                rename[normalized_lookup[k]] = target
                break

    df = df.rename(columns=rename)
    df.columns = [clean_name(c) for c in df.columns]
    return df


def normalize_state_value(v: object) -> str:
    if pd.isna(v):
        return ""
    s = str(v).strip().upper()
    s_clean = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")

    if s_clean in STATE_MAP_NUMERIC:
        return STATE_MAP_NUMERIC[s_clean]
    if s_clean in ["LAUNCHPAD"]:
        return "LAUNCH_PAD"
    if s_clean in ["PAYLOADRELEASE"]:
        return "PAYLOAD_RELEASE"
    if s_clean in ["PROBERELEASE"]:
        return "PROBE_RELEASE"
    if s_clean in {"LAUNCH_PAD", "ASCENT", "APOGEE", "DESCENT", "PROBE_RELEASE", "PAYLOAD_RELEASE", "LANDED"}:
        return s_clean

    s_space = s_clean.replace("_", " ")
    for state, words in STATE_KEYWORDS:
        for w in words:
            if w in s_space or w.replace(" ", "_") in s_clean:
                return state
    return s_clean


def infer_state_from_altitude(df: pd.DataFrame) -> pd.Series:
    """Infer a minimal STATE sequence from ALTITUDE without relying on label-based positions."""
    if "ALTITUDE" not in df.columns or len(df) == 0:
        return pd.Series(["ASCENT"] * len(df), index=df.index, dtype=object)

    alt = pd.to_numeric(df["ALTITUDE"], errors="coerce").interpolate(limit_direction="both").bfill().ffill()
    n = len(alt)
    if n == 0:
        return pd.Series([], dtype=object)

    arr = alt.to_numpy(dtype=float)
    finite = pd.Series(arr).replace([float("inf"), float("-inf")], pd.NA).dropna()
    if finite.empty:
        states = pd.Series(["ASCENT"] * n, index=df.index, dtype=object)
        return states

    max_pos = int(pd.Series(arr).idxmax())
    max_alt = float(pd.Series(arr).max())
    states = pd.Series(["DESCENT"] * n, index=df.index, dtype=object)

    head_n = max(3, min(20, n))
    baseline = float(pd.Series(arr[:head_n]).median())
    ascent_threshold = baseline + max(2.0, abs(max_alt - baseline) * 0.02)

    above_positions = [i for i, v in enumerate(arr) if pd.notna(v) and v > ascent_threshold]
    launch_pos = above_positions[0] if above_positions else 0
    launch_pos = max(0, min(launch_pos, n - 1))
    max_pos = max(launch_pos, min(max_pos, n - 1))

    states.iloc[:launch_pos] = "LAUNCH_PAD"
    states.iloc[launch_pos:max_pos] = "ASCENT"
    states.iloc[max_pos] = "APOGEE"

    payload_alt = max_alt * 0.80
    pr_pos = None
    for i in range(max_pos, n):
        if pd.notna(arr[i]) and arr[i] <= payload_alt:
            pr_pos = i
            break
    if pr_pos is not None and 0 <= pr_pos < n:
        states.iloc[pr_pos:] = "PAYLOAD_RELEASE"
        states.iloc[pr_pos] = "PAYLOAD_RELEASE"

    landed_pos = None
    landed_threshold = max(baseline + 3.0, 5.0)
    for i in range(max_pos, n):
        if pd.notna(arr[i]) and arr[i] <= landed_threshold:
            landed_pos = i
            break
    if landed_pos is not None and 0 <= landed_pos < n:
        states.iloc[landed_pos:] = "LANDED"

    # Guarantee at least one ASCENT point for the plotting engine.
    if not (states == "ASCENT").any():
        safe_pos = max(0, min(launch_pos, n - 1))
        states.iloc[safe_pos] = "ASCENT"

    return states


def ensure_packet_count(df: pd.DataFrame) -> pd.DataFrame:
    if "PACKET_COUNT" not in df.columns:
        # Prefer time-like columns if present, otherwise row index.
        df["PACKET_COUNT"] = range(len(df))
    df["PACKET_COUNT"] = pd.to_numeric(df["PACKET_COUNT"], errors="coerce")
    if df["PACKET_COUNT"].isna().all():
        df["PACKET_COUNT"] = range(len(df))
    df["PACKET_COUNT"] = df["PACKET_COUNT"].interpolate(limit_direction="both").bfill().ffill()
    return df


def normalize_input_file(path: Path, out_dir: Path) -> Path:
    raw = read_any_table(path)
    df = rename_columns(raw)

    # Drop fully empty rows.
    df = df.dropna(how="all").reset_index(drop=True)

    if "ALTITUDE" not in df.columns:
        raise ValueError(
            "ALTITUDE column not found. Excel/CSV loaded, but the graph engine needs altitude. "
            f"Detected columns: {list(raw.columns)[:30]}"
        )

    df = ensure_packet_count(df)

    if "STATE" in df.columns:
        df["STATE"] = df["STATE"].map(normalize_state_value)
        if not (df["STATE"] == "ASCENT").any():
            # Bad or unknown STATE labels: infer rather than fail.
            df["STATE"] = infer_state_from_altitude(df)
    else:
        df["STATE"] = infer_state_from_altitude(df)

    # Numeric conversions for known sensor columns. Use errors="coerce" for pandas 3 compatibility.
    for col in df.columns:
        if col != "STATE":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Fill optional columns so missing non-critical families do not break as early.
    optional_defaults = {
        "VOLTAGE": 0.0, "TEMPERATURE": 0.0,
        "GPS_LAT": 0.0, "GPS_LON": 0.0, "GPS_ALT": 0.0,
        "GYRO_R": 0.0, "GYRO_P": 0.0, "GYRO_Y": 0.0,
        "ACCEL_R": 0.0, "ACCEL_P": 0.0, "ACCEL_Y": 0.0,
        "TILT_ROLL_DERIVED": 0.0, "TILT_PITCH_DERIVED": 0.0,
        "SERVO_CURRENT_1": 0.0, "SERVO_CURRENT_2": 0.0, "SERVO_CURRENT_3": 0.0,
        "SERVO_TARGET_1": 0.0, "SERVO_TARGET_2": 0.0, "SERVO_TARGET_3": 0.0,
    }
    for col, val in optional_defaults.items():
        if col not in df.columns:
            df[col] = val

    if not (df["STATE"] == "ASCENT").any():
        raise ValueError(
            "No ASCENT state found even after normalization/inference. "
            "Check that ALTITUDE contains a real climb segment."
        )

    normalized_path = out_dir / "normalized_input_for_engine.csv"
    df.to_csv(normalized_path, index=False)
    report = {
        "source_file": str(path),
        "normalized_csv": str(normalized_path),
        "rows": len(df),
        "columns": list(df.columns),
        "state_counts": df["STATE"].value_counts(dropna=False).to_dict(),
    }
    (out_dir / "input_normalization_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return normalized_path



def _first_time_state(df: pd.DataFrame, state: str):
    rows = df[df["STATE"].astype(str).eq(state)]
    if rows.empty:
        return None
    return rows.iloc[0]


def _status_band(value, low, high):
    if value is None or pd.isna(value):
        return "UNKNOWN"
    return "PASS" if low <= float(value) <= high else "CHECK"


def _fmt(v, nd=2):
    if v is None or pd.isna(v):
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


def _linear_descent_fit(df: pd.DataFrame, t0: float, t1: float, trim_start_s: float = 0.0, trim_end_s: float = 0.0) -> dict:
    """Fit altitude = m*time + b for a descent segment and report descent_rate = -m."""
    if t0 is None or t1 is None or pd.isna(t0) or pd.isna(t1) or t1 <= t0:
        return {
            "status": "NOT_AVAILABLE",
            "reason": "invalid time window",
            "descent_rate_mps": None,
            "slope_m_per_s": None,
            "intercept_m": None,
            "r2": None,
            "sample_count": 0,
            "window_start_s": None,
            "window_end_s": None,
        }

    start_t = float(t0) + float(trim_start_s)
    end_t = float(t1) - float(trim_end_s)
    if end_t <= start_t:
        start_t, end_t = float(t0), float(t1)

    seg = df[(df["T_REPORT"] >= start_t) & (df["T_REPORT"] <= end_t)].copy()
    seg["ALTITUDE"] = pd.to_numeric(seg["ALTITUDE"], errors="coerce")
    seg = seg.dropna(subset=["T_REPORT", "ALTITUDE"])
    if len(seg) < 3:
        return {
            "status": "NOT_AVAILABLE",
            "reason": "fewer than 3 samples",
            "descent_rate_mps": None,
            "slope_m_per_s": None,
            "intercept_m": None,
            "r2": None,
            "sample_count": int(len(seg)),
            "window_start_s": start_t,
            "window_end_s": end_t,
        }

    x = seg["T_REPORT"].to_numpy(dtype=float)
    y = seg["ALTITUDE"].to_numpy(dtype=float)
    x0 = x - x.mean()  # improves numerical conditioning
    slope, intercept_centered = np.polyfit(x0, y, 1)
    intercept = intercept_centered - slope * x.mean()
    y_pred = slope * x + intercept
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None

    return {
        "status": "OK",
        "descent_rate_mps": float(-slope),
        "slope_m_per_s": float(slope),
        "intercept_m": float(intercept),
        "r2": float(r2) if r2 is not None else None,
        "sample_count": int(len(seg)),
        "window_start_s": float(start_t),
        "window_end_s": float(end_t),
    }


def _safe_float(x):
    try:
        if pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def _advanced_column_statistics(df: pd.DataFrame, time_col: str = "T_REPORT") -> dict:
    """Compute descriptive and trend statistics for every numeric telemetry column."""
    stats = {}
    skip = {"PACKET_COUNT", "SOURCE_ROW"}
    for col in df.columns:
        if col in skip:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        non_nan = int(s.notna().sum())
        if non_nan == 0:
            continue
        clean = s.dropna()
        row = {
            "count": non_nan,
            "missing": int(len(df) - non_nan),
            "mean": _safe_float(clean.mean()),
            "median": _safe_float(clean.median()),
            "std": _safe_float(clean.std(ddof=1)) if non_nan > 1 else None,
            "min": _safe_float(clean.min()),
            "q1": _safe_float(clean.quantile(0.25)),
            "q3": _safe_float(clean.quantile(0.75)),
            "max": _safe_float(clean.max()),
            "range": _safe_float(clean.max() - clean.min()),
            "iqr": _safe_float(clean.quantile(0.75) - clean.quantile(0.25)),
        }
        if time_col in df.columns and col != time_col:
            t = pd.to_numeric(df[time_col], errors="coerce")
            fit_df = pd.DataFrame({"t": t, "y": s}).dropna()
            if len(fit_df) >= 3 and float(fit_df["t"].max() - fit_df["t"].min()) != 0 and float(fit_df["y"].max() - fit_df["y"].min()) != 0:
                x = fit_df["t"].to_numpy(float)
                y = fit_df["y"].to_numpy(float)
                slope, intercept_centered = np.polyfit(x - x.mean(), y, 1)
                intercept = intercept_centered - slope * x.mean()
                pred = slope * x + intercept
                ss_res = float(np.sum((y - pred) ** 2))
                ss_tot = float(np.sum((y - np.mean(y)) ** 2))
                row.update({
                    "trend_slope_per_s": _safe_float(slope),
                    "trend_intercept": _safe_float(intercept),
                    "trend_r2": _safe_float(1.0 - ss_res / ss_tot if ss_tot > 0 else None),
                })
            else:
                row.update({"trend_slope_per_s": None, "trend_intercept": None, "trend_r2": None})
        stats[col] = row
    return stats


REPORT_DEFINITIONS = {
    "Apogee": "Highest altitude point after launch; used as the peak mission altitude.",
    "Payload release percent of peak": "Payload release altitude divided by apogee altitude, multiplied by 100.",
    "Endpoint average descent rate": "Altitude drop divided by elapsed time between two mission events.",
    "Linear regression descent rate": "Negative slope of the best-fit altitude-time line. The app uses least-squares fitting.",
    "Stable-window regression": "Regression after trimming event-boundary transition samples so the fit focuses on the stable descent segment.",
    "R² / coefficient of determination": "Goodness-of-fit score for a regression. 1.0 is best; 0.0 is similar to predicting the mean; it can be negative.",
    "Sample count / n": "Number of usable non-missing rows included in a calculation.",
    "Mean": "Arithmetic average of numeric samples.",
    "Median": "Middle value after sorting samples; less sensitive to extreme outliers than mean.",
    "Standard deviation": "Typical spread of values around the mean.",
    "IQR": "Interquartile range: Q3 minus Q1. It measures the spread of the middle 50% of samples.",
    "Trend slope": "Linear-regression slope of a data column versus mission time. Units are the column unit per second.",
    "Missing": "Rows where a numeric value could not be read for that column.",
}

def build_flight_report(normalized_csv: Path, out_dir: Path, source_path: Path, mode: str) -> dict:
    """Build a PFR-ready report with key mission events and regression-based rule checks."""
    df = pd.read_csv(normalized_csv)
    df["PACKET_COUNT"] = pd.to_numeric(df["PACKET_COUNT"], errors="coerce")
    df["ALTITUDE"] = pd.to_numeric(df["ALTITUDE"], errors="coerce")
    df = df.dropna(subset=["PACKET_COUNT", "ALTITUDE"]).sort_values("PACKET_COUNT").reset_index(drop=True)
    if df.empty:
        raise ValueError("Cannot build flight report: no packet/altitude rows after normalization.")

    fs_hz = 5.0
    if "T_FROM_LAUNCH_S" in df.columns:
        df["T_REPORT"] = pd.to_numeric(df["T_FROM_LAUNCH_S"], errors="coerce")
        ascent = df[df["STATE"].astype(str).eq("ASCENT")]
        launch_packet = float(ascent["PACKET_COUNT"].iloc[0]) if not ascent.empty else float(df["PACKET_COUNT"].iloc[0])
    else:
        ascent = df[df["STATE"].astype(str).eq("ASCENT")]
        launch_packet = float(ascent["PACKET_COUNT"].iloc[0]) if not ascent.empty else float(df["PACKET_COUNT"].iloc[0])
        df["T_REPORT"] = (df["PACKET_COUNT"] - launch_packet) / fs_hz

    flight = df[df["T_REPORT"] >= 0].copy()
    if flight.empty:
        flight = df.copy()

    apogee_idx = int(flight["ALTITUDE"].idxmax())
    apogee_row = df.loc[apogee_idx]
    apogee_alt = float(apogee_row["ALTITUDE"])
    apogee_t = float(apogee_row["T_REPORT"])

    # Payload release: prefer state marker; fallback to first descent crossing of 80% peak altitude.
    pr_row = _first_time_state(df, "PAYLOAD_RELEASE")
    release_source = "STATE=PAYLOAD_RELEASE"
    target_release_alt = apogee_alt * 0.80
    if pr_row is None:
        after_apogee = df[df["T_REPORT"] >= apogee_t].copy()
        candidates = after_apogee[after_apogee["ALTITUDE"] <= target_release_alt]
        if not candidates.empty:
            pr_row = candidates.iloc[0]
            release_source = "80% altitude crossing fallback"
    if pr_row is not None:
        pr_t = float(pr_row["T_REPORT"])
        pr_alt = float(pr_row["ALTITUDE"])
    else:
        pr_t = None
        pr_alt = None

    landed_row = _first_time_state(df, "LANDED")
    if landed_row is not None:
        land_t = float(landed_row["T_REPORT"])
        land_alt = float(landed_row["ALTITUDE"])
        land_source = "STATE=LANDED"
    else:
        last = df.iloc[-1]
        land_t = float(last["T_REPORT"])
        land_alt = float(last["ALTITUDE"])
        land_source = "last packet fallback"

    # Instrument release at 2 m: first crossing after payload release, otherwise unknown.
    inst_t = None
    inst_alt = None
    if pr_t is not None:
        post_pr = df[df["T_REPORT"] >= pr_t]
        inst_candidates = post_pr[post_pr["ALTITUDE"] <= 2.0]
        if not inst_candidates.empty:
            inst = inst_candidates.iloc[0]
            inst_t = float(inst["T_REPORT"])
            inst_alt = float(inst["ALTITUDE"])

    # Endpoint averages retained as transparent secondary metrics.
    stage1_endpoint_rate = None
    if pr_t is not None and pr_t > apogee_t:
        stage1_endpoint_rate = (apogee_alt - pr_alt) / (pr_t - apogee_t)

    stage2_endpoint_rate = None
    if pr_t is not None and land_t > pr_t:
        stage2_endpoint_rate = (pr_alt - land_alt) / (land_t - pr_t)

    # Regression rates are the primary report values.
    # Full-window: apogee -> payload release, payload release -> landing.
    stage1_full_reg = _linear_descent_fit(df, apogee_t, pr_t, 0.0, 0.0)
    stage2_full_reg = _linear_descent_fit(df, pr_t, land_t, 0.0, 0.0)

    # Stable-window: removes transition noise near event boundaries.
    # Stage 1 uses apogee+1.5s to payload_release-0.5s.
    # Stage 2 uses payload_release+0.5s to landing-0.5s.
    stage1_stable_reg = _linear_descent_fit(df, apogee_t, pr_t, 1.5, 0.5)
    stage2_stable_reg = _linear_descent_fit(df, pr_t, land_t, 0.5, 0.5)

    # Official report uses stable regression when available; otherwise falls back to full regression then endpoint.
    def _primary_rate(stable, full, endpoint):
        if stable.get("status") == "OK":
            return stable.get("descent_rate_mps"), "stable-window linear regression"
        if full.get("status") == "OK":
            return full.get("descent_rate_mps"), "full-window linear regression"
        return endpoint, "endpoint average fallback"

    stage1_rate, stage1_method = _primary_rate(stage1_stable_reg, stage1_full_reg, stage1_endpoint_rate)
    stage2_rate, stage2_method = _primary_rate(stage2_stable_reg, stage2_full_reg, stage2_endpoint_rate)

    release_percent = (pr_alt / apogee_alt * 100.0) if pr_alt is not None and apogee_alt else None
    release_error_pp = (release_percent - 80.0) if release_percent is not None else None

    checks = {
        "C4_stage1_descent_rate": {
            "rule": "15 m/s ± 3 m/s after deployment under parachute",
            "measured_mps": stage1_rate,
            "method": stage1_method,
            "target": "12–18 m/s",
            "status": _status_band(stage1_rate, 12.0, 18.0),
        },
        "C5_C6_payload_release_altitude": {
            "rule": "payload release / paraglider deployment at 80% peak altitude",
            "measured_percent_of_peak": release_percent,
            "target": "80% peak altitude",
            "error_percentage_points": release_error_pp,
            "status": "PASS" if release_error_pp is not None and abs(release_error_pp) <= 5.0 else "CHECK",
            "note": "The app uses ±5 percentage points as an engineering check band; the mission guide states the 80% target.",
        },
        "C7_stage2_paraglider_descent_rate": {
            "rule": "5 m/s average ± 3 m/s with paraglider descent control",
            "measured_mps": stage2_rate,
            "method": stage2_method,
            "target": "2–8 m/s",
            "status": _status_band(stage2_rate, 2.0, 8.0),
        },
        "C12_instrument_release": {
            "rule": "instrument release at 2 m above ground",
            "measured_altitude_m": inst_alt,
            "measured_time_s": inst_t,
            "target": "2 m",
            "status": "FOUND" if inst_t is not None else "NOT FOUND",
        },
    }

    duration_s = float(df["T_REPORT"].max() - df["T_REPORT"].min()) if len(df) > 1 else 0.0
    packet_count = int(len(df))
    avg_sample_rate = packet_count / duration_s if duration_s > 0 else None

    advanced_stats = _advanced_column_statistics(df, "T_REPORT")

    report = {
        "source_log": str(source_path),
        "normalized_csv": str(normalized_csv),
        "mode": mode,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sampling_assumption_hz": fs_hz,
        "descent_rate_method_note": "Primary descent rates use least-squares linear regression on stable windows. Endpoint averages are retained as secondary reference values.",
        "summary": {
            "apogee_altitude_m": apogee_alt,
            "apogee_time_s": apogee_t,
            "payload_release_altitude_m": pr_alt,
            "payload_release_time_s": pr_t,
            "payload_release_source": release_source if pr_row is not None else "not found",
            "payload_release_percent_of_peak": release_percent,
            "payload_release_error_percentage_points": release_error_pp,
            "stage1_average_descent_rate_mps": stage1_rate,
            "stage1_rate_method": stage1_method,
            "stage1_endpoint_average_descent_rate_mps": stage1_endpoint_rate,
            "stage1_full_regression_descent_rate_mps": stage1_full_reg.get("descent_rate_mps"),
            "stage1_full_regression_r2": stage1_full_reg.get("r2"),
            "stage1_full_regression_samples": stage1_full_reg.get("sample_count"),
            "stage1_stable_regression_descent_rate_mps": stage1_stable_reg.get("descent_rate_mps"),
            "stage1_stable_regression_r2": stage1_stable_reg.get("r2"),
            "stage1_stable_regression_samples": stage1_stable_reg.get("sample_count"),
            "stage1_stable_window_start_s": stage1_stable_reg.get("window_start_s"),
            "stage1_stable_window_end_s": stage1_stable_reg.get("window_end_s"),
            "stage2_average_descent_rate_mps": stage2_rate,
            "stage2_rate_method": stage2_method,
            "stage2_endpoint_average_descent_rate_mps": stage2_endpoint_rate,
            "stage2_full_regression_descent_rate_mps": stage2_full_reg.get("descent_rate_mps"),
            "stage2_full_regression_r2": stage2_full_reg.get("r2"),
            "stage2_full_regression_samples": stage2_full_reg.get("sample_count"),
            "stage2_stable_regression_descent_rate_mps": stage2_stable_reg.get("descent_rate_mps"),
            "stage2_stable_regression_r2": stage2_stable_reg.get("r2"),
            "stage2_stable_regression_samples": stage2_stable_reg.get("sample_count"),
            "stage2_stable_window_start_s": stage2_stable_reg.get("window_start_s"),
            "stage2_stable_window_end_s": stage2_stable_reg.get("window_end_s"),
            "instrument_release_time_s": inst_t,
            "instrument_release_altitude_m": inst_alt,
            "landing_time_s": land_t,
            "landing_altitude_m": land_alt,
            "landing_source": land_source,
            "packet_count": packet_count,
            "duration_s": duration_s,
            "average_sample_rate_hz": avg_sample_rate,
        },
        "regression_details": {
            "stage1_full_window": stage1_full_reg,
            "stage1_stable_window": stage1_stable_reg,
            "stage2_full_window": stage2_full_reg,
            "stage2_stable_window": stage2_stable_reg,
        },
        "checks": checks,
        "advanced_statistics": advanced_stats,
        "definitions": REPORT_DEFINITIONS,
    }

    diagnostics_dir = out_dir / "00_diagnostics"
    diagnostics_dir.mkdir(exist_ok=True)

    (diagnostics_dir / "flight_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Advanced statistics exports
    adv_rows = []
    for col, st in advanced_stats.items():
        row = {"Column": col}
        row.update(st)
        adv_rows.append(row)
    pd.DataFrame(adv_rows).to_csv(diagnostics_dir / "advanced_data_statistics.csv", index=False)

    def_rows = [{"Term": k, "Definition": v} for k, v in REPORT_DEFINITIONS.items()]
    pd.DataFrame(def_rows).to_csv(diagnostics_dir / "report_definitions.csv", index=False)

    reg_rows = []
    for name, reg in {
        "Stage 1 full": stage1_full_reg,
        "Stage 1 stable": stage1_stable_reg,
        "Stage 2 full": stage2_full_reg,
        "Stage 2 stable": stage2_stable_reg,
    }.items():
        rr = {"Window": name}
        rr.update(reg)
        reg_rows.append(rr)
    pd.DataFrame(reg_rows).to_csv(diagnostics_dir / "regression_details.csv", index=False)

    rows = [
        ("Apogee altitude", _fmt(apogee_alt), "m", ""),
        ("Apogee time", _fmt(apogee_t), "s", ""),
        ("Payload release altitude", _fmt(pr_alt), "m", release_source if pr_row is not None else "not found"),
        ("Payload release time", _fmt(pr_t), "s", ""),
        ("Payload release % of peak", _fmt(release_percent), "%", f"error {_fmt(release_error_pp)} percentage points"),
        ("Stage 1 primary descent rate", _fmt(stage1_rate), "m/s", stage1_method + " / " + checks["C4_stage1_descent_rate"]["status"]),
        ("Stage 1 stable regression", _fmt(stage1_stable_reg.get("descent_rate_mps")), "m/s", f"R²={_fmt(stage1_stable_reg.get('r2'),3)}, n={stage1_stable_reg.get('sample_count')}"),
        ("Stage 1 full regression", _fmt(stage1_full_reg.get("descent_rate_mps")), "m/s", f"R²={_fmt(stage1_full_reg.get('r2'),3)}, n={stage1_full_reg.get('sample_count')}"),
        ("Stage 1 endpoint average", _fmt(stage1_endpoint_rate), "m/s", "secondary reference"),
        ("Stage 2 primary descent rate", _fmt(stage2_rate), "m/s", stage2_method + " / " + checks["C7_stage2_paraglider_descent_rate"]["status"]),
        ("Stage 2 stable regression", _fmt(stage2_stable_reg.get("descent_rate_mps")), "m/s", f"R²={_fmt(stage2_stable_reg.get('r2'),3)}, n={stage2_stable_reg.get('sample_count')}"),
        ("Stage 2 full regression", _fmt(stage2_full_reg.get("descent_rate_mps")), "m/s", f"R²={_fmt(stage2_full_reg.get('r2'),3)}, n={stage2_full_reg.get('sample_count')}"),
        ("Stage 2 endpoint average", _fmt(stage2_endpoint_rate), "m/s", "secondary reference"),
        ("Instrument release altitude", _fmt(inst_alt), "m", checks["C12_instrument_release"]["status"]),
        ("Landing time", _fmt(land_t), "s", land_source),
        ("Average sample rate", _fmt(avg_sample_rate), "Hz", ""),
    ]
    pd.DataFrame(rows, columns=["Metric", "Value", "Unit", "Note"]).to_csv(diagnostics_dir / "flight_report_summary.csv", index=False)

    md = []
    md.append("# Flight Rule Report")
    md.append("")
    md.append(f"Source log: `{source_path}`")
    md.append("")
    md.append("## Method")
    md.append("")
    md.append("Primary descent rates use least-squares linear regression on stable altitude-time windows:")
    md.append("")
    md.append("- Stage 1 stable window: apogee + 1.5 s to payload release - 0.5 s")
    md.append("- Stage 2 stable window: payload release + 0.5 s to landing - 0.5 s")
    md.append("- Full-window regression and endpoint averages are also reported as references.")
    md.append("")
    md.append("## Mission Summary")
    md.append("")
    md.append("| Metric | Value | Unit | Note |")
    md.append("|---|---:|---|---|")
    for metric, value, unit, note in rows:
        md.append(f"| {metric} | {value} | {unit} | {note} |")
    md.append("")
    md.append("## Rule Checks")
    md.append("")
    md.append("| Requirement | Rule | Measured | Target | Status |")
    md.append("|---|---|---:|---|---|")
    md.append(f"| C4 | {checks['C4_stage1_descent_rate']['rule']} | {_fmt(stage1_rate)} m/s | 12–18 m/s | {checks['C4_stage1_descent_rate']['status']} |")
    md.append(f"| C5/C6 | {checks['C5_C6_payload_release_altitude']['rule']} | {_fmt(release_percent)}% | 80% | {checks['C5_C6_payload_release_altitude']['status']} |")
    md.append(f"| C7 | {checks['C7_stage2_paraglider_descent_rate']['rule']} | {_fmt(stage2_rate)} m/s | 2–8 m/s | {checks['C7_stage2_paraglider_descent_rate']['status']} |")
    md.append(f"| C12 | {checks['C12_instrument_release']['rule']} | {_fmt(inst_alt)} m | 2 m | {checks['C12_instrument_release']['status']} |")
    md.append("")
    md.append("> Note: release altitude target is stated as 80% peak altitude. The app flags ±5 percentage points as an engineering check band, while still reporting the exact value.")
    md.append("")
    md.append("## Regression Details")
    md.append("")
    md.append("| Window | Rate (m/s) | R² | Samples | Start (s) | End (s) |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for name, reg in {
        "Stage 1 full": stage1_full_reg,
        "Stage 1 stable": stage1_stable_reg,
        "Stage 2 full": stage2_full_reg,
        "Stage 2 stable": stage2_stable_reg,
    }.items():
        md.append(f"| {name} | {_fmt(reg.get('descent_rate_mps'))} | {_fmt(reg.get('r2'),3)} | {reg.get('sample_count')} | {_fmt(reg.get('window_start_s'))} | {_fmt(reg.get('window_end_s'))} |")
    md.append("")
    md.append("## Advanced Data Statistics")
    md.append("")
    md.append("| Column | Count | Mean | Median | Std | Min | Max | IQR | Trend slope/s | Trend R² |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for col, st in advanced_stats.items():
        md.append(f"| {col} | {st.get('count')} | {_fmt(st.get('mean'))} | {_fmt(st.get('median'))} | {_fmt(st.get('std'))} | {_fmt(st.get('min'))} | {_fmt(st.get('max'))} | {_fmt(st.get('iqr'))} | {_fmt(st.get('trend_slope_per_s'))} | {_fmt(st.get('trend_r2'),3)} |")
    md.append("")
    md.append("## Definitions")
    md.append("")
    for term, definition in REPORT_DEFINITIONS.items():
        md.append(f"- **{term}:** {definition}")
    (diagnostics_dir / "flight_report.md").write_text("\n".join(md), encoding="utf-8")

    (out_dir / "flight_report.md").write_text("\n".join(md), encoding="utf-8")
    return report


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="CanSat Graph Studio worker")
    parser.add_argument("--csv", required=True, help="Input log: CSV or Excel")
    parser.add_argument("--out", required=True, help="Output folder")
    parser.add_argument("--mode", choices=["all", "quick"], default="all", help="Generation mode")
    parser.add_argument("--speed", choices=["fast", "quality"], default="fast", help="Web export speed profile")
    parser.add_argument("--families", default="", help="Comma-separated graph family keys to generate. Empty means preset/default.")
    args = parser.parse_args()

    source_path = Path(args.csv).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir = out_dir / "00_diagnostics"
    diagnostics_dir.mkdir(exist_ok=True)

    started = datetime.now().isoformat(timespec="seconds")
    print(f"[worker] Started: {started}", flush=True)
    print(f"[worker] Source log: {source_path}", flush=True)
    print(f"[worker] Output: {out_dir}", flush=True)
    print(f"[worker] Mode: {args.mode}", flush=True)
    print(f"[worker] Speed profile: {args.speed}", flush=True)
    if args.speed == "fast":
        os.environ["CFDS_SKIP_SVG"] = "1"
        os.environ["CFDS_PNG_DPI"] = "160"
    else:
        os.environ["CFDS_SKIP_SVG"] = "0"
        os.environ["CFDS_PNG_DPI"] = "300"

    if not source_path.exists():
        raise FileNotFoundError(f"Log file not found: {source_path}")

    try:
        print("[worker] Normalizing every input into canonical log format... (v0.5.1)", flush=True)
        normalized_csv = normalize_log_file(source_path, out_dir)
        print(f"[worker] Normalized CSV: {normalized_csv}", flush=True)
        print("[worker] Building flight rule report...", flush=True)
        flight_report = build_flight_report(normalized_csv, out_dir, source_path, args.mode)
        print(f"[worker] Report: 00_diagnostics/flight_report.md", flush=True)
        try:
            import shutil as _shutil
            rep = out_dir / "input_normalization_report.json"
            if rep.exists():
                _shutil.copy2(rep, diagnostics_dir / "input_normalization_report.json")
            if normalized_csv.exists():
                _shutil.copy2(normalized_csv, diagnostics_dir / "normalized_input_for_engine.csv")
        except Exception:
            pass
        try:
            _ndf = pd.read_csv(normalized_csv)
            print(f"[worker] Rows after normalization: {len(_ndf)}", flush=True)
            print(f"[worker] State counts: {_ndf['STATE'].value_counts(dropna=False).to_dict()}", flush=True)
        except Exception:
            pass

        selected_families = [x.strip() for x in str(args.families).split(",") if x.strip()]

        if args.mode == "quick" and not selected_families:
            print("[worker] Generating quick altitude preview...", flush=True)
            df = pd.read_csv(normalized_csv)
            generated = generate_altitude(df, out_dir)
        else:
            if selected_families:
                print(f"[worker] Updating selected graph families: {selected_families}", flush=True)
                generated = generate_all(normalized_csv, out_dir, selected_families=selected_families)
            else:
                print("[worker] Updating all graph families...", flush=True)
                generated = generate_all(normalized_csv, out_dir)

        generated_names = [Path(p).name for p in generated]
        manifest = {
            "status": "success",
            "mode": args.mode,
            "selected_families": selected_families if "selected_families" in locals() else [],
            "started": started,
            "finished": datetime.now().isoformat(timespec="seconds"),
            "source_log": str(source_path),
            "normalized_csv": str(normalized_csv),
            "output_dir": str(out_dir),
            "generated_count": len(generated_names),
            "generated_files": generated_names,
            "flight_report": "00_diagnostics/flight_report.md",
            "flight_report_json": "00_diagnostics/flight_report.json",
        }
        write_json(out_dir / "studio_manifest.json", manifest)
        write_json(diagnostics_dir / "studio_manifest.json", manifest)
        print(f"[worker] Done. Generated {len(generated_names)} files.", flush=True)
        return 0

    except Exception as exc:
        error_text = traceback.format_exc()
        (out_dir / "error_log.txt").write_text(error_text, encoding="utf-8")
        try:
            (out_dir / "00_diagnostics").mkdir(exist_ok=True)
            (out_dir / "00_diagnostics" / "error_log.txt").write_text(error_text, encoding="utf-8")
        except Exception:
            pass
        write_json(out_dir / "studio_manifest.json", {
            "status": "error",
            "mode": args.mode,
            "selected_families": selected_families if "selected_families" in locals() else [],
            "started": started,
            "failed": datetime.now().isoformat(timespec="seconds"),
            "source_log": str(source_path),
            "output_dir": str(out_dir),
            "error": str(exc),
            "error_log": "error_log.txt",
        })
        print("[worker] ERROR:", exc, flush=True)
        print("[worker] See error_log.txt in the output folder.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
