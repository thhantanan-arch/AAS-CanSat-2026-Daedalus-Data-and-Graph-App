
from pathlib import Path
import argparse
import os
import json
import zipfile
import shutil
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator, AutoMinorLocator, MaxNLocator, LogLocator, NullFormatter
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from scipy.signal import savgol_filter
from scipy.interpolate import PchipInterpolator

# ============================================================
# CanSat Frozen Graph Generator
# Frozen families:
# 1. altitude_smooth_p15q_v5_packet_time_3s_before_launch
# 2. velocity_family_candidate_v7_no_internal_text
# 3. voltage_temperature_scalar_candidate_v2
# 4. gps_family_candidate_v3_reference_style
# 5. multi_axis_family_candidate_v2
# 6. conops_altitude_actual_vs_planned_v6_pale_blue_frozen
# ============================================================

FS = 5.0
PRE_LAUNCH = 3.0
POST_LANDED = 5.0

STATE_COLORS = {
    "LAUNCH_PAD": "#D9D9D9",
    "ASCENT": "#E15759",
    "APOGEE": "#F1CE63",
    "DESCENT": "#B07AA1",
    "PROBE_RELEASE": "#FF9D3A",
    "PAYLOAD_RELEASE": "#59A14F",
    "LANDED": "#BAB0AC",
}
GRID = "#9AA3AF"
TEXT = "#1F2937"
ALT_COLOR = "#0B6FA4"
DARK = "#070B17"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "figure.dpi": 160,
    "savefig.dpi": 300,
    "axes.edgecolor": "#2B3038",
    "axes.linewidth": 0.8,
    "xtick.color": TEXT,
    "ytick.color": TEXT,
})

def odd_window(max_w, n):
    w = min(int(max_w), n if n % 2 else n - 1)
    if w % 2 == 0:
        w -= 1
    return max(w, 3)

def smooth_series(y, max_window=31, poly=3):
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    if ok.sum() < 7:
        return y
    idx = np.arange(len(y))
    yi = y.copy()
    yi[~ok] = np.interp(idx[~ok], idx[ok], y[ok])
    w = odd_window(max_window, len(yi))
    return savgol_filter(yi, w, min(poly, w - 1), mode="interp") if w >= 7 else yi

def mode_or_first(s):
    mode = s.mode()
    return mode.iloc[0] if not mode.empty else s.iloc[0]

def _shorten_path_if_needed(path):
    """Shorten file names on Windows-like long paths before saving."""
    path = Path(path)
    s = str(path)
    if len(s) <= 235:
        return path
    import hashlib
    h = hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:8]
    stem = path.stem
    short_stem = stem[:42] + "_" + h
    return path.with_name(short_stem + path.suffix)

def savefig(fig, path, svg=True):
    path = _shorten_path_if_needed(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    # Web speed controls:
    # CFDS_PNG_DPI=160 makes phone previews/export much faster than 300 dpi.
    # CFDS_SKIP_SVG=1 skips duplicate SVG saves; this nearly halves full-export time.
    try:
        png_dpi = int(os.environ.get("CFDS_PNG_DPI", "300"))
    except ValueError:
        png_dpi = 300
    skip_svg = os.environ.get("CFDS_SKIP_SVG", "0").strip().lower() in {"1", "true", "yes", "on"}
    fig.savefig(path, bbox_inches="tight", pad_inches=0.10, dpi=png_dpi, facecolor=fig.get_facecolor())
    if svg and not skip_svg:
        fig.savefig(path.with_suffix(".svg"), bbox_inches="tight", pad_inches=0.10, facecolor=fig.get_facecolor())
    plt.close(fig)

def add_state_background(ax, segments, alpha=0.08, strip_alpha=0.92):
    for state, start, end in segments:
        ax.axvspan(start, end, ymin=0.0, ymax=0.948,
                   color=STATE_COLORS.get(state, "#EEEEEE"), alpha=alpha, lw=0, zorder=0)
    for state, start, end in segments:
        ax.axvspan(start, end, ymin=0.972, ymax=0.990,
                   color=STATE_COLORS.get(state, "#EEEEEE"), alpha=strip_alpha, lw=0, zorder=4)

def make_segments(g, time_col="T"):
    segs = []
    states = g["STATE"].astype(str).tolist()
    times = g[time_col].tolist()
    if not states:
        return segs
    start = 0
    for i in range(1, len(g)):
        if states[i] != states[start]:
            segs.append((states[start], float(times[start]), float(times[i])))
            start = i
    segs.append((states[start], float(times[start]), float(times[-1])))
    return segs

def basic_time_style(ax, title, ylabel, xlim=None, ylim=None):
    ax.set_facecolor("white")
    ax.set_title(title, fontsize=19, fontweight="bold", pad=14)
    ax.set_xlabel("Mission Time Relative to Launch (s)", fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)
    ax.xaxis.set_major_locator(MultipleLocator(20))
    ax.xaxis.set_minor_locator(MultipleLocator(5))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=7))
    ax.yaxis.set_minor_locator(AutoMinorLocator(4))
    ax.grid(True, which="major", color=GRID, alpha=0.22, linewidth=0.65)
    ax.grid(True, which="minor", color=GRID, alpha=0.09, linewidth=0.35)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", labelsize=10.5, colors=TEXT)

def event_context_from_time_aligned(data, time_col="T", alt_col="ALTITUDE", state_col="STATE"):
    """Shared graph event detector. Does not trust early LANDED state for crop."""
    d = data.dropna(subset=[time_col, alt_col]).sort_values(time_col).copy()
    if d.empty:
        return {"apogee_t": 0.0, "apogee_alt": 0.0, "payload_state_t": np.nan,
                "payload_80_t": np.nan, "release_t": np.nan, "landing_physical_t": np.nan,
                "landing_state_t": np.nan, "plot_end_t": 0.0}

    flight = d[d[time_col] >= 0].copy()
    if flight.empty:
        flight = d.copy()
    ap_idx = flight[alt_col].idxmax()
    ap = d.loc[ap_idx]
    ap_t = float(ap[time_col])
    ap_alt = float(ap[alt_col])
    after_ap = d[d[time_col] > ap_t]

    pr_state = after_ap[after_ap[state_col].astype(str).eq("PAYLOAD_RELEASE")] if state_col in d.columns else pd.DataFrame()
    payload_state_t = float(pr_state[time_col].iloc[0]) if not pr_state.empty else np.nan

    target80 = ap_alt * 0.8
    pr80 = after_ap[after_ap[alt_col] <= target80]
    payload_80_t = float(pr80[time_col].iloc[0]) if not pr80.empty else np.nan

    if np.isfinite(payload_state_t):
        release_t = payload_state_t
    elif np.isfinite(payload_80_t):
        release_t = payload_80_t
    else:
        release_t = ap_t

    after_release = d[d[time_col] >= release_t]
    physical = after_release[after_release[alt_col] <= 2.0]
    landing_physical_t = float(physical[time_col].iloc[0]) if not physical.empty else np.nan

    landed_state = after_ap[after_ap[state_col].astype(str).eq("LANDED")] if state_col in d.columns else pd.DataFrame()
    landing_state_t = float(landed_state[time_col].iloc[0]) if not landed_state.empty else np.nan

    plot_end_t = (landing_physical_t + POST_LANDED) if np.isfinite(landing_physical_t) else float(d[time_col].max())

    return {"apogee_t": ap_t, "apogee_alt": ap_alt, "target80_alt": target80,
            "payload_state_t": payload_state_t, "payload_80_t": payload_80_t,
            "release_t": release_t, "landing_physical_t": landing_physical_t,
            "landing_state_t": landing_state_t, "plot_end_t": plot_end_t}


def choose_timebase_and_launch(data, packet_col="PACKET_COUNT", state_col="STATE", alt_col="ALTITUDE"):
    """Use normalized T_FROM_LAUNCH_S when available; otherwise use altitude-trigger launch."""
    if "T_FROM_LAUNCH_S" in data.columns:
        t = pd.to_numeric(data["T_FROM_LAUNCH_S"], errors="coerce")
        if t.notna().sum() >= 3:
            return t, None, "T_FROM_LAUNCH_S"

    packet = pd.to_numeric(data[packet_col], errors="coerce")
    alt = pd.to_numeric(data[alt_col], errors="coerce").interpolate(limit_direction="both").bfill().ffill()
    states = data[state_col].astype(str) if state_col in data.columns else pd.Series([""] * len(data), index=data.index)
    ascent_idx = np.where(states.eq("ASCENT").to_numpy())[0]
    first_ascent_pos = int(ascent_idx[0]) if len(ascent_idx) else None

    arr = alt.to_numpy(float)
    if first_ascent_pos is not None and first_ascent_pos > 3:
        base_slice = arr[max(0, first_ascent_pos - 40):first_ascent_pos]
    else:
        base_slice = arr[:max(5, min(50, len(arr)))]
    baseline = float(np.nanmedian(base_slice))
    noise = float(np.nanmedian(np.abs(base_slice - baseline))) if len(base_slice) else 0.0
    threshold = baseline + max(2.0, 6.0 * noise, 0.015 * abs(float(np.nanmax(arr)) - baseline))
    rise = np.where(arr > threshold)[0]
    if len(rise):
        launch_packet = float(packet.iloc[int(rise[0])])
        method = "ALTITUDE_RISE"
    elif first_ascent_pos is not None:
        launch_packet = float(packet.iloc[first_ascent_pos])
        method = "STATE_ASCENT"
    else:
        launch_packet = float(packet.iloc[0])
        method = "FIRST_PACKET"
    return (packet - launch_packet) / FS, launch_packet, method


def prepare_launch_window(df, columns, packet_col="PACKET_COUNT", state_col="STATE", alt_col="ALTITUDE"):
    need = [packet_col, state_col, alt_col] + (["T_FROM_LAUNCH_S"] if "T_FROM_LAUNCH_S" in df.columns else []) + [c for c in columns if c not in [packet_col, state_col, alt_col, "T_FROM_LAUNCH_S"]]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns for this graph family: {missing}")

    data = df[need].copy()
    data[packet_col] = pd.to_numeric(data[packet_col], errors="coerce")
    data[alt_col] = pd.to_numeric(data[alt_col], errors="coerce")
    for c in columns:
        if c in data.columns:
            data[c] = pd.to_numeric(data[c], errors="coerce")
    data[state_col] = data[state_col].astype(str)
    data = data.dropna(subset=[packet_col, alt_col]).sort_values(packet_col).reset_index(drop=True)

    if data.empty:
        raise ValueError("No usable rows after filtering packet/altitude data.")

    # Canonical timebase: altitude-triggered launch is x=0.
    data["T"], launch_packet, launch_method = choose_timebase_and_launch(data, packet_col, state_col, alt_col)

    # Graph crop is based on physical altitude landing, not first STATE=LANDED.
    ev = event_context_from_time_aligned(data, "T", alt_col, state_col)
    plot_end = ev["plot_end_t"]
    win = data[(data["T"] >= -PRE_LAUNCH) & (data["T"] <= plot_end)].copy()
    if win.empty:
        win = data.copy()

    agg = {"STATE": (state_col, mode_or_first), "ALTITUDE": (alt_col, "median")}
    for c in columns:
        if c not in agg and c in win.columns:
            agg[c] = (c, "median")
    g = win.groupby("T", as_index=False).agg(**agg).sort_values("T").reset_index(drop=True)
    if g.empty:
        raise ValueError("No grouped time rows available for plotting.")
    return g


def altitude_context(g):
    if g is None or len(g) == 0:
        raise ValueError("No altitude rows available for altitude context.")
    x = g["T"].to_numpy(float)
    y = g["ALTITUDE"].to_numpy(float)
    pre = x < 0
    post = x >= 0
    baseline = float(np.nanmedian(y[pre])) if pre.any() else float(y[0])
    baseline = max(0.0, baseline)
    x_pre = x[pre]
    y_pre = np.full_like(x_pre, baseline)
    x_post = x[post].copy()
    y_post = y[post].copy()
    if len(x_post) and abs(x_post[0]) < 1e-12:
        x_post[0] = 1.0 / FS
    tmp = pd.DataFrame({"x": np.concatenate([[0.0], x_post]),
                        "y": np.concatenate([[baseline], y_post])}).groupby("x", as_index=False).median()
    xs = tmp["x"].to_numpy(float)
    ys = tmp["y"].to_numpy(float)
    ys = np.maximum(smooth_series(ys, 41), 0.0)
    ys[0] = baseline
    xd = np.linspace(0, float(xs.max()), 1800)
    yd = np.maximum(PchipInterpolator(xs, ys)(xd), 0.0)
    yd[0] = baseline
    return np.concatenate([x_pre, xd]), np.concatenate([y_pre, yd]), baseline

def attach_altitude_axis(ax, g, color="#F28E2B"):
    ax2 = ax.twinx()
    axx, ayy, _ = altitude_context(g)
    ax2.plot(axx, ayy, color=color, linewidth=1.85, alpha=0.72, zorder=3)
    ax2.set_ylabel("Altitude (m)", fontsize=11, color=color)
    ax2.tick_params(axis="y", colors=color, labelsize=9.5)
    ax2.spines["right"].set_color(color)
    ax2.spines["top"].set_visible(False)
    ax2.set_ylim(0, 1000)
    ax2.yaxis.set_major_locator(MultipleLocator(100))
    ax2.yaxis.set_minor_locator(AutoMinorLocator(2))
    return ax2

# ---------------- ALTITUDE ----------------
def generate_altitude(df, outdir):
    g = prepare_launch_window(df, [])
    x, y, baseline = altitude_context(g)
    segs = make_segments(g)

    fig, ax = plt.subplots(figsize=(16.4, 7.3))
    fig.patch.set_facecolor("white")
    add_state_background(ax, segs)
    ax.plot(x, y, color=ALT_COLOR, lw=3.0, solid_capstyle="round", zorder=6)
    ax.set_xlim(-PRE_LAUNCH, float(g["T"].max()))
    ax.set_ylim(0, max(900, float(np.nanmax(y)) * 1.08))
    basic_time_style(ax, "Altitude Profile — Smooth", "Altitude (m)", (-PRE_LAUNCH, float(g["T"].max())), ax.get_ylim())
    ax.legend(handles=[
        Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32, label="State highlight / strip"),
        Line2D([0], [0], color=ALT_COLOR, lw=3, label="Smoothed altitude"),
    ], title="ALTITUDE", loc="upper left", bbox_to_anchor=(1.012, 0.76), fontsize=9, title_fontsize=10, frameon=True)
    path = outdir / "altitude_smooth_p15q_v5_packet_time_3s_before_launch.png"
    savefig(fig, path)
    return [path, path.with_suffix(".svg")]

# ---------------- VELOCITY ----------------
def prepare_velocity(df):
    g = prepare_launch_window(df, [])
    x_all = g["T"].to_numpy(float)
    y_all = np.maximum(smooth_series(g["ALTITUDE"].to_numpy(float), 31), 0.0)
    states = g["STATE"].astype(str).to_numpy()
    ap_idx = int(np.nanargmax(y_all))
    ap_t = float(x_all[ap_idx])
    pr_rows = g[g["STATE"].eq("PAYLOAD_RELEASE")]
    pr_t_abs = float(pr_rows["T"].iloc[0]) if not pr_rows.empty else ap_t + 30
    landed_rows = g[g["STATE"].eq("LANDED")]
    land_t_abs = float(landed_rows["T"].iloc[0]) if not landed_rows.empty else float(x_all[-1])
    mask = (x_all >= ap_t) & (x_all <= land_t_abs)
    if not mask.any():
        mask = np.ones_like(x_all, dtype=bool)
    t = x_all[mask] - ap_t
    alt = y_all[mask]
    st = states[mask]
    payload_t = pr_t_abs - ap_t
    end_t = land_t_abs - ap_t
    rate_raw = -np.gradient(alt, t)
    rate = np.clip(smooth_series(rate_raw, 21), 0, 22)
    def reg_rate(x, yy):
        if len(x) < 2:
            return np.nan
        m, b = np.polyfit(x, yy, 1)
        return -float(m)
    s1 = reg_rate(t[(t>=0)&(t<=payload_t)], alt[(t>=0)&(t<=payload_t)])
    s2 = reg_rate(t[(t>=payload_t)&(t<=end_t)] - payload_t, alt[(t>=payload_t)&(t<=end_t)])
    tmp = pd.DataFrame({"T": t, "STATE": st})
    segs = make_segments(tmp)
    return t, alt, rate, segs, payload_t, end_t, s1, s2

def velocity_base(ax, title, ylabel, payload_t, end_t, ylim):
    ax.set_facecolor("white")
    ax.set_title(title, fontsize=19, fontweight="bold", pad=14)
    ax.set_xlabel("Time Since Apogee (s)", fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_xlim(0, end_t)
    ax.set_ylim(*ylim)
    ax.xaxis.set_major_locator(MultipleLocator(20))
    ax.xaxis.set_minor_locator(MultipleLocator(5))
    ax.grid(True, which="major", color=GRID, alpha=0.22, linewidth=0.65)
    ax.grid(True, which="minor", color=GRID, alpha=0.09, linewidth=0.35)
    ax.spines["top"].set_visible(False)
    ax.tick_params(axis="both", labelsize=10.5, colors=TEXT)

def generate_velocity(df, outdir):
    t, alt, rate, segs, payload_t, end_t, s1, s2 = prepare_velocity(df)
    c1, c2, c_alt = "#6F4CC3", "#E8742B", ALT_COLOR
    outputs = []
    stage1 = (t>=0)&(t<=payload_t)
    stage2 = (t>=payload_t)&(t<=end_t)

    fig, ax = plt.subplots(figsize=(16.4, 7.3)); fig.patch.set_facecolor("white")
    add_state_background(ax, segs)
    ax.fill_between([0, payload_t], 12, 18, color=c1, alpha=0.145, lw=0, zorder=1)
    ax.fill_between([payload_t, end_t], 2, 8, color=c2, alpha=0.150, lw=0, zorder=1)
    ax.plot(t[stage1], rate[stage1], color=c1, lw=2.35, zorder=5)
    ax.plot(t[stage2], rate[stage2], color=c2, lw=2.35, zorder=5)
    ax.hlines(s1, 0, payload_t, color=c1, lw=3, zorder=6)
    ax.hlines(s2, payload_t, end_t, color=c2, lw=3, zorder=6)
    velocity_base(ax, "Descent Control — Two-stage Average Descent Rate", "Descent Rate (m/s)", payload_t, end_t, (0, 20))
    ax.legend(handles=[
        Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32, label="State highlight / strip"),
        Patch(facecolor=c1, alpha=0.145, label="Stage 1 rule: 12–18 m/s"),
        Patch(facecolor=c2, alpha=0.150, label="Stage 2 rule: 2–8 m/s"),
        Line2D([0],[0], color=c1, lw=2.35, label="Stage 1 rate"),
        Line2D([0],[0], color=c2, lw=2.35, label="Stage 2 rate"),
        Line2D([0],[0], color=c1, lw=3, label=f"Average parachute: {s1:.2f} m/s"),
        Line2D([0],[0], color=c2, lw=3, label=f"Average paraglider: {s2:.2f} m/s"),
    ], title="DESCENT CONTROL", loc="upper left", bbox_to_anchor=(1.012,0.76), fontsize=8.9, title_fontsize=10, frameon=True)
    p = outdir/"velocity_descent_control_candidate_v7_no_internal_text.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]

    fig, ax = plt.subplots(figsize=(16.4, 7.3)); fig.patch.set_facecolor("white")
    add_state_background(ax, segs)
    td = np.linspace(0, end_t, 1600)
    ad = np.maximum(PchipInterpolator(t, alt)(td), 0)
    ax.plot(td, ad, color=c_alt, lw=3, zorder=5)
    # regression trend lines
    m1, b1 = np.polyfit(t[stage1], alt[stage1], 1)
    m2, b2 = np.polyfit(t[stage2]-payload_t, alt[stage2], 1)
    ax.plot([0,payload_t], [b1, m1*payload_t+b1], color=c1, ls="--", lw=2.25)
    ax.plot([payload_t,end_t], [b2, m2*(end_t-payload_t)+b2], color=c2, ls="--", lw=2.25)
    velocity_base(ax, "Velocity Plot — Altitude-Time Slope Trend", "Barometer Altitude (m)", payload_t, end_t, (0, max(1000, float(np.nanmax(alt))*1.1)))
    ax.legend(handles=[
        Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32, label="State highlight / strip"),
        Line2D([0],[0], color=c_alt, lw=3, label="Measured altitude"),
        Line2D([0],[0], color=c1, ls="--", lw=2.25, label=f"Parachute regression: {s1:.2f} m/s"),
        Line2D([0],[0], color=c2, ls="--", lw=2.25, label=f"Paraglider regression: {s2:.2f} m/s"),
    ], title="VELOCITY TREND", loc="upper left", bbox_to_anchor=(1.012,0.76), fontsize=8.9, title_fontsize=10, frameon=True)
    p = outdir/"velocity_altitude_slope_trend_candidate_v7_no_internal_text.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]

    fig, ax = plt.subplots(figsize=(16.8, 7.45)); fig.patch.set_facecolor("white")
    add_state_background(ax, segs)
    ax.fill_between([0, payload_t], 12, 18, color=c1, alpha=0.145, lw=0, zorder=1)
    ax.fill_between([payload_t, end_t], 2, 8, color=c2, alpha=0.150, lw=0, zorder=1)
    ax.plot(t[stage1], rate[stage1], color=c1, lw=2.35, zorder=5)
    ax.plot(t[stage2], rate[stage2], color=c2, lw=2.35, zorder=5)
    ax.hlines(s1, 0, payload_t, color=c1, lw=3, zorder=6)
    ax.hlines(s2, payload_t, end_t, color=c2, lw=3, zorder=6)
    velocity_base(ax, "Two-stage Descent Rate — Rule Trend + Altitude", "Descent Rate (m/s)", payload_t, end_t, (0,20.7))
    ax2 = ax.twinx()
    ax2.plot(td, ad, color=c_alt, lw=3)
    ax2.set_ylim(0, 1000)
    ax2.set_ylabel("Altitude (m)", color=c_alt)
    ax2.tick_params(axis="y", colors=c_alt)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_color(c_alt)
    ax2.yaxis.set_major_locator(MultipleLocator(100))
    ax.legend(handles=[
        Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32, label="State highlight / strip"),
        Patch(facecolor=c1, alpha=0.145, label="Stage 1 rule 12–18 m/s"),
        Patch(facecolor=c2, alpha=0.150, label="Stage 2 rule 2–8 m/s"),
        Line2D([0],[0], color=c1, lw=2.35, label="Stage 1 rate"),
        Line2D([0],[0], color=c2, lw=2.35, label="Stage 2 rate"),
        Line2D([0],[0], color=c_alt, lw=3, label="Altitude"),
    ], title="VELOCITY / ALTITUDE", loc="upper left", bbox_to_anchor=(1.012,0.76), fontsize=8.8, title_fontsize=10, frameon=True)
    p = outdir/"velocity_rule_trend_altitude_candidate_v7_no_internal_text.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    return outputs

# ---------------- VOLTAGE / TEMPERATURE ----------------
def iqr_filter(x, y, k=1.5):
    x = np.asarray(x, float); y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(y) < 8:
        return x, y
    q1, q3 = np.nanpercentile(y, [25, 75])
    iqr = q3 - q1
    if iqr <= 0:
        return x, y
    keep = (y >= q1 - k*iqr) & (y <= q3 + k*iqr)
    return x[keep], y[keep]

def plot_scalar_family(df, outdir, sensor, col, ylabel, color, prefix):
    g = prepare_launch_window(df, [col])
    x = g["T"].to_numpy(float)
    y = g[col].to_numpy(float)
    ys = smooth_series(y, 41)
    segs = make_segments(g)
    xlim = (-PRE_LAUNCH, float(x.max()))
    ymin, ymax = np.nanmin([np.nanmin(y), np.nanmin(ys)]), np.nanmax([np.nanmax(y), np.nanmax(ys)])
    pad = max((ymax-ymin)*0.12, 0.05)
    ylim = (ymin-pad, ymax+pad)
    outputs = []
    variants = [
        ("line", f"{sensor} Line", "line"),
        ("clean", f"{sensor} Clean", "clean"),
        ("scatter_fitted", f"{sensor} Scatter + Linear Fit", "scatter"),
        ("dual_altitude", f"{sensor} + Altitude", "dual"),
        ("scatter_fitted_dual_altitude", f"{sensor} Scatter + Linear Fit + Altitude", "scatterdual"),
    ]
    for suffix, title, kind in variants:
        fig, ax = plt.subplots(figsize=(16.8 if "dual" in kind else 16.4, 7.45 if "dual" in kind else 7.3))
        fig.patch.set_facecolor("white")
        add_state_background(ax, segs)
        handles = [Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32, label="State highlight / strip")]
        if kind == "line":
            ax.plot(x, y, color=color, lw=2.1, alpha=0.9, zorder=5)
            handles.append(Line2D([0],[0], color=color, lw=2.1, label=f"{sensor} raw line"))
        elif kind == "clean":
            ax.plot(x, ys, color=color, lw=2.65, zorder=5)
            handles.append(Line2D([0],[0], color=color, lw=2.65, label=f"{sensor} smoothed line"))
        elif kind == "scatter":
            xs, yy = iqr_filter(x, y)
            ax.scatter(xs, yy, s=16, color=color, alpha=0.65, edgecolor="none", zorder=5)
            if len(xs) >= 2:
                m,b = np.polyfit(xs, yy, 1)
                fx = np.array([np.nanmin(xs), np.nanmax(xs)])
                ax.plot(fx, m*fx+b, color="#202020", lw=2.45, ls="--", zorder=6)
            handles += [Line2D([0],[0], marker="o", color="w", markerfacecolor=color, markersize=6, label=f"{sensor} filtered data"),
                        Line2D([0],[0], color="#202020", lw=2.45, ls="--", label="Linear regression fit")]
        elif kind == "dual":
            ax.plot(x, ys, color=color, lw=2.65, zorder=5)
            attach_altitude_axis(ax, g)
            handles += [Line2D([0],[0], color=color, lw=2.65, label=f"{sensor} smoothed line"),
                        Line2D([0],[0], color="#F28E2B", lw=1.85, label="Altitude")]
        elif kind == "scatterdual":
            xs, yy = iqr_filter(x, y)
            ax.scatter(xs, yy, s=18, color=color, alpha=0.62, edgecolor="none", zorder=5)
            if len(xs) >= 2:
                m,b = np.polyfit(xs, yy, 1)
                fx = np.array([np.nanmin(xs), np.nanmax(xs)])
                ax.plot(fx, m*fx+b, color="#202020", lw=2.45, ls="--", zorder=6)
            attach_altitude_axis(ax, g)
            handles += [Line2D([0],[0], marker="o", color="w", markerfacecolor=color, markersize=6, label=f"{sensor} filtered scatter"),
                        Line2D([0],[0], color="#202020", lw=2.45, ls="--", label="Linear regression fit"),
                        Line2D([0],[0], color="#F28E2B", lw=1.85, label="Altitude")]
        basic_time_style(ax, title, ylabel, xlim, ylim)
        ax.legend(handles=handles, title=(sensor.upper() if "dual" not in kind else f"{sensor.upper()} / ALTITUDE"),
                  loc="upper left", bbox_to_anchor=(1.012,0.76), fontsize=9, title_fontsize=10, frameon=True)
        p = outdir / f"{prefix}_{suffix}_candidate_v1.png"
        if suffix == "scatter_fitted_dual_altitude":
            p = outdir / f"{prefix}_{suffix}_candidate_v2.png"
        savefig(fig, p)
        outputs += [p, p.with_suffix(".svg")]
    return outputs

def generate_voltage_temperature(df, outdir):
    outputs = []
    outputs += plot_scalar_family(df, outdir, "Voltage", "VOLTAGE", "Battery Voltage (V)", "#4E79A7", "voltage")
    outputs += plot_scalar_family(df, outdir, "Temperature", "TEMPERATURE", "Temperature (°C)", "#E15759", "temperature")
    return outputs


def generate_placeholder(outdir, filename, title, message):
    """Create a PNG/SVG placeholder when a non-critical graph family cannot be generated."""
    fig, ax = plt.subplots(figsize=(12.5, 6.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")
    ax.text(0.5, 0.62, title, ha="center", va="center", fontsize=22, fontweight="bold", color="#1F2937")
    ax.text(0.5, 0.44, message, ha="center", va="center", fontsize=12.5, color="#4B5563", wrap=True)
    p = outdir / filename
    savefig(fig, p)
    return [p, p.with_suffix(".svg")]

# ---------------- GPS ----------------
def generate_gps(df, outdir):
    cols = ["GPS_LAT", "GPS_LON", "GPS_ALT", "ALTITUDE"]
    data = df[["PACKET_COUNT","STATE"] + (["T_FROM_LAUNCH_S"] if "T_FROM_LAUNCH_S" in df.columns else []) + cols].copy()
    data["PACKET_COUNT"] = pd.to_numeric(data["PACKET_COUNT"], errors="coerce")
    for c in cols:
        data[c] = pd.to_numeric(data[c], errors="coerce")
    data["STATE"] = data["STATE"].astype(str)
    data = data.dropna(subset=["PACKET_COUNT","STATE","GPS_LAT","GPS_LON","ALTITUDE"]).sort_values("PACKET_COUNT")
    valid = data["GPS_LAT"].between(-90,90)&data["GPS_LON"].between(-180,180)&~((data["GPS_LAT"].abs()<1e-10)&(data["GPS_LON"].abs()<1e-10))
    data = data[valid].copy()
    if data.empty or not data["STATE"].eq("ASCENT").any():
        return generate_placeholder(
            outdir,
            "gps_unavailable_placeholder.png",
            "GPS Graphs Unavailable",
            "No valid GPS latitude/longitude rows with ASCENT state were found after filtering. Other graph families can still be generated."
        )
    data["T"], launch_packet, launch_method = choose_timebase_and_launch(data, "PACKET_COUNT", "STATE", "ALTITUDE")
    ev = event_context_from_time_aligned(data, "T", "ALTITUDE", "STATE")
    plot_end = ev["plot_end_t"]
    data = data[(data["T"]>=-PRE_LAUNCH)&(data["T"]<=plot_end)]
    g = data.groupby("T", as_index=False).agg(STATE=("STATE", mode_or_first), GPS_LAT=("GPS_LAT","median"),
        GPS_LON=("GPS_LON","median"), GPS_ALT=("GPS_ALT","median"), ALTITUDE=("ALTITUDE","median")).sort_values("T")
    if g.empty:
        return generate_placeholder(
            outdir,
            "gps_unavailable_placeholder.png",
            "GPS Graphs Unavailable",
            "GPS rows became empty after time grouping. Other graph families can still be generated."
        )
    lat = smooth_series(g["GPS_LAT"], 13)
    lon = smooth_series(g["GPS_LON"], 13)
    alt = np.maximum(smooth_series(g["ALTITUDE"], 31), 0)
    t = g["T"].to_numpy(float)
    states = g["STATE"].astype(str).to_numpy()
    ap = int(np.nanargmax(alt))
    pr_idx = np.where(states=="PAYLOAD_RELEASE")[0]
    pr = int(pr_idx[0]) if len(pr_idx) else None
    last = len(g)-1
    colors = {"ASCENT":"#D62728","APOGEE":"#F2C300","DESCENT":"#6F4CC3","PROBE_RELEASE":"#F28E2B","PAYLOAD_RELEASE":"#2CA02C","LANDED":"#8C8C8C","LAUNCH_PAD":"#BDBDBD"}
    state_handles = [Line2D([0],[0], color=colors[s], lw=3, label=s) for s in ["ASCENT","APOGEE","DESCENT","PROBE_RELEASE","PAYLOAD_RELEASE","LANDED"] if s in set(states)]
    event_handles = [Line2D([0],[0],marker="o",color="w",markerfacecolor="#D62728",markeredgecolor="black",markersize=7,label="Apogee"),
                     Line2D([0],[0],marker="o",color="w",markerfacecolor="#6F4CC3",markeredgecolor="black",markersize=7,label="Payload Release"),
                     Line2D([0],[0],marker="v",color="w",markerfacecolor="#009E73",markeredgecolor="black",markersize=7,label="Landing / Last Point")]
    outputs=[]
    pts = np.array([lat, lon, alt]).T.reshape(-1,1,3)
    seg3d = np.concatenate([pts[:-1], pts[1:]], axis=1)
    seg_colors = [colors.get(states[i],"#666") for i in range(len(states)-1)]
    fig = plt.figure(figsize=(13.8,10.2)); fig.patch.set_facecolor("white")
    ax = fig.add_subplot(111, projection="3d"); ax.set_facecolor("white")
    ax.add_collection3d(Line3DCollection(seg3d, colors=seg_colors, linewidths=2.65, alpha=0.97))
    ax.scatter(lat[ap], lon[ap], alt[ap], s=72, color="#D62728", edgecolor="black", linewidth=0.7, depthshade=False)
    if pr is not None: ax.scatter(lat[pr], lon[pr], alt[pr], s=72, color="#6F4CC3", edgecolor="black", linewidth=0.7, depthshade=False)
    ax.scatter(lat[last], lon[last], alt[last], s=82, marker="v", color="#009E73", edgecolor="black", linewidth=0.7, depthshade=False)
    ax.set_xlim(np.nanmin(lat)-max((np.nanmax(lat)-np.nanmin(lat))*0.08,0.00008), np.nanmax(lat)+max((np.nanmax(lat)-np.nanmin(lat))*0.08,0.00008))
    ax.set_ylim(np.nanmin(lon)-max((np.nanmax(lon)-np.nanmin(lon))*0.08,0.00008), np.nanmax(lon)+max((np.nanmax(lon)-np.nanmin(lon))*0.08,0.00008))
    ax.set_zlim(0, max(1000, np.nanmax(alt)*1.05))
    ax.view_init(elev=25, azim=-128); ax.set_box_aspect((1.12,1,0.78), zoom=0.88)
    ax.set_title("GPS 3D Flight Path — Latitude / Longitude / Altitude", fontsize=18, fontweight="bold", pad=18)
    ax.set_xlabel("Latitude (degrees North)", labelpad=10); ax.set_ylabel("Longitude (degrees East)", labelpad=10); ax.set_zlabel("Altitude (m)", labelpad=10)
    ax.legend(handles=state_handles+event_handles, title="STATE / PATH", loc="upper left", bbox_to_anchor=(1.02,0.92), fontsize=9, title_fontsize=10, frameon=True)
    p=outdir/"gps_3d_latlon_altitude_candidate_v3_reference_style.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    # Ground track
    fig, ax = plt.subplots(figsize=(11.5,10)); fig.patch.set_facecolor("white"); ax.set_facecolor("white")
    for i in range(len(g)-1):
        ax.plot(lat[i:i+2], lon[i:i+2], color=colors.get(states[i],"#666"), lw=2.45, alpha=0.96)
    ax.scatter(lat[0],lon[0],s=60,color="#202020",marker="s",edgecolor="white",linewidth=0.7,zorder=7)
    ax.scatter(lat[ap],lon[ap],s=72,color="#D62728",edgecolor="black",linewidth=0.7,zorder=8)
    if pr is not None: ax.scatter(lat[pr],lon[pr],s=72,color="#6F4CC3",edgecolor="black",linewidth=0.7,zorder=8)
    ax.scatter(lat[last],lon[last],s=82,marker="v",color="#009E73",edgecolor="black",linewidth=0.7,zorder=8)
    ax.set_title("GPS Ground Track — Latitude / Longitude", fontsize=19, fontweight="bold", pad=14)
    ax.set_xlabel("Latitude (degrees North)"); ax.set_ylabel("Longitude (degrees East)")
    ax.grid(True, color=GRID, alpha=0.24); ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.legend(handles=state_handles+event_handles+[Line2D([0],[0],marker="s",color="w",markerfacecolor="#202020",markeredgecolor="white",markersize=7,label="Start")], title="STATE / PATH", loc="upper left", bbox_to_anchor=(1.02,0.92), fontsize=9, title_fontsize=10, frameon=True)
    p=outdir/"gps_ground_track_latlon_candidate_v3.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    # 3-axis time
    fig, ax = plt.subplots(figsize=(16.4,7.3)); fig.patch.set_facecolor("white")
    add_state_background(ax, make_segments(g))
    lat0 = float(g.loc[g["T"]<0, "GPS_LAT"].median()) if (g["T"]<0).any() else float(g["GPS_LAT"].iloc[0])
    lon0 = float(g.loc[g["T"]<0, "GPS_LON"].median()) if (g["T"]<0).any() else float(g["GPS_LON"].iloc[0])
    ax.plot(t,(lat-lat0)*1e5,color="#0072B2",lw=2.1,zorder=5)
    ax.plot(t,(lon-lon0)*1e5,color="#009E73",lw=2.1,zorder=5)
    ax.plot(t,alt/100,color="#CC79A7",lw=2.1,zorder=5)
    basic_time_style(ax, "GPS 3-Axis Time — Relative Lat/Lon + Altitude Scale", "Scaled value: lat/lon ×1e5, altitude ÷100", (-PRE_LAUNCH, float(t.max())))
    ax.legend(handles=[Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32,label="State highlight / strip"),
                       Line2D([0],[0],color="#0072B2",lw=2.1,label="Latitude offset ×1e5"),
                       Line2D([0],[0],color="#009E73",lw=2.1,label="Longitude offset ×1e5"),
                       Line2D([0],[0],color="#CC79A7",lw=2.1,label="Altitude ÷100")],
              title="GPS AXES", loc="upper left", bbox_to_anchor=(1.012,0.76), fontsize=9, title_fontsize=10, frameon=True)
    p=outdir/"gps_3_axis_time_latlon_candidate_v3.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    # Alt check
    fig, ax = plt.subplots(figsize=(16.4,7.3)); fig.patch.set_facecolor("white")
    add_state_background(ax, make_segments(g))
    gps_alt = g["GPS_ALT"].interpolate(limit_direction="both")
    pre = g["T"]<0
    gps_rel = gps_alt - (gps_alt[pre].median() if pre.any() else gps_alt.iloc[0])
    ax.plot(t, alt, color="#202020", lw=2.45, zorder=5)
    ax.plot(t, gps_rel, color="#4E79A7", lw=1.95, alpha=0.82, zorder=4)
    basic_time_style(ax, "GPS Altitude Check — GPS Relative vs Barometer", "Altitude (m)", (-PRE_LAUNCH, float(t.max())), (0, max(1000,float(np.nanmax(alt))*1.05)))
    ax.legend(handles=[Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32,label="State highlight / strip"),
                       Line2D([0],[0],color="#202020",lw=2.45,label="Barometer altitude"),
                       Line2D([0],[0],color="#4E79A7",lw=1.95,label="GPS altitude relative")],
              title="GPS ALTITUDE CHECK", loc="upper left", bbox_to_anchor=(1.012,0.76), fontsize=9, title_fontsize=10, frameon=True)
    p=outdir/"gps_altitude_check_candidate_v3.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    return outputs

# ---------------- MULTI AXIS ----------------
def global_ylim(ys, pad=0.14):
    clean = []
    for y in ys:
        arr = np.asarray(y, float)
        arr = arr[np.isfinite(arr)]
        if len(arr):
            clean.append(arr)
    if not clean:
        return (-1, 1)
    vals = np.concatenate(clean)
    mn, mx = float(np.nanmin(vals)), float(np.nanmax(vals))
    if mx == mn:
        return (mn - 1, mx + 1)
    p = (mx - mn) * pad
    return (mn - p, mx + p)

def label_with_unit(label, unit):
    return f"{label} ({unit})" if "(" not in label and ")" not in label else label

def valid_numeric_columns(df, columns, min_samples=10, min_range=1e-6):
    """Return columns that contain enough real numeric variation for plotting."""
    ok = []
    stats = {}
    for c in columns:
        if c not in df.columns:
            stats[c] = {"exists": False, "non_nan": 0, "range": None}
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        non_nan = int(s.notna().sum())
        rng = float(s.max() - s.min()) if non_nan else np.nan
        stats[c] = {"exists": True, "non_nan": non_nan, "range": rng}
        if non_nan >= min_samples and np.isfinite(rng) and abs(rng) > min_range:
            ok.append(c)
    return ok, stats


def choose_tilt_rate_columns(df):
    """Choose actual tilt-rate columns.

    Frozen rule: Tilt graph unit is degree/s.
    If derived tilt columns are empty placeholders, use GYRO_R/P/Y as tilt-rate fallback.
    """
    direct = ["TILT_R", "TILT_P", "TILT_Y"]
    derived = ["TILT_ROLL_DERIVED", "TILT_PITCH_DERIVED", "TILT_YAW_DERIVED"]
    gyro = ["GYRO_R", "GYRO_P", "GYRO_Y"]

    direct_ok, direct_stats = valid_numeric_columns(df, direct, min_samples=10, min_range=1e-4)
    if len(direct_ok) >= 2:
        labels = {"TILT_R": "Tilt Roll Rate", "TILT_P": "Tilt Pitch Rate", "TILT_Y": "Tilt Yaw Rate"}
        return direct_ok, [labels[c] for c in direct_ok], "Tilt Rate (degree/s)", "direct tilt columns", direct_stats

    derived_ok, derived_stats = valid_numeric_columns(df, derived, min_samples=10, min_range=1e-4)
    if len(derived_ok) >= 2:
        labels = {
            "TILT_ROLL_DERIVED": "Tilt Roll Rate",
            "TILT_PITCH_DERIVED": "Tilt Pitch Rate",
            "TILT_YAW_DERIVED": "Tilt Yaw Rate",
        }
        return derived_ok, [labels[c] for c in derived_ok], "Tilt Rate (degree/s)", "derived tilt columns", derived_stats

    gyro_ok, gyro_stats = valid_numeric_columns(df, gyro, min_samples=10, min_range=1e-4)
    if len(gyro_ok) >= 2:
        labels = {"GYRO_R": "Tilt Roll Rate", "GYRO_P": "Tilt Pitch Rate", "GYRO_Y": "Tilt Yaw Rate"}
        return gyro_ok, [labels[c] for c in gyro_ok], "Tilt Rate (degree/s)", "GYRO fallback", gyro_stats

    all_stats = {"direct": direct_stats, "derived": derived_stats, "gyro": gyro_stats}
    return [], [], "Tilt Rate (degree/s)", "unavailable", all_stats


def generate_multi_axis(df, outdir):
    # Multi-axis units are explicit so every graph/axis has a unit label.
    # Tilt is treated as rate in degree/s, matching the frozen project rule.
    families = [
        ("acceleration","Acceleration",["ACCEL_R","ACCEL_P","ACCEL_Y"],["Accel R","Accel P","Accel Y"],"Acceleration (m/s²)"),
        ("gyro","Gyro",["GYRO_R","GYRO_P","GYRO_Y"],["Gyro R","Gyro P","Gyro Y"],"Gyro Rate (degree/s)"),
        ("angular_velocity","Angular Velocity",["GYRO_R","GYRO_P","GYRO_Y"],["Roll Rate","Pitch Rate","Yaw Rate"],"Angular Velocity (degree/s)"),
        # Tilt is chosen dynamically below. Do not use empty derived placeholder columns.

        ("servo_current","Servo Current",["SERVO_CURRENT_1","SERVO_CURRENT_2","SERVO_CURRENT_3"],["Servo Current 1","Servo Current 2","Servo Current 3"],"Servo Current (mA)"),
        ("servo_target","Servo Target",["SERVO_TARGET_1","SERVO_TARGET_2","SERVO_TARGET_3"],["Servo Target 1","Servo Target 2","Servo Target 3"],"Servo Target (command units)"),
    ]

    tilt_cols, tilt_labels, tilt_unit, tilt_source, tilt_stats = choose_tilt_rate_columns(df)
    if tilt_cols:
        families.insert(3, ("tilt","Tilt",tilt_cols,tilt_labels,tilt_unit))
    else:
        # Keep a clear diagnostic image instead of an empty ±0.9 graph.
        generate_placeholder(
            outdir,
            "tilt_unavailable_placeholder.png",
            "Tilt Graphs Unavailable",
            "No usable tilt-rate data found. Checked direct tilt, derived tilt, and GYRO_R/P/Y fallback columns."
        )

    colors = ["#0072B2","#009E73","#CC79A7"]
    outputs=[]
    for key,name,cols,labels,unit in families:
        if not all(c in df.columns for c in cols): continue
        g = prepare_launch_window(df, cols)
        x = g["T"].to_numpy(float); segs = make_segments(g); ys=[g[c].to_numpy(float) for c in cols]
        xlim=(-PRE_LAUNCH,float(x.max())); ylim=global_ylim(ys)
        # focus overview + each axis, normal and dual
        for dual in [False, True]:
            for mode_i in [None]+list(range(len(cols))):
                fig, ax = plt.subplots(figsize=(16.4,7.3)); fig.patch.set_facecolor("white")
                add_state_background(ax,segs)
                handles=[Patch(facecolor=STATE_COLORS["ASCENT"], alpha=0.32, label="State highlight / strip")]
                if mode_i is None:
                    for y,c,lab in zip(ys,colors,labels):
                        ax.plot(x,y,color=c,lw=2.2,alpha=0.94,zorder=5)
                        handles.append(Line2D([0],[0],color=c,lw=2.2,label=label_with_unit(lab, unit)))
                    title=f"{name} Focus — Overview"
                    mode="overview"
                else:
                    for i,(y,c,lab) in enumerate(zip(ys,colors,labels)):
                        if i==mode_i:
                            ax.plot(x,y,color=c,lw=2.75,alpha=0.98,zorder=6)
                        else:
                            ax.plot(x,y,color="#8A8F98",lw=1.65,alpha=0.25,zorder=5)
                    handles += [Line2D([0],[0],color=colors[mode_i],lw=2.75,label=f"{label_with_unit(labels[mode_i], unit)} focus"),
                                Line2D([0],[0],color="#8A8F98",lw=1.65,alpha=0.45,label="Other axes shadow")]
                    title=f"{name} Focus — {labels[mode_i]}"
                    mode=f"focus_{mode_i+1}"
                basic_time_style(ax, title + (" + Altitude" if dual else ""), unit, xlim, ylim)
                if dual:
                    attach_altitude_axis(ax,g)
                    handles.append(Line2D([0],[0],color="#F28E2B",lw=1.85,label="Altitude"))
                ax.legend(handles=handles,title=name.upper(),loc="upper left",bbox_to_anchor=(1.012,0.76),fontsize=8.9,title_fontsize=10,frameon=True)
                p=outdir/f"{key}_{mode}{'_dual_altitude' if dual else ''}_candidate_v2.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
        # compared stacked normal and dual
        for dual in [False, True]:
            fig, axes = plt.subplots(len(cols),1,figsize=(16.2,2.9*len(cols)+1.4),sharex=True,sharey=True)
            if len(cols)==1: axes=[axes]
            fig.patch.set_facecolor("white")
            for ax,y,c,lab in zip(axes,ys,colors,labels):
                add_state_background(ax,segs)
                ax.plot(x,y,color=c,lw=2.35,zorder=5)
                ax.set_ylabel(label_with_unit(lab, unit),fontsize=10.5)
                ax.set_xlim(*xlim); ax.set_ylim(*ylim)
                ax.grid(True,which="major",color=GRID,alpha=0.22,linewidth=0.62)
                ax.grid(True,which="minor",color=GRID,alpha=0.09,linewidth=0.32)
                ax.yaxis.set_major_locator(MaxNLocator(nbins=5)); ax.yaxis.set_minor_locator(AutoMinorLocator(3))
                ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
                if dual: attach_altitude_axis(ax,g)
            axes[0].set_title(f"{name} Compared — Stacked Axes" + (" + Altitude" if dual else ""), fontsize=19, fontweight="bold", pad=12)
            axes[-1].set_xlabel("Mission Time Relative to Launch (s)", fontsize=12)
            axes[-1].xaxis.set_major_locator(MultipleLocator(20)); axes[-1].xaxis.set_minor_locator(MultipleLocator(5))
            fig.text(0.012,0.5,unit,va="center",rotation="vertical",fontsize=12)
            handles=[Patch(facecolor=STATE_COLORS["ASCENT"],alpha=0.32,label="State highlight / strip")] + [Line2D([0],[0],color=c,lw=2.35,label=label_with_unit(lab, unit)) for c,lab in zip(colors,labels)]
            if dual: handles.append(Line2D([0],[0],color="#F28E2B",lw=1.85,label="Altitude"))
            fig.legend(handles=handles,title=name.upper(),loc="center left",bbox_to_anchor=(0.885,0.5),fontsize=8.9,title_fontsize=10,frameon=True)
            fig.subplots_adjust(right=0.84,hspace=0.16)
            p=outdir/f"{key}_compared_stacked{'_dual_altitude' if dual else ''}_candidate_v2.png"; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    return outputs

# ---------------- CONOPS ----------------
def generate_conops(df, outdir):
    g = prepare_launch_window(df, [])
    x_actual, y_actual, _ = altitude_context(g)
    ap_i = int(np.nanargmax(y_actual))
    ev = event_context_from_time_aligned(g, "T", "ALTITUDE", "STATE")
    events = {
        "actual_apogee_t": float(x_actual[ap_i]),
        "actual_apogee_alt": float(y_actual[ap_i]),
        "actual_payload_t": ev["payload_state_t"] if np.isfinite(ev["payload_state_t"]) else ev["payload_80_t"],
        "actual_landing_t": ev["landing_physical_t"] if np.isfinite(ev["landing_physical_t"]) else float(g["T"].max())
    }
    events["actual_payload_alt"] = float(np.interp(events["actual_payload_t"], x_actual, y_actual)) if np.isfinite(events["actual_payload_t"]) else np.nan
    planned_t_apogee=11.2; planned_h_apogee=681.0; planned_h_payload=681.0*0.80
    stage1_rate=15.0; stage2_rate=5.0; h2=2.0
    planned_t_payload=planned_t_apogee+(planned_h_apogee-planned_h_payload)/stage1_rate
    planned_t_2m=planned_t_payload+(planned_h_payload-h2)/stage2_rate
    planned_t_land=planned_t_2m+h2/stage2_rate
    def yplan(x):
        return np.interp(x, [-PRE_LAUNCH,0,planned_t_apogee,planned_t_payload,planned_t_2m,planned_t_land],
                         [0,0,planned_h_apogee,planned_h_payload,h2,0], left=0, right=0)
    outputs=[]
    for scale, fname in [("linear","conops_altitude_linear_actual_vs_planned_v6_pale_blue_frozen.png"),
                         ("log10","conops_altitude_log10_actual_vs_planned_v6_pale_blue_frozen.png"),
                         ("symlog","conops_altitude_symlog_actual_vs_planned_v6_pale_blue_frozen.png")]:
        end_t=max(float(g["T"].max()), events["actual_landing_t"], planned_t_land)+1
        xg=np.linspace(-PRE_LAUNCH,end_t,2400); yp=yplan(xg)
        ymax=max(900,float(np.nanmax(y_actual))*1.06,planned_h_apogee*1.2)
        ya=np.maximum(y_actual,1) if scale=="log10" else y_actual
        ypp=np.maximum(yp,1) if scale=="log10" else yp
        fig,ax=plt.subplots(figsize=(17.2,6.1)); fig.patch.set_facecolor("white"); ax.set_facecolor("#F7FCFF")
        ax.set_xlim(-PRE_LAUNCH,end_t); ax.set_xlabel("Mission Time (s)"); ax.set_ylabel("Altitude (m)")
        ax.grid(True,which="major",color="#8CA6B8",alpha=0.26,linewidth=0.66); ax.grid(True,which="minor",color="#8CA6B8",alpha=0.10,linewidth=0.35)
        ax.xaxis.set_major_locator(MultipleLocator(20)); ax.xaxis.set_minor_locator(MultipleLocator(5))
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        if scale=="linear":
            ax.set_ylim(0,ymax); ax.yaxis.set_major_locator(MultipleLocator(200)); ax.yaxis.set_minor_locator(AutoMinorLocator(4))
        elif scale=="log10":
            ax.set_yscale("log"); ax.set_ylim(1,ymax); ax.yaxis.set_major_locator(LogLocator(base=10,numticks=6)); ax.yaxis.set_minor_locator(LogLocator(base=10,subs=np.arange(2,10)*0.1,numticks=12)); ax.yaxis.set_minor_formatter(NullFormatter())
        else:
            ax.set_yscale("symlog", linthresh=10,linscale=1.0,base=10); ax.set_ylim(0,ymax)
        ax.hlines(2,-PRE_LAUNCH,end_t,color="#5F6F7D",lw=1.0,ls=(0,(1,2)),alpha=0.75,zorder=1)
        ax.text(end_t-1.8, 2+(ymax*0.006 if scale=="linear" else 0.4), "2 m", fontsize=9.5, color="#5F6F7D", ha="right", va="bottom")
        ax.plot(x_actual,ya,color=DARK,lw=2.85,zorder=6)
        ax.plot(xg,ypp,color=DARK,lw=2.45,ls=(0,(8,5)),zorder=5)
        for x,c,actual in [(events["actual_apogee_t"],"#D62728",True),(planned_t_apogee,"#D62728",False),(events["actual_payload_t"],"#7E57C2",True),(planned_t_payload,"#7E57C2",False),(events["actual_landing_t"],"#2CA25F",True),(planned_t_land,"#2CA25F",False)]:
            if np.isfinite(x): ax.axvline(x,color=c,ls="-" if actual else (0,(4,3)),lw=1.45 if actual else 1.35,alpha=0.88 if actual else 0.70,zorder=3)
        y_label = ymax*0.965 if scale=="linear" else (ymax/1.25 if scale=="log10" else ymax*0.94)
        for x,txt,c in [(events["actual_apogee_t"],"A Apo","#D62728"),(planned_t_apogee,"P Apo","#D62728"),(events["actual_payload_t"],"A PR","#7E57C2"),(planned_t_payload,"P PR","#7E57C2"),(events["actual_landing_t"],"A Land","#2CA25F"),(planned_t_land,"P Land","#2CA25F")]:
            if np.isfinite(x): ax.text(x,y_label,txt,rotation=90,color=c,fontsize=8,fontweight="bold",ha="center",va="top",alpha=0.84,clip_on=True)
        ax.set_title(f"CONOPS {scale.upper()} — Actual vs Planned Mission Profile", fontsize=18, fontweight="bold", pad=12)
        ax.legend(handles=[Line2D([0],[0],color=DARK,lw=2.85,label="Actual altitude"),
                           Line2D([0],[0],color=DARK,lw=2.45,ls=(0,(8,5)),label="Planned altitude"),
                           Line2D([0],[0],color="#D62728",lw=1.45,label="Actual event boundary"),
                           Line2D([0],[0],color="#D62728",lw=1.35,ls=(0,(4,3)),label="Planned event boundary")], loc="upper right", fontsize=9.4, frameon=False)
        p=outdir/fname; savefig(fig,p); outputs += [p,p.with_suffix(".svg")]
    return outputs

def generate_all(csv_path, output_dir, selected_families=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path)
    generated = []
    family_results = []

    family_specs = [
        ("01_alt", "altitude", generate_altitude),
        ("02_vel", "velocity", generate_velocity),
        ("03_vt", "voltage_temperature", generate_voltage_temperature),
        ("04_gps", "gps", generate_gps),
        ("05_multi", "multi_axis", generate_multi_axis),
        ("06_conops", "conops", generate_conops),
    ]

    if selected_families is not None:
        selected_set = {str(x).strip().lower() for x in selected_families if str(x).strip()}
        family_specs = [spec for spec in family_specs if spec[1] in selected_set]
        if not family_specs:
            print("[engine] No graph families selected; diagnostics/report only.", flush=True)

    diagnostics_dir = output_dir / "00_diagnostics"
    diagnostics_dir.mkdir(exist_ok=True)

    for folder_name, family_name, func in family_specs:
        family_dir = output_dir / folder_name
        family_dir.mkdir(parents=True, exist_ok=True)
        try:
            print(f"[engine] Generating family: {family_name} -> {folder_name}", flush=True)
            files = func(df, family_dir)
            generated += files
            family_results.append({
                "family": family_name,
                "folder": folder_name,
                "status": "success",
                "generated_count": len(files),
                "generated_files": [str(Path(p).relative_to(output_dir)) for p in files],
            })
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            family_results.append({
                "family": family_name,
                "folder": folder_name,
                "status": "error",
                "error": err,
            })
            (family_dir / f"{family_name}_error.txt").write_text(err, encoding="utf-8")
            try:
                placeholder_files = generate_placeholder(
                    family_dir,
                    f"{family_name}_unavailable_placeholder.png",
                    f"{family_name.upper()} unavailable",
                    f"This graph family could not be generated from the selected log. Error: {err}"
                )
                generated += placeholder_files
            except Exception:
                pass
            print(f"[engine] WARNING: {family_name} failed: {err}", flush=True)

    manifest = {
        "generator": "CanSat Frozen Graph Generator - organized output mode",
        "sampling_rate_hz": FS,
        "output_structure": {
            "00_diagnostics": "manifests, input reports, error logs",
            "01_alt": "altitude graph family",
            "02_vel": "velocity graph family",
            "03_vt": "voltage and temperature graph family",
            "04_gps": "GPS graph family",
            "05_multi": "multi-axis focus/compared graph family",
            "06_conops": "CONOPS actual vs planned graph family",
        },
        "generated_count": len(generated),
        "generated_files": [str(Path(p).relative_to(output_dir)) for p in generated],
        "family_results": family_results,
        "selected_families": [family_name for _, family_name, _ in family_specs],
        "families": [
            "altitude_smooth_p15q_v5_packet_time_3s_before_launch",
            "velocity_family_candidate_v7_no_internal_text",
            "voltage_temperature_scalar_candidate_v2",
            "gps_family_candidate_v3_reference_style",
            "multi_axis_family_candidate_v2",
            "conops_altitude_actual_vs_planned_v6_pale_blue_frozen",
        ],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (diagnostics_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return generated

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to normalized flight CSV")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()
    files = generate_all(args.csv, args.out)
    print(f"Generated {len(files)} files into {args.out}")

if __name__ == "__main__":
    main()
