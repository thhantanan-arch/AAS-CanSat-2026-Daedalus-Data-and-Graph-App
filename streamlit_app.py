from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
import hashlib
import json
from pathlib import Path
from io import BytesIO

import streamlit as st

# Make matplotlib safe on headless web hosts.
os.environ.setdefault("MPLBACKEND", "Agg")

APP_TITLE = "CanSat Flight Data Studio"
SUPPORTED_TYPES = ["csv", "txt", "xlsx", "xlsm", "xls"]

GRAPH_FAMILIES = {
    "altitude": {"label": "Altitude", "hint": "main launch/apogee altitude previews", "folder_prefix": "01_alt"},
    "velocity": {"label": "Velocity", "hint": "descent-rate and rule trend graphs", "folder_prefix": "02_vel"},
    "conops": {"label": "CONOPS", "hint": "actual vs planned mission profile", "folder_prefix": "06_conops"},
    "voltage_temperature": {"label": "Voltage + Temperature", "hint": "sensor health scalar graphs", "folder_prefix": "03_vt"},
    "gps": {"label": "GPS", "hint": "3D path, ground track, GPS checks", "folder_prefix": "04_gps"},
    "multi_axis": {"label": "Motion / Multi-axis", "hint": "accel, gyro, tilt focus + compared graphs", "folder_prefix": "05_multi"},
}

PRESETS = {
    "Quick Check": ["altitude", "velocity", "conops"],
    "Sensor Health": ["voltage_temperature"],
    "GPS Pack": ["gps"],
    "Motion Pack": ["multi_axis"],
    "Report Pack": ["altitude", "velocity", "voltage_temperature", "gps", "conops"],
    "Full Export": list(GRAPH_FAMILIES.keys()),
    "Custom": ["altitude", "velocity", "conops"],
}


st.set_page_config(
    page_title="CFDS Web",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 0.75rem; padding-bottom: 1.5rem; max-width: 1180px; }
    div[data-testid="stSidebar"] { min-width: 250px; }
    .cfds-hero {
        padding: 0.95rem 1rem;
        border-radius: 20px;
        border: 1px solid rgba(148, 163, 184, 0.25);
        background: linear-gradient(135deg, rgba(15,23,42,0.96), rgba(30,64,175,0.88));
        color: white;
        margin-bottom: 0.75rem;
    }
    .cfds-hero h1 { margin: 0; font-size: clamp(1.45rem, 4vw, 2.1rem); }
    .cfds-hero p { opacity: 0.86; margin: 0.3rem 0 0 0; }
    .cfds-card {
        padding: 0.8rem 0.9rem;
        border-radius: 16px;
        border: 1px solid rgba(148, 163, 184, 0.25);
        background: rgba(248, 250, 252, 0.72);
    }
    .stButton button, .stDownloadButton button { min-height: 3rem; border-radius: 14px; font-weight: 700; }
    .metric-card { padding: 0.65rem 0.8rem; border-radius: 14px; background: rgba(241,245,249,0.78); border: 1px solid rgba(148,163,184,0.25); }
    @media (max-width: 760px) {
        .block-container { padding-left: 0.55rem; padding-right: 0.55rem; }
        .cfds-hero { border-radius: 16px; padding: 0.85rem; }
        .cfds-hero p { font-size: 0.88rem; }
        div[data-testid="stImage"] img { border-radius: 10px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in {".", "_", "-", " ", "(", ")"}:
            keep.append(ch)
        else:
            keep.append("_")
    clean = "".join(keep).strip()
    return clean or "uploaded_log.csv"


def make_zip_bytes(folder: Path) -> bytes:
    """Fast ZIP creation. compresslevel=1 is much quicker on small cloud CPUs."""
    return make_filtered_zip_bytes(folder, "CFDS_graph_exports.zip", include_suffixes=None)


def make_filtered_zip_bytes(folder: Path, archive_name: str, include_suffixes: set[str] | None = None) -> bytes:
    """Create an export ZIP from a folder, optionally filtered by file suffix."""
    archive_path = folder.parent / archive_name
    if archive_path.exists():
        archive_path.unlink()
    try:
        zf_ctx = zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1)
    except TypeError:
        zf_ctx = zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED)
    with zf_ctx as zf:
        for file in sorted(folder.rglob("*")):
            if not file.is_file():
                continue
            if include_suffixes is not None and file.suffix.lower() not in include_suffixes:
                continue
            # Mobile thumbnail cache should never go into official exports.
            if "_mobile_previews" in file.parts:
                continue
            zf.write(file, file.relative_to(folder))
    return archive_path.read_bytes()


def collect_export_payload(output_dir: Path, code: int, logs: str) -> dict:
    """Store export data in session_state so buttons keep working after Streamlit reruns."""
    png_files = sorted(output_dir.rglob("*.png"), key=_score_preview)
    report_md = output_dir / "00_diagnostics" / "flight_report.md"
    normalized_csv = output_dir / "00_diagnostics" / "normalized_log.csv"

    individual_pngs = []
    for png in png_files[:80]:
        try:
            individual_pngs.append((str(png.relative_to(output_dir)), png.read_bytes()))
        except Exception:
            pass

    payload = {
        "status_code": code,
        "logs": logs[-20000:],
        "png_count": len(png_files),
        "full_zip": make_zip_bytes(output_dir),
        "png_zip": make_filtered_zip_bytes(output_dir, "CFDS_png_only_export.zip", {".png"}),
        "diagnostics_zip": make_filtered_zip_bytes(output_dir, "CFDS_diagnostics_export.zip", {".json", ".csv", ".md", ".txt"}),
        "individual_pngs": individual_pngs,
        "folder_zips": make_folder_zips(output_dir),
        "report_text": report_md.read_text(encoding="utf-8", errors="replace") if report_md.exists() else "",
        "normalized_csv": normalized_csv.read_bytes() if normalized_csv.exists() else b"",
    }
    return payload


def _download_kwargs(key: str) -> dict:
    """Keep download buttons from rerunning the whole Streamlit page.

    Streamlit's default download_button behavior is to rerun the app after a
    click. On iPhone this makes expanders/folder sections look like they
    suddenly disappeared. on_click="ignore" keeps the UI stable.
    """
    return {"key": key, "on_click": "ignore"}




def _safe_radio_choice(label: str, options: list[str], key: str, index: int = 0, help_text: str | None = None) -> str | None:
    """iPhone-safe selector.

    Streamlit selectbox is searchable and on iPhone it can leave typed filter text
    in the field with a red/invalid border. Radio buttons are less compact but
    they are tap-only, so the value cannot desync from the visible label.
    """
    if not options:
        return None
    index = max(0, min(index, len(options) - 1))
    return st.radio(
        label,
        options,
        index=index,
        key=key,
        horizontal=False,
        help=help_text,
    )

def show_export_center(payload: dict) -> None:
    st.subheader("Export center")
    st.caption("Use these buttons directly in the web app. On iPhone, downloaded files go to Files/Downloads.")

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇️ Export full CFDS ZIP",
            data=payload.get("full_zip", b""),
            file_name="CFDS_graph_exports.zip",
            mime="application/zip",
            use_container_width=True,
            disabled=not payload.get("full_zip"),
            **_download_kwargs("dl_full_cfds_zip"),
        )
        st.download_button(
            "🖼️ Export PNG graphs only",
            data=payload.get("png_zip", b""),
            file_name="CFDS_png_only_export.zip",
            mime="application/zip",
            use_container_width=True,
            disabled=not payload.get("png_zip"),
            **_download_kwargs("dl_png_graphs_only"),
        )
    with c2:
        st.download_button(
            "🧪 Export diagnostics",
            data=payload.get("diagnostics_zip", b""),
            file_name="CFDS_diagnostics_export.zip",
            mime="application/zip",
            use_container_width=True,
            disabled=not payload.get("diagnostics_zip"),
            **_download_kwargs("dl_diagnostics_zip"),
        )
        st.download_button(
            "📄 Export flight report",
            data=payload.get("report_text", ""),
            file_name="flight_report.md",
            mime="text/markdown",
            use_container_width=True,
            disabled=not payload.get("report_text"),
            **_download_kwargs("dl_flight_report_md"),
        )

    if payload.get("normalized_csv"):
        st.download_button(
            "📊 Export normalized CSV",
            data=payload["normalized_csv"],
            file_name="normalized_log.csv",
            mime="text/csv",
            use_container_width=True,
            **_download_kwargs("dl_normalized_csv"),
        )

    folder_zips = payload.get("folder_zips", {})
    individual_pngs = payload.get("individual_pngs", [])

    # Export browser: one folder selector controls both folder ZIP and PNG list.
    # This is more stable on iPhone than two independent widgets, and it prevents
    # the PNG selector from showing stale files after the selected folder changes.
    if folder_zips or individual_pngs:
        st.markdown("### Graph export browser")
        st.caption("Choose one graph folder, then download that folder ZIP or one PNG from that same folder.")

        png_by_folder: dict[str, list[tuple[str, bytes]]] = {}
        for rel_name, data in individual_pngs:
            folder_name = str(Path(rel_name).parent).replace("\\", "/")
            if folder_name in ["", "."]:
                folder_name = "root"
            png_by_folder.setdefault(folder_name, []).append((rel_name, data))

        folder_names = sorted(set(folder_zips.keys()) | set(png_by_folder.keys()))
        if folder_names:
            previous = st.session_state.get("export_graph_folder", folder_names[0])
            if previous not in folder_names:
                previous = folder_names[0]
            selected_folder = _safe_radio_choice(
                "Choose graph folder",
                folder_names,
                key="export_graph_folder_radio",
                index=folder_names.index(previous),
                help_text="Tap one folder. This avoids the searchable selectbox state bug on iPhone.",
            )
            st.session_state["export_graph_folder"] = selected_folder

            if selected_folder in folder_zips:
                folder_file_name = "CFDS_" + selected_folder.replace("/", "_").replace("\\", "_") + "_png.zip"
                st.download_button(
                    f"⬇️ Download selected folder ZIP: {selected_folder}",
                    data=folder_zips[selected_folder],
                    file_name=folder_file_name,
                    mime="application/zip",
                    use_container_width=True,
                    **_download_kwargs(f"dl_folder_zip_{hashlib.sha1(selected_folder.encode('utf-8')).hexdigest()[:10]}"),
                )
            else:
                st.info("This folder has PNG files, but no folder ZIP was packed for it.")

            folder_pngs = sorted(png_by_folder.get(selected_folder, []), key=lambda item: item[0])
            st.markdown("### Individual PNG download")
            if not folder_pngs:
                st.info("No individual PNG found in this folder.")
            elif len(folder_pngs) == 1:
                rel_name, data = folder_pngs[0]
                st.caption(f"1 PNG available in {selected_folder}")
                st.download_button(
                    f"⬇️ Download PNG: {Path(rel_name).name}",
                    data=data,
                    file_name=Path(rel_name).name,
                    mime="image/png",
                    use_container_width=True,
                    **_download_kwargs(f"dl_png_single_{hashlib.sha1(rel_name.encode('utf-8')).hexdigest()[:10]}"),
                )
            else:
                png_labels = [rel_name for rel_name, _ in folder_pngs]
                png_key = "export_individual_png_selector_" + hashlib.sha1(selected_folder.encode("utf-8")).hexdigest()[:10]
                selected_png = _safe_radio_choice(
                    f"Choose PNG ({len(folder_pngs)} in this folder)",
                    png_labels,
                    index=0,
                    key=png_key + "_radio",
                    help_text="Tap one PNG from the selected folder.",
                )
                png_lookup = dict(folder_pngs)
                st.download_button(
                    f"⬇️ Download PNG: {Path(selected_png).name}",
                    data=png_lookup[selected_png],
                    file_name=Path(selected_png).name,
                    mime="image/png",
                    use_container_width=True,
                    **_download_kwargs(f"dl_png_{hashlib.sha1(selected_png.encode('utf-8')).hexdigest()[:10]}"),
                )


def run_worker(input_path: Path, output_dir: Path, mode: str, speed: str, families: list[str]) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("run_worker.py")),
        "--csv",
        str(input_path),
        "--out",
        str(output_dir),
        "--mode",
        mode,
        "--speed",
        speed,
        "--families",
        ",".join(families),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(Path(__file__).parent),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout


def show_report(output_dir: Path) -> None:
    report_md = output_dir / "00_diagnostics" / "flight_report.md"
    if report_md.exists():
        with st.expander("Flight report", expanded=False):
            st.markdown(report_md.read_text(encoding="utf-8", errors="replace"))

    manifest = output_dir / "studio_manifest.json"
    if manifest.exists():
        with st.expander("Studio manifest / diagnostics", expanded=False):
            st.code(manifest.read_text(encoding="utf-8", errors="replace"), language="json")


def _thumbnail_path(src: Path, cache_dir: Path, max_width: int = 900) -> Path:
    """Create a lightweight mobile preview image without changing export files."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{src.stem}_preview.jpg"
    if out.exists():
        return out
    try:
        from PIL import Image
        img = Image.open(src)
        img.thumbnail((max_width, max_width * 3))
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
        img.save(out, "JPEG", quality=72, optimize=True)
        return out
    except Exception:
        return src


def _score_preview(png: Path) -> tuple[int, str]:
    name = png.name.lower()
    rel = str(png).lower()
    # Put the most useful checks first on mobile.
    if "altitude" in name or "01_alt" in rel:
        return (0, name)
    if "velocity" in name or "02_vel" in rel:
        return (1, name)
    if "conops" in name or "06_conops" in rel:
        return (2, name)
    if "voltage" in name or "temperature" in name or "03_vt" in rel:
        return (3, name)
    if "gps" in name or "04_gps" in rel:
        return (4, name)
    return (5, name)


def _folder_label(folder: Path, output_dir: Path, count: int) -> str:
    rel = folder.relative_to(output_dir)
    name = "All folders" if str(rel) == "." else str(rel)
    return f"{name}  ({count})"


def show_previews(output_dir: Path, max_images: int, show_full_png: bool, show_all_folders: bool) -> None:
    png_files = sorted(output_dir.rglob("*.png"), key=_score_preview)
    if not png_files:
        st.warning("No PNG previews were generated. Check diagnostics or error log in the ZIP.")
        return

    thumb_dir = output_dir / "00_diagnostics" / "_mobile_previews"

    folders = sorted({p.parent for p in png_files}, key=lambda x: str(x.relative_to(output_dir)))
    folder_counts = {folder: sum(1 for p in png_files if p.parent == folder) for folder in folders}

    st.subheader("Preview browser")
    st.caption(
        f"Generated {len(png_files)} PNG files. Choose a folder to browse, or use Fast preview for the first {max_images}. "
        "Phone previews are compressed; ZIP exports keep the selected quality profile."
    )

    tab_fast, tab_folder = st.tabs(["⚡ Fast preview", "📁 Folder browser"])

    with tab_fast:
        visible = png_files[:max_images]
        st.caption(f"Showing {len(visible)} of {len(png_files)} PNG files.")
        for png in visible:
            preview_img = png if show_full_png else _thumbnail_path(png, thumb_dir)
            st.image(str(preview_img), caption=str(png.relative_to(output_dir)), use_container_width=True)

    with tab_folder:
        labels = [_folder_label(folder, output_dir, folder_counts[folder]) for folder in folders]
        selected_label = _safe_radio_choice(
            "Choose graph folder",
            labels,
            index=0,
            key="preview_graph_folder_radio",
            help_text="Tap-only folder picker for iPhone stability.",
        )
        selected_folder = folders[labels.index(selected_label)]
        folder_files = sorted([p for p in png_files if p.parent == selected_folder], key=lambda p: p.name)

        # Streamlit sliders require min_value < max_value.
        # Some selected folders contain only one image, so render that image directly
        # instead of creating a 1..1 slider that crashes the app.
        if len(folder_files) <= 1:
            folder_limit = len(folder_files)
            st.caption(
                f"Showing {folder_limit} of {len(folder_files)} image in {selected_folder.relative_to(output_dir)}"
            )
        else:
            default_limit = min(len(folder_files), 12)
            safe_folder_key = hashlib.sha1(str(selected_folder.relative_to(output_dir)).encode("utf-8")).hexdigest()[:10]
            folder_limit = st.slider(
                "Images from this folder",
                min_value=1,
                max_value=len(folder_files),
                value=default_limit,
                step=1,
                key=f"folder_preview_limit_{safe_folder_key}",
            )
            st.caption(
                f"Showing {min(folder_limit, len(folder_files))} of {len(folder_files)} images in {selected_folder.relative_to(output_dir)}"
            )

        for png in folder_files[:folder_limit]:
            preview_img = png if show_full_png else _thumbnail_path(png, thumb_dir)
            st.image(str(preview_img), caption=png.name, use_container_width=True)

    if show_all_folders:
        with st.expander("Folder summary", expanded=False):
            for folder in folders:
                st.markdown(f"**{folder.relative_to(output_dir)}** — {folder_counts[folder]} images")




def _score_preview_name(rel_name: str) -> tuple[int, str]:
    name = Path(rel_name).name.lower()
    rel = rel_name.lower()
    if "altitude" in name or "01_alt" in rel:
        return (0, name)
    if "velocity" in name or "02_vel" in rel:
        return (1, name)
    if "conops" in name or "06_conops" in rel:
        return (2, name)
    if "voltage" in name or "temperature" in name or "03_vt" in rel:
        return (3, name)
    if "gps" in name or "04_gps" in rel:
        return (4, name)
    return (5, name)


def _preview_image_bytes(png_bytes: bytes, show_full_png: bool, max_width: int = 900) -> bytes:
    """Return stable in-memory bytes for Streamlit image rendering.

    Important: Streamlit reruns the script after widget changes. The graph files are
    generated in a TemporaryDirectory that disappears after the generation run, so
    preview images must come from session-state bytes, not from local temp paths.
    """
    if show_full_png:
        return png_bytes
    try:
        from PIL import Image
        img = Image.open(BytesIO(png_bytes))
        img.thumbnail((max_width, max_width * 3))
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
        out = BytesIO()
        img.save(out, "JPEG", quality=72, optimize=True)
        return out.getvalue()
    except Exception:
        return png_bytes


def show_previews_from_payload(payload: dict, max_images: int, show_full_png: bool, show_all_folders: bool) -> None:
    """Render preview images from cached bytes so folder switching works after reruns."""
    png_items = payload.get("individual_pngs", []) or []
    if not png_items:
        st.warning("No PNG previews are available in the cached export. Try generating graphs again.")
        return

    png_items = sorted(png_items, key=lambda item: _score_preview_name(item[0]))
    by_folder: dict[str, list[tuple[str, bytes]]] = {}
    for rel_name, data in png_items:
        folder_name = str(Path(rel_name).parent).replace("\\", "/")
        if folder_name in ["", "."]:
            folder_name = "root"
        by_folder.setdefault(folder_name, []).append((rel_name, data))

    st.subheader("Preview browser")
    st.caption(
        f"Generated {len(png_items)} PNG files. These previews are rendered from session memory, "
        "so they remain visible after changing folders on iPhone."
    )

    tab_fast, tab_folder = st.tabs(["⚡ Fast preview", "📁 Folder browser"])

    with tab_fast:
        visible = png_items[:max_images]
        st.caption(f"Showing {len(visible)} of {len(png_items)} PNG files.")
        for rel_name, data in visible:
            st.image(BytesIO(_preview_image_bytes(data, show_full_png)), caption=rel_name, use_container_width=True)

    with tab_folder:
        folders = sorted(by_folder.keys())
        folder_labels = [f"{folder}  ({len(by_folder[folder])})" for folder in folders]
        current_folder = st.session_state.get("preview_selected_folder", folders[0])
        if current_folder not in folders:
            current_folder = folders[0]
        selected_label = _safe_radio_choice(
            "Choose graph folder",
            folder_labels,
            index=folders.index(current_folder),
            key="preview_graph_folder_radio_memory",
            help_text="Tap-only folder picker. Images are loaded from cached bytes, not deleted temp files.",
        )
        selected_folder = folders[folder_labels.index(selected_label)]
        st.session_state["preview_selected_folder"] = selected_folder

        folder_files = sorted(by_folder[selected_folder], key=lambda item: item[0])
        if len(folder_files) <= 1:
            folder_limit = len(folder_files)
        else:
            safe_key = hashlib.sha1(selected_folder.encode("utf-8")).hexdigest()[:10]
            default_limit = min(len(folder_files), 12)
            folder_limit = st.slider(
                "Images from this folder",
                min_value=1,
                max_value=len(folder_files),
                value=default_limit,
                step=1,
                key=f"folder_preview_limit_memory_{safe_key}",
            )
        st.caption(f"Showing {folder_limit} of {len(folder_files)} images in {selected_folder}")
        for rel_name, data in folder_files[:folder_limit]:
            st.image(BytesIO(_preview_image_bytes(data, show_full_png)), caption=Path(rel_name).name, use_container_width=True)

    if show_all_folders:
        with st.expander("Folder summary", expanded=False):
            for folder in sorted(by_folder.keys()):
                st.markdown(f"**{folder}** — {len(by_folder[folder])} images")


def make_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def make_cache_key(file_hash: str, selected: list[str], speed: str) -> str:
    return json.dumps({"file": file_hash, "families": sorted(selected), "speed": speed}, sort_keys=True)


def make_folder_zips(output_dir: Path) -> dict[str, bytes]:
    png_files = sorted(output_dir.rglob("*.png"))
    folders = sorted({p.parent for p in png_files}, key=lambda x: str(x.relative_to(output_dir)))
    result = {}
    for folder in folders:
        rel = str(folder.relative_to(output_dir))
        archive_name = "CFDS_" + rel.replace("/", "_").replace("\\", "_") + "_png.zip"
        result[rel] = make_filtered_zip_bytes(folder, archive_name, {".png"})
    return result


def quick_data_diagnostics(input_path: Path) -> dict:
    try:
        import pandas as pd
        suffix = input_path.suffix.lower()
        if suffix in [".xlsx", ".xlsm", ".xls"]:
            df = pd.read_excel(input_path, nrows=8)
        else:
            df = pd.read_csv(input_path, nrows=8)
        cols = [str(c) for c in df.columns]
        upper = {c.upper().replace(" ", "_") for c in cols}
        def has_any(names):
            return any(n in upper for n in names)
        return {
            "ok": True,
            "columns": cols[:18],
            "has_packet": has_any(["PACKET_COUNT", "PACKET", "PKT"]),
            "has_altitude": has_any(["ALTITUDE", "ALT", "ALTITUDE_M"]),
            "has_state": has_any(["STATE", "FLIGHT_STATE", "MODE"]),
            "has_gps": has_any(["GPS_LAT", "LAT", "LATITUDE"]) and has_any(["GPS_LON", "LON", "LONGITUDE"]),
            "has_motion": has_any(["ACCEL_R", "ACCEL_X", "GYRO_R", "GYRO_X", "TILT_ROLL_DERIVED", "ROLL"]),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def render_data_diagnostics(diag: dict) -> None:
    with st.expander("Pre-flight data check", expanded=False):
        if not diag.get("ok"):
            st.warning(f"Could not read a quick preview of the input file: {diag.get('error')}")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("Altitude column", "OK" if diag.get("has_altitude") else "Check")
        c2.metric("State column", "OK" if diag.get("has_state") else "Can infer")
        c3.metric("GPS columns", "OK" if diag.get("has_gps") else "Missing/optional")
        c4, c5 = st.columns(2)
        c4.metric("Packet/timebase", "OK" if diag.get("has_packet") else "Index fallback")
        c5.metric("Motion columns", "OK" if diag.get("has_motion") else "Missing/optional")
        st.caption("Detected columns: " + ", ".join(diag.get("columns", [])))


def render_family_selector(preset_name: str) -> list[str]:
    default = PRESETS.get(preset_name, PRESETS["Quick Check"])
    selected = []
    st.markdown("**Graph families**")
    cols = st.columns(2)
    for i, (key, meta) in enumerate(GRAPH_FAMILIES.items()):
        with cols[i % 2]:
            checked = st.checkbox(
                meta["label"],
                value=(key in default),
                key=f"family_{preset_name}_{key}",
                help=meta["hint"],
                disabled=(preset_name != "Custom"),
            )
            if checked:
                selected.append(key)
    return selected

if "cfds_last_export" not in st.session_state:
    st.session_state["cfds_last_export"] = None
if "cfds_export_cache" not in st.session_state:
    st.session_state["cfds_export_cache"] = {}


st.markdown(
    """
    <div class="cfds-hero">
      <h1>🚀 CanSat Flight Data Studio Web</h1>
      <p>Mobile-optimized version: upload a flight log, generate key previews quickly, then download the full export ZIP.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("CFDS Mobile Pro")
    mobile_fast = st.toggle("Mobile fast mode", value=True, help="Compressed previews, compact layout, and phone-first defaults.")
    preset_name = st.selectbox(
        "Graph preset",
        list(PRESETS.keys()),
        index=list(PRESETS.keys()).index("Quick Check"),
        help="Generate only the graph families you need. This is the biggest speed boost on iPhone.",
    )
    selected_families = render_family_selector(preset_name)
    mode_label = "Selected export"
    mode = "all"
    speed_label = st.radio(
        "Export quality",
        ["Mobile Fast", "Report Quality"],
        index=0,
        help="Mobile Fast skips SVG and uses lighter PNGs. Report Quality keeps 300 dpi PNG + SVG for final reports.",
    )
    speed = "fast" if speed_label == "Mobile Fast" else "quality"
    if mobile_fast:
        max_preview = st.slider("Max preview images", min_value=3, max_value=80, value=12, step=1)
        show_full_png = False
        show_all_folders = st.checkbox("Show folder summary", value=False)
    else:
        max_preview = st.slider("Max preview images", min_value=3, max_value=80, value=24, step=3)
        show_full_png = st.checkbox("Show original full PNG previews", value=False)
        show_all_folders = st.checkbox("Show folder summary", value=False)
    use_cached = st.checkbox("Use cached result if same log/settings", value=True, help="Skip regeneration if this exact log + selected pack was already generated in this session.")
    clear_cache_now = st.button("Clear session cache", use_container_width=True)
    if clear_cache_now:
        st.session_state["cfds_export_cache"] = {}
        st.session_state["cfds_last_export"] = None
        st.success("Cache cleared.")
    use_demo = st.checkbox("Use included demo log", value=False)
    show_logs_when_success = st.checkbox("Show worker logs after success", value=False)

st.markdown(
    """
    <div class="cfds-card">
    <b>Phone speed tip:</b> choose a preset first. Generating only Altitude + Velocity + CONOPS is much faster than building every graph family. Use Mobile Fast for field checks and Report Quality only for final files.
    </div>
    """,
    unsafe_allow_html=True,
)

uploaded = None
if not use_demo:
    uploaded = st.file_uploader("Upload flight log", type=SUPPORTED_TYPES)

start = st.button("Generate selected graphs", type="primary", use_container_width=True)

if start:
    with tempfile.TemporaryDirectory(prefix="cfds_web_") as tmp:
        work = Path(tmp)
        input_dir = work / "input"
        output_dir = work / "outputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        if use_demo:
            demo_path = Path(__file__).parent / "data" / "normalized_flight1043.csv"
            if not demo_path.exists():
                st.error("Demo file not found: data/normalized_flight1043.csv")
                st.stop()
            input_path = input_dir / demo_path.name
            shutil.copy2(demo_path, input_path)
        else:
            if uploaded is None:
                st.error("Upload a CSV/XLSX log first, or enable the demo log in the sidebar.")
                st.stop()
            input_path = input_dir / safe_filename(uploaded.name)
            input_path.write_bytes(uploaded.getbuffer())

        file_hash = make_file_hash(input_path)
        cache_key = make_cache_key(file_hash, selected_families, speed)
        render_data_diagnostics(quick_data_diagnostics(input_path))

        st.info(f"Input: {input_path.name} | Preset: {preset_name} | Families: {', '.join(selected_families) or 'diagnostics only'} | Quality: {speed_label}")

        if use_cached and cache_key in st.session_state["cfds_export_cache"]:
            st.success("Using cached result for this log/settings. No regeneration needed.")
            st.session_state["cfds_last_export"] = st.session_state["cfds_export_cache"][cache_key]
            # Rerun so the Export center renders outside this button-click block.
            # st.stop() would prevent cached downloads from appearing.
            st.rerun()

        progress = st.progress(0, text="Starting CFDS engine...")
        with st.spinner("Generating selected graphs inside the CFDS engine..."):
            progress.progress(20, text="Normalizing log and building diagnostics...")
            code, logs = run_worker(input_path, output_dir, mode, speed, selected_families)
            progress.progress(82, text="Packing exports and mobile previews...")

        if code != 0:
            st.error("CFDS generation failed. Open logs below, then download ZIP for diagnostics if available.")
        else:
            st.success("Graph generation completed.")

        show_report(output_dir)

        if code != 0 or show_logs_when_success:
            with st.expander("Worker logs", expanded=code != 0):
                st.code(logs[-20000:], language="text")

        st.session_state["cfds_last_export"] = collect_export_payload(output_dir, code, logs)
        st.session_state["cfds_export_cache"][cache_key] = st.session_state["cfds_last_export"]
        progress.progress(100, text="Done")

if st.session_state.get("cfds_last_export") is not None:
    show_previews_from_payload(st.session_state["cfds_last_export"], max_preview, show_full_png, show_all_folders)
    show_export_center(st.session_state["cfds_last_export"])

st.divider()
st.caption("CFDS Web keeps the original graph engine, but uses a mobile-optimized browser interface for iPhone/iPad/desktop.")
