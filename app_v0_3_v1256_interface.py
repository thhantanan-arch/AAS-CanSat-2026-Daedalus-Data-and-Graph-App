from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from PyQt6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "data" / "normalized_flight1043.csv"
OUTPUT_ROOT = Path.home() / "CFDS_outputs"
EXPORT_ROOT = Path.home() / "CFDS_exports"
WORKER = ROOT / "run_worker.py"

SKIN = {
    "bg": "#050b12", "panel": "#071827", "panel2": "#0b2136", "card": "#0e2b45",
    "text": "#eaffff", "muted": "#99b4c9", "accent": "#38bdf8", "accent2": "#22c55e",
    "warn": "#f59e0b", "danger": "#ef4444", "input": "#0b2136", "border": "rgba(150,255,245,0.15)",
}


def qss(font_size: int = 13) -> str:
    p = SKIN
    return f"""
    * {{ font-family: "Segoe UI", "SF Pro Display", Arial; font-size: {font_size}px; }}
    QMainWindow {{ background: {p['bg']}; }}
    QWidget#CentralRoot, QSplitter, QStackedWidget {{ background: {p['bg']}; }}
    QWidget {{ color: {p['text']}; background: transparent; }}
    QLabel#Title {{ font-size: {font_size+16}px; font-weight: 900; letter-spacing: 1.5px; }}
    QLabel#SubTitle {{ color: {p['muted']}; font-size: {font_size-1}px; }}
    QLabel#SectionTitle {{ font-size: {font_size+8}px; font-weight: 900; letter-spacing: .8px; }}
    QLabel#CardTitle {{ font-weight: 900; color: {p['accent']}; }}
    QFrame#Sidebar, QFrame#Card, QFrame#Header {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 16px; }}
    QFrame#ReportSection {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 14px; }}
    QWidget#PageBody, QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget {{ background: {p['bg']}; }}
    QFrame#MetricCard {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 14px; }}
    QLabel#Avatar {{ background: {p['card']}; border: 2px solid {p['accent']}; border-radius: 33px; }}
    QPushButton {{ background: {p['panel2']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 12px; padding: 9px 14px; font-weight: 800; }}
    QPushButton:hover {{ background: {p['card']}; border: 1px solid {p['accent']}; }}
    QPushButton#Primary {{ background: {p['accent']}; color: #04111e; border: 0px; }}
    QPushButton#GreenPrimary {{ background: {p['accent2']}; color: #04111e; border: 0px; }}
    QPushButton#Danger {{ background: {p['danger']}; color: #ffffff; border: 0px; }}
    QPushButton#Nav {{ text-align: left; padding-left: 18px; min-height: 36px; }}
    QPushButton#Nav[active="true"] {{ background: {p['accent']}; color: #04111e; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{ background: {p['input']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 7px; selection-background-color: {p['accent']}; selection-color: #04111e; }}
    QListWidget {{ background: {p['input']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 6px; }}
    QListWidget::item {{ padding: 7px; border-radius: 8px; }}
    QListWidget::item:selected {{ background: {p['accent']}; color: #04111e; }}
    QProgressBar {{ border: 1px solid {p['border']}; border-radius: 8px; text-align: center; background: {p['panel']}; }}
    QProgressBar::chunk {{ border-radius: 8px; background: {p['accent']}; }}
    QScrollArea {{ border: 0px; background: {p['bg']}; }}
    QScrollArea#PageScroll, QScrollArea#PageScroll QWidget#qt_scrollarea_viewport {{ background: {p['bg']}; }}
    """


class Card(QtWidgets.QFrame):
    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("Card")
        self.v = QtWidgets.QVBoxLayout(self)
        self.v.setContentsMargins(16, 16, 16, 16)
        self.v.setSpacing(10)
        if title:
            lab = QtWidgets.QLabel(title)
            lab.setObjectName("CardTitle")
            self.v.addWidget(lab)


class MetricCard(QtWidgets.QFrame):
    def __init__(self, title: str, value: str = "—", sub: str = ""):
        super().__init__()
        self.setObjectName("MetricCard")
        l = QtWidgets.QVBoxLayout(self)
        l.setContentsMargins(14, 13, 14, 13)
        title_lab = QtWidgets.QLabel(title)
        title_lab.setObjectName("SubTitle")
        self.value = QtWidgets.QLabel(value)
        self.value.setObjectName("SectionTitle")
        self.sub = QtWidgets.QLabel(sub)
        self.sub.setObjectName("SubTitle")
        self.sub.setWordWrap(True)
        l.addWidget(title_lab)
        l.addWidget(self.value)
        l.addWidget(self.sub)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CanSat Flight Data Studio V12.56 Interface")
        self.resize(1440, 880)
        self.setMinimumSize(1180, 720)
        self.csv_path = str(DEFAULT_CSV) if DEFAULT_CSV.exists() else ""
        self.current_output = OUTPUT_ROOT / "last_run"
        self.process: QtCore.QProcess | None = None
        self.pages: dict[str, QtWidgets.QWidget] = {}
        self.nav_buttons: dict[str, QtWidgets.QPushButton] = {}
        self.preview_files: list[Path] = []
        self.current_pixmap = None
        OUTPUT_ROOT.mkdir(exist_ok=True)
        EXPORT_ROOT.mkdir(exist_ok=True)
        self.build_ui()
        self.goto("Game Start")

    def build_ui(self):
        self.setStyleSheet(qss())
        central = QtWidgets.QWidget()
        central.setObjectName("CentralRoot")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(self.header())

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)
        split.addWidget(self.sidebar())
        self.stack = QtWidgets.QStackedWidget()
        split.addWidget(self.stack)
        split.setSizes([270, 1150])

        for name, builder in [
            ("Game Start", self.page_game_start),
            ("Dashboard", self.page_dashboard),
            ("Setup", self.page_setup),
            ("Preview / Output", self.page_preview),
            ("Flight Report", self.page_report),
            ("Report Summary", self.page_report_summary),
            ("Report Rule Checks", self.page_report_checks),
            ("Report Regression", self.page_report_regression),
            ("Report Advanced Stats", self.page_report_advanced_stats),
            ("Report Definitions", self.page_report_definitions),
            ("Report Markdown", self.page_report_markdown),
            ("Export Center", self.page_export),
            ("System", self.page_system),
        ]:
            page = builder()
            self.pages[name] = page
            self.stack.addWidget(page)

    def header(self):
        h = QtWidgets.QFrame()
        h.setObjectName("Header")
        h.setMinimumHeight(88)
        hl = QtWidgets.QHBoxLayout(h)
        hl.setContentsMargins(18, 10, 18, 10)
        logo = QtWidgets.QLabel("AC")
        logo.setObjectName("Avatar")
        logo.setFixedSize(66, 66)
        logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("font-size:22px;font-weight:900;")
        hl.addWidget(logo)

        box = QtWidgets.QVBoxLayout()
        self.title = QtWidgets.QLabel("CanSat Flight Data Studio V12.56")
        self.title.setObjectName("Title")
        self.subtitle = QtWidgets.QLabel("Mission interface • Report in-page scroll • Competition graph formats protected")
        self.subtitle.setObjectName("SubTitle")
        box.addWidget(self.title)
        box.addWidget(self.subtitle)
        hl.addLayout(box, 1)

        for label, fn, obj in [
            ("Select Log", self.select_log, "Primary"),
            ("Update Graphs", self.update_graphs, "GreenPrimary"),
            ("Open Outputs", self.open_outputs, ""),
        ]:
            b = QtWidgets.QPushButton(label)
            b.setMinimumHeight(40)
            b.clicked.connect(fn)
            if obj:
                b.setObjectName(obj)
            hl.addWidget(b)
        return h

    def sidebar(self):
        side = QtWidgets.QFrame()
        side.setObjectName("Sidebar")
        side.setMinimumWidth(250)
        side.setMaximumWidth(310)
        sl = QtWidgets.QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 16)
        sl.setSpacing(8)
        brand = QtWidgets.QLabel("SPACE AC INSTITUTE")
        brand.setObjectName("CardTitle")
        sl.addWidget(brand)

        def add_nav_button(page, parent_layout):
            b = QtWidgets.QPushButton(page)
            b.setObjectName("Nav")
            b.setProperty("active", False)
            b.clicked.connect(lambda _, n=page: self.goto(n))
            parent_layout.addWidget(b)
            self.nav_buttons[page] = b
            return b

        # Keep sidebar simple. Report sub-choices are inside the Flight Report page and scroll there.
        for page in ["Game Start", "Dashboard", "Setup", "Preview / Output", "Flight Report", "Export Center", "System"]:
            add_nav_button(page, sl)

        sl.addSpacing(8)
        flow = Card("Mission Flow")
        for line in ["1  Select Log", "2  Update Graphs", "3  Flight Report", "4  Preview / Export"]:
            lab = QtWidgets.QLabel(line)
            lab.setObjectName("SubTitle")
            flow.v.addWidget(lab)
        sl.addWidget(flow)
        sl.addStretch(1)
        foot = QtWidgets.QLabel("Elfaria / Daedalus shell")
        foot.setObjectName("SubTitle")
        sl.addWidget(foot)
        return side


    def toggle_report_nav(self):
        # v0.5.7: no expanding report submenu in the sidebar.
        # All report details live inside the Flight Report scroll page.
        self.goto("Flight Report")

    def scroll_page(self, title: str):
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        body = QtWidgets.QWidget()
        body.setObjectName("PageBody")
        scroll.setWidget(body)
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(14)
        lab = QtWidgets.QLabel(title)
        lab.setObjectName("SectionTitle")
        v.addWidget(lab)
        return scroll, v

    def goto(self, name: str):
        if name not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[name])

        for page, button in self.nav_buttons.items():
            active = (page == name) or (page == "Flight Report" and name.startswith("Report "))
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self.subtitle.setText(f"{name} • Log: {Path(self.csv_path).name if self.csv_path else 'No log selected'}")

        if (name == "Flight Report" or name.startswith("Report ")) and hasattr(self, "refresh_report"):
            self.refresh_report()


    def page_game_start(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.addStretch(1)
        title = QtWidgets.QLabel("CanSat Flight Data Studio")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:46px;font-weight:900;color:#38d5ff;letter-spacing:2px;")
        layout.addWidget(title)
        sub = QtWidgets.QLabel("MISSION INTERFACE // SPACE AC INSTITUTE // GRAPH ENGINE")
        sub.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("font-size:16px;color:#99b4c9;letter-spacing:1px;")
        layout.addWidget(sub)
        status = QtWidgets.QLabel("SYSTEM READY  •  UPDATE CORE ONLINE  •  CONOPS V6 ACTIVE")
        status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        status.setStyleSheet("color:#22c55e;font-weight:900;margin-top:18px;")
        layout.addWidget(status)
        start = QtWidgets.QPushButton("LINK START")
        start.setObjectName("Primary")
        start.setMinimumHeight(56)
        start.setMaximumWidth(260)
        start.clicked.connect(lambda: self.goto("Dashboard"))
        layout.addWidget(start, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        hint = QtWidgets.QLabel("Press LINK START to enter Dashboard, or choose any module from the left navigation.")
        hint.setObjectName("SubTitle")
        hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def page_dashboard(self):
        scroll, v = self.scroll_page("Dashboard")
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)
        v.addLayout(grid)
        self.metric_log = MetricCard("Log Status", "Sample Ready" if DEFAULT_CSV.exists() else "No Log", "Selected Log input.")
        self.metric_engine = MetricCard("Engine State", "Protected", "UI calls worker; worker calls engine.")
        self.metric_out = MetricCard("Output Folder", "Ready", "Graphs are written per run.")
        self.metric_last = MetricCard("Last Run", "—", "No update run yet.")
        for i, card in enumerate([self.metric_log, self.metric_engine, self.metric_out, self.metric_last]):
            grid.addWidget(card, 0, i)
        actions = Card("Mission Actions")
        row = QtWidgets.QHBoxLayout()
        for label, fn, obj in [
            ("Select Log", self.select_log, "Primary"),
            ("Update Graphs", self.update_graphs, "GreenPrimary"),
            ("Preview Graphs", lambda: self.goto("Preview / Output"), ""),
            ("Flight Report", lambda: self.goto("Flight Report"), ""),
            ("Export ZIP", self.export_zip, ""),
        ]:
            b = QtWidgets.QPushButton(label)
            b.setMinimumHeight(48)
            b.clicked.connect(fn)
            if obj:
                b.setObjectName(obj)
            row.addWidget(b)
        actions.v.addLayout(row)
        v.addWidget(actions)
        body = QtWidgets.QHBoxLayout()
        v.addLayout(body, 1)
        events = Card("State Timeline / Key Events")
        self.event_text = QtWidgets.QTextEdit()
        self.event_text.setReadOnly(True)
        self.event_text.setPlainText("ASCENT → APOGEE → DESCENT → PAYLOAD_RELEASE → LANDED\n\nEvent boundaries stay inside the graph renderer.")
        events.v.addWidget(self.event_text, 1)
        body.addWidget(events, 1)
        out = Card("Output Folder Status")
        self.output_text = QtWidgets.QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setPlainText(str(self.current_output))
        out.v.addWidget(self.output_text, 1)
        body.addWidget(out, 1)
        check = Card("PFR Evidence Checklist")
        for line in ["Altitude plot updated", "Velocity rule bands updated", "CONOPS actual/planned updated", "GPS path available", "Export ZIP ready"]:
            cb = QtWidgets.QCheckBox(line)
            check.v.addWidget(cb)
        body.addWidget(check, 1)
        v.addStretch(1)
        return scroll

    def page_setup(self):
        scroll, v = self.scroll_page("Setup")
        row = QtWidgets.QHBoxLayout()
        v.addLayout(row)
        paths = Card("Paths")
        self.csv_edit = QtWidgets.QLineEdit(self.csv_path)
        paths.v.addWidget(QtWidgets.QLabel("Selected normalized Log"))
        paths.v.addWidget(self.csv_edit)
        browse = QtWidgets.QPushButton("Browse Log")
        browse.setObjectName("Primary")
        browse.clicked.connect(self.select_log)
        paths.v.addWidget(browse)
        self.out_edit = QtWidgets.QLineEdit(str(self.current_output))
        paths.v.addWidget(QtWidgets.QLabel("Output folder"))
        paths.v.addWidget(self.out_edit)
        out_browse = QtWidgets.QPushButton("Choose Output Folder")
        out_browse.clicked.connect(self.choose_output_folder)
        paths.v.addWidget(out_browse)
        row.addWidget(paths, 1)
        val = Card("Data Validation")
        self.validation = QtWidgets.QTextEdit()
        self.validation.setReadOnly(True)
        self.validation.setPlainText("Required columns: PACKET_COUNT, STATE, ALTITUDE, VOLTAGE, TEMPERATURE, GPS_LAT, GPS_LON, GPS_ALT, GYRO_R/P/Y, ACCEL_R/P/Y.\n\nFlow: Select Log → Update Graphs → Preview.")
        val.v.addWidget(self.validation, 1)
        row.addWidget(val, 1)
        return scroll

    def page_preview(self):
        scroll, v = self.scroll_page("Preview / Output")
        note = QtWidgets.QLabel("Output browser: current run only. Graphs are indexed by output family folder, so filters do not accidentally open the wrong graph.")
        note.setObjectName("SubTitle")
        note.setWordWrap(True)
        v.addWidget(note)
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        v.addWidget(split, 1)
        left = Card("Output Images")
        self.preview_filter = QtWidgets.QComboBox()
        self.preview_filter.addItems(["All PNG", "Altitude", "Velocity", "Voltage", "Temperature", "GPS", "Multi-axis", "CONOPS"])
        self.preview_filter.currentTextChanged.connect(lambda _: self.refresh_previews())
        self.preview_list = QtWidgets.QListWidget()
        self.preview_list.itemSelectionChanged.connect(self.show_selected_preview)
        left.v.addWidget(self.preview_filter)
        left.v.addWidget(self.preview_list, 1)
        for label, fn in [("Refresh", self.refresh_previews), ("Open Selected", self.open_selected), ("Open Output Folder", self.open_outputs)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            left.v.addWidget(b)
        split.addWidget(left)
        right = Card("Preview / Output Graph")
        self.preview_file_label = QtWidgets.QLabel("No image selected")
        self.preview_file_label.setObjectName("SubTitle")
        self.preview_file_label.setWordWrap(True)
        self.preview_label = QtWidgets.QLabel("No image selected")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(620, 430)
        self.preview_label.setStyleSheet("background:#020712;border:1px solid rgba(150,255,245,0.15);border-radius:12px;color:#99b4c9;")
        right.v.addWidget(self.preview_file_label)
        right.v.addWidget(self.preview_label, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        return scroll

    def page_report(self):
        scroll, v = self.scroll_page("Flight Report")
        note = QtWidgets.QLabel(
            "All report sections are inside this page now. Use the buttons below or scroll down; the sidebar stays clean."
        )
        note.setObjectName("SubTitle")
        note.setWordWrap(True)
        v.addWidget(note)

        top = QtWidgets.QHBoxLayout()
        v.addLayout(top)
        for label, fn, obj in [
            ("Refresh Report", self.refresh_report, "Primary"),
            ("Open Output Folder", self.open_outputs, ""),
            ("Export ZIP", self.export_zip, ""),
        ]:
            b = QtWidgets.QPushButton(label)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(fn)
            top.addWidget(b)

        self.report_cards = {}
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)
        v.addLayout(grid)
        for i, (key, title) in enumerate([
            ("apogee_altitude_m", "Apogee Altitude"),
            ("payload_release_percent_of_peak", "Release % Peak"),
            ("stage1_average_descent_rate_mps", "Stage 1 Rate"),
            ("stage2_average_descent_rate_mps", "Stage 2 Rate"),
        ]):
            card = MetricCard(title, "—", key)
            self.report_cards[key] = card
            grid.addWidget(card, 0, i)

        jump = Card("Report Sections")
        jump_note = QtWidgets.QLabel("Sections are stacked below in one scroll page: Summary → Rule Checks → Regression → Advanced Stats → Definitions → Markdown.")
        jump_note.setWordWrap(True)
        jump_note.setObjectName("SubTitle")
        jump.v.addWidget(jump_note)
        v.addWidget(jump)

        def add_report_section(title: str, attr_name: str, subtitle: str, min_h: int = 220):
            section = QtWidgets.QFrame()
            section.setObjectName("ReportSection")
            layout = QtWidgets.QVBoxLayout(section)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(8)

            t = QtWidgets.QLabel(title)
            t.setObjectName("CardTitle")
            layout.addWidget(t)

            sub = QtWidgets.QLabel(subtitle)
            sub.setObjectName("SubTitle")
            sub.setWordWrap(True)
            layout.addWidget(sub)

            box = QtWidgets.QTextEdit()
            box.setReadOnly(True)
            box.setMinimumHeight(min_h)
            box.setPlainText("Run Update Graphs, then press Refresh Report.")
            setattr(self, attr_name, box)
            layout.addWidget(box, 1)

            v.addWidget(section)
            return box

        add_report_section(
            "Report Summary",
            "report_summary",
            "Mission-level summary: source log, apogee, payload release, instrument release, landing, sample rate, and key mission values.",
            230
        )
        add_report_section(
            "Report Rule Checks",
            "report_checks",
            "Competition requirement checks with measured values, target bands, method, and status.",
            260
        )
        add_report_section(
            "Report Regression",
            "report_regression",
            "Regression details: full/stable windows, descent rate, slope, intercept, R², samples, and window boundaries.",
            260
        )
        add_report_section(
            "Report Advanced Stats",
            "report_stats",
            "Advanced statistics for every numeric telemetry column: mean, median, std, IQR, min/max, trend slope, and trend R².",
            320
        )
        add_report_section(
            "Report Definitions",
            "report_definitions",
            "Definitions of each metric/statistic used in the report.",
            220
        )
        add_report_section(
            "Report Markdown",
            "report_text",
            "Full markdown report generated into 00_diagnostics/flight_report.md.",
            360
        )
        return scroll

    def _report_text_page(self, title: str, attr_name: str, subtitle: str):
        scroll, v = self.scroll_page(title)
        note = QtWidgets.QLabel(subtitle)
        note.setObjectName("SubTitle")
        note.setWordWrap(True)
        v.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        v.addLayout(row)
        for label, fn, obj in [
            ("Refresh Report", self.refresh_report, "Primary"),
            ("Back to Flight Report", lambda: self.goto("Flight Report"), ""),
            ("Open Output Folder", self.open_outputs, ""),
        ]:
            b = QtWidgets.QPushButton(label)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(fn)
            row.addWidget(b)
        box = QtWidgets.QTextEdit()
        box.setReadOnly(True)
        box.setPlainText("Run Update Graphs, then press Refresh Report.")
        setattr(self, attr_name, box)
        v.addWidget(box, 1)
        return scroll

    def page_report_summary(self):
        return self._report_text_page(
            "Report Summary",
            "report_summary",
            "Mission-level summary: source log, apogee, payload release, instrument release, landing, sample rate, and key mission values."
        )

    def page_report_checks(self):
        return self._report_text_page(
            "Report Rule Checks",
            "report_checks",
            "Competition requirement checks with measured values, target bands, method, and status."
        )

    def page_report_regression(self):
        return self._report_text_page(
            "Report Regression",
            "report_regression",
            "Regression details: full/stable windows, descent rate, slope, intercept, R², samples, and window boundaries."
        )

    def page_report_advanced_stats(self):
        return self._report_text_page(
            "Report Advanced Stats",
            "report_stats",
            "Advanced statistics for every numeric telemetry column: mean, median, std, IQR, min/max, trend slope, and trend R²."
        )

    def page_report_definitions(self):
        return self._report_text_page(
            "Report Definitions",
            "report_definitions",
            "Definitions of each metric/statistic used in the report."
        )

    def page_report_markdown(self):
        return self._report_text_page(
            "Report Markdown",
            "report_text",
            "Full markdown report generated into 00_diagnostics/flight_report.md."
        )

    def page_export(self):
        scroll, v = self.scroll_page("Export Center")
        fmt = Card("Export Format / DPI")
        hint = QtWidgets.QLabel("Graph update uses locked renderer settings. Export here only packages existing outputs.")
        hint.setObjectName("SubTitle")
        hint.setWordWrap(True)
        fmt.v.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        for label, fn, obj in [
            ("Open Outputs", self.open_outputs, ""),
            ("Export Current Run ZIP", self.export_zip, "GreenPrimary"),
            ("Clear Outputs", self.clear_outputs, "Danger"),
            ("Refresh Info", self.refresh_export_info, ""),
        ]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            if obj:
                b.setObjectName(obj)
            row.addWidget(b)
        fmt.v.addLayout(row)
        v.addWidget(fmt)
        info = Card("Export Log")
        self.export_log = QtWidgets.QTextEdit()
        self.export_log.setReadOnly(True)
        info.v.addWidget(self.export_log, 1)
        v.addWidget(info, 1)
        self.refresh_export_info()
        return scroll

    def page_system(self):
        scroll, v = self.scroll_page("System")
        arch = Card("Extracted V12.56 Interface Pattern")
        txt = QtWidgets.QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText("Extracted from V12.56: QMainWindow header, left navigation sidebar, QSplitter layout, QStackedWidget page router, Card/MetricCard language, Game Start LINK START page, Dashboard/Setup/Preview/Export workflow, Daedalus-style QSS theme.\n\nThis v0.3 keeps that interface structure but replaces the old graph backend with the frozen generator worker.")
        arch.v.addWidget(txt, 1)
        v.addWidget(arch)
        sysc = Card("Process Isolation")
        lab = QtWidgets.QLabel("UI → run_worker.py → generate_all_cansat_graphs.py → outputs/")
        lab.setObjectName("SectionTitle")
        sysc.v.addWidget(lab)
        v.addWidget(sysc)
        v.addStretch(1)
        return scroll

    def select_log(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select CanSat log file", str(ROOT), "Log Files (*.csv *.xlsx *.xlsm *.xls);;Log Files (*.csv);;Excel Files (*.xlsx *.xlsm *.xls);;All Files (*)")
        if path:
            self.csv_path = path
            if hasattr(self, "csv_edit"):
                self.csv_edit.setText(path)
            self.metric_log.value.setText("Log Selected")
            self.metric_log.sub.setText(Path(path).name)
            self.subtitle.setText(f"Selected log: {Path(path).name}")

    def choose_output_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder", str(OUTPUT_ROOT))
        if path:
            self.current_output = Path(path)
            self.out_edit.setText(path)

    def new_output_dir(self, mode="all"):
        # Keep output paths short on Windows. Long nested paths can appear as FileNotFoundError.
        log_name = Path(self.csv_path).stem if self.csv_path else "log"
        safe_log = re.sub(r"[^A-Za-z0-9]+", "_", log_name).strip("_") or "log"
        safe_log = safe_log[:28]
        run_name = f"{mode}_{datetime.now().strftime('%m%d_%H%M%S')}"
        out = OUTPUT_ROOT / safe_log / run_name
        out.mkdir(parents=True, exist_ok=True)
        self.current_output = out
        if hasattr(self, "out_edit"):
            self.out_edit.setText(str(out))
        return out

    def update_graphs(self):
        self.start_worker("all")

    def start_worker(self, mode):
        csv = Path(self.csv_path)
        if not csv.exists():
            QtWidgets.QMessageBox.warning(self, "Log missing", "Select a valid Log log first.")
            return
        if self.process and self.process.state() != QtCore.QProcess.ProcessState.NotRunning:
            QtWidgets.QMessageBox.warning(self, "Worker running", "A graph update is already running.")
            return
        out = self.new_output_dir(mode)
        self.process = QtCore.QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setProgram(sys.executable)
        self.process.setArguments([str(WORKER), "--csv", str(csv), "--out", str(out), "--mode", mode])
        self.process.readyReadStandardOutput.connect(self.worker_stdout)
        self.process.readyReadStandardError.connect(self.worker_stdout)
        self.process.finished.connect(self.worker_finished)
        self.metric_engine.value.setText("Running")
        self.metric_last.value.setText("Updating")
        self.output_text.setPlainText(str(out))
        self.process.start()
        self.goto("Dashboard")

    def worker_stdout(self):
        if not self.process:
            return
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        data += bytes(self.process.readAllStandardError()).decode(errors="replace")
        if data and hasattr(self, "output_text"):
            self.output_text.append(data.rstrip())

    def worker_finished(self, code, status):
        self.metric_engine.value.setText("Ready")
        self.metric_last.value.setText("Done" if code == 0 else "Error")
        self.refresh_previews()
        self.refresh_export_info()
        if hasattr(self, "refresh_report"):
            self.refresh_report()
        self.subtitle.setText("Graph update finished. Preview outputs are ready." if code == 0 else "Graph update failed. Check output error_log.txt.")


    def graph_family_for_file(self, p: Path) -> str:
        """Classify preview image by output folder first, filename second."""
        parts = {x.lower() for x in p.parts}
        name = p.name.lower()
        if "01_alt" in parts or "01_altitude" in parts:
            return "Altitude"
        if "02_vel" in parts or "02_velocity" in parts:
            return "Velocity"
        if "03_vt" in parts or "03_voltage_temperature" in parts:
            if name.startswith("voltage"):
                return "Voltage"
            if name.startswith("temperature"):
                return "Temperature"
            return "Voltage/Temperature"
        if "04_gps" in parts:
            return "GPS"
        if "05_multi" in parts or "05_multi_axis" in parts:
            return "Multi-axis"
        if "06_conops" in parts:
            return "CONOPS"

        # fallback for older output folders
        if name.startswith("altitude_"):
            return "Altitude"
        if name.startswith("velocity_"):
            return "Velocity"
        if name.startswith("voltage_"):
            return "Voltage"
        if name.startswith("temperature_"):
            return "Temperature"
        if name.startswith("gps_"):
            return "GPS"
        if name.startswith(("acceleration_", "gyro_", "angular_velocity_", "tilt_", "servo_")):
            return "Multi-axis"
        if name.startswith("conops_"):
            return "CONOPS"
        return "Other"

    def preview_display_name(self, p: Path) -> str:
        family = self.graph_family_for_file(p)
        try:
            rel = p.relative_to(Path(self.current_output))
        except Exception:
            try:
                rel = p.relative_to(OUTPUT_ROOT)
            except Exception:
                rel = p.name
        return f"[{family}] {rel}"

    def preview_sort_key(self, p: Path):
        family_order = {
            "Altitude": 0,
            "Velocity": 1,
            "Voltage": 2,
            "Temperature": 3,
            "Voltage/Temperature": 3,
            "GPS": 4,
            "Multi-axis": 5,
            "CONOPS": 6,
            "Other": 99,
        }
        return (family_order.get(self.graph_family_for_file(p), 99), str(p).lower())


    def refresh_previews(self):
        if not hasattr(self, "preview_list"):
            return

        # Preview must show the CURRENT RUN first. Older runs are only a fallback.
        root = Path(self.current_output)
        files = []
        if root.exists():
            files = list(root.rglob("*.png"))

        # If current run has no images yet, show newest PNGs from all CFDS outputs as fallback.
        if not files and OUTPUT_ROOT.exists():
            files = sorted(OUTPUT_ROOT.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:300]

        # de-duplicate, ignore placeholders unless no real graph exists
        seen = set()
        unique = []
        for p in files:
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp not in seen and p.exists():
                seen.add(rp)
                unique.append(p)

        real = [p for p in unique if "placeholder" not in p.name.lower() and "unavailable" not in p.name.lower()]
        if real:
            unique = real

        filt = self.preview_filter.currentText() if hasattr(self, "preview_filter") else "All PNG"
        if filt != "All PNG":
            unique = [p for p in unique if self.graph_family_for_file(p) == filt]

        unique = sorted(unique, key=self.preview_sort_key)

        self.preview_files = unique
        self.preview_list.clear()
        for p in unique:
            self.preview_list.addItem(self.preview_display_name(p))

        self.preview_file_label.setText(f"{len(unique)} image(s) in current preview filter")
        if unique:
            self.preview_list.setCurrentRow(0)
        else:
            self.preview_label.setText("No matching graph images found for this run.")

    def show_selected_preview(self):
        items = self.preview_list.selectedIndexes()
        if not items:
            return
        idx = items[0].row()
        if idx >= len(self.preview_files):
            return
        p = self.preview_files[idx]
        self.preview_file_label.setText(str(p))
        pix = QtGui.QPixmap(str(p))
        if pix.isNull():
            self.preview_label.setText("Could not load image")
            return
        self.preview_label.setPixmap(pix.scaled(self.preview_label.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))

    def open_selected(self):
        items = self.preview_list.selectedIndexes() if hasattr(self, "preview_list") else []
        if not items:
            QtWidgets.QMessageBox.information(self, "No selection", "Select a graph first.")
            return
        idx = items[0].row()
        if idx < len(self.preview_files):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.preview_files[idx])))

    def open_outputs(self):
        self.current_output.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.current_output)))

    def export_zip(self):
        out = Path(self.current_output)
        files = [p for p in out.rglob("*") if p.is_file()] if out.exists() else []
        if not files:
            QtWidgets.QMessageBox.warning(self, "No outputs", "Update graphs first.")
            return
        EXPORT_ROOT.mkdir(exist_ok=True)
        zpath = EXPORT_ROOT / f"cansat_graph_outputs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, p.relative_to(out))
        self.refresh_export_info()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(EXPORT_ROOT)))

    def clear_outputs(self):
        if QtWidgets.QMessageBox.question(self, "Clear outputs", "Delete all generated outputs?") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if OUTPUT_ROOT.exists():
            for p in OUTPUT_ROOT.iterdir():
                if p.name == ".gitkeep":
                    continue
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        self.refresh_previews()
        self.refresh_export_info()
        if hasattr(self, "refresh_report"):
            self.refresh_report()

    def refresh_report(self):
        root = Path(self.current_output)
        diag = root / "00_diagnostics"
        report_json = diag / "flight_report.json"
        report_md = diag / "flight_report.md"

        if not report_json.exists():
            msg = f"No flight report for current output yet.\nCurrent output: {self.current_output}\nRun Update Graphs first."
            for attr in ["report_summary", "report_checks", "report_regression", "report_stats", "report_definitions", "report_text"]:
                if hasattr(self, attr):
                    getattr(self, attr).setPlainText(msg)
            return

        try:
            report = json.loads(report_json.read_text(encoding="utf-8"))
            summary = report.get("summary", {})
            checks = report.get("checks", {})
            regs = report.get("regression_details", {})
            stats = report.get("advanced_statistics", {})
            definitions = report.get("definitions", {})

            for key, card in getattr(self, "report_cards", {}).items():
                val = summary.get(key)
                if isinstance(val, (int, float)):
                    card.value.setText(f"{val:.2f}")
                elif val is None:
                    card.value.setText("—")
                else:
                    card.value.setText(str(val))

            def fmt(v, digits=3):
                if v is None:
                    return "—"
                if isinstance(v, (int, float)):
                    return f"{v:.{digits}f}"
                return str(v)

            s_lines = [
                f"Source log: {report.get('source_log', '—')}",
                f"Generated at: {report.get('generated_at', '—')}",
                f"Mode: {report.get('mode', '—')}",
                "",
                "MISSION EVENTS",
            ]
            for k in [
                "apogee_altitude_m", "apogee_time_s",
                "payload_release_altitude_m", "payload_release_time_s",
                "payload_release_percent_of_peak", "payload_release_error_percentage_points",
                "instrument_release_altitude_m", "instrument_release_time_s",
                "landing_altitude_m", "landing_time_s",
                "packet_count", "duration_s", "average_sample_rate_hz",
            ]:
                s_lines.append(f"{k}: {fmt(summary.get(k))}")
            self.report_summary.setPlainText("\n".join(s_lines))

            c_lines = []
            for req, item in checks.items():
                c_lines.append(f"{req}: {item.get('status', 'UNKNOWN')}")
                c_lines.append(f"  Rule: {item.get('rule', '—')}")
                if "method" in item:
                    c_lines.append(f"  Method: {item.get('method')}")
                for mk in ["measured_mps", "measured_percent_of_peak", "measured_altitude_m", "measured_time_s", "error_percentage_points"]:
                    if mk in item:
                        c_lines.append(f"  {mk}: {fmt(item.get(mk))}")
                c_lines.append(f"  Target: {item.get('target', '—')}")
                if "note" in item:
                    c_lines.append(f"  Note: {item.get('note')}")
                c_lines.append("")
            self.report_checks.setPlainText("\n".join(c_lines))

            r_lines = ["REGRESSION DETAILS", ""]
            for name, reg in regs.items():
                r_lines.append(name)
                for k in ["status", "descent_rate_mps", "slope_m_per_s", "intercept_m", "r2", "sample_count", "window_start_s", "window_end_s"]:
                    r_lines.append(f"  {k}: {fmt(reg.get(k))}")
                r_lines.append("")
            self.report_regression.setPlainText("\n".join(r_lines))

            st_lines = ["ADVANCED DATA STATISTICS", "Format: count | missing | mean | median | std | min | max | IQR | trend slope/s | trend R²", ""]
            for col, st in stats.items():
                st_lines.append(
                    f"{col}: count={st.get('count')} | missing={st.get('missing')} | "
                    f"mean={fmt(st.get('mean'))} | median={fmt(st.get('median'))} | std={fmt(st.get('std'))} | "
                    f"min={fmt(st.get('min'))} | max={fmt(st.get('max'))} | IQR={fmt(st.get('iqr'))} | "
                    f"slope/s={fmt(st.get('trend_slope_per_s'))} | R²={fmt(st.get('trend_r2'))}"
                )
            self.report_stats.setPlainText("\n".join(st_lines))

            d_lines = []
            for term, definition in definitions.items():
                d_lines.append(f"{term}\n  {definition}\n")
            self.report_definitions.setPlainText("\n".join(d_lines))

            self.report_text.setPlainText(report_md.read_text(encoding="utf-8") if report_md.exists() else json.dumps(report, indent=2))
        except Exception as e:
            msg = f"Could not load flight report:\n{e}\n\nPath: {report_json}"
            for attr in ["report_summary", "report_checks", "report_regression", "report_stats", "report_definitions", "report_text"]:
                if hasattr(self, attr):
                    getattr(self, attr).setPlainText(msg)

    def refresh_export_info(self):
        if not hasattr(self, "export_log"):
            return
        out = Path(self.current_output)
        png = len(list(out.rglob("*.png"))) if out.exists() else 0
        svg = len(list(out.rglob("*.svg"))) if out.exists() else 0
        manifest = out / "studio_manifest.json"
        text = f"Current output folder:\n{out}\n\nPNG files: {png}\nSVG files: {svg}\n\n"
        if manifest.exists():
            try:
                text += "Manifest:\n" + json.dumps(json.loads(manifest.read_text(encoding="utf-8")), indent=2)
            except Exception:
                text += manifest.read_text(encoding="utf-8", errors="replace")
        else:
            text += "No manifest yet."
        self.export_log.setPlainText(text)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "current_pixmap") and self.current_pixmap is not None:
            QtCore.QTimer.singleShot(30, self._update_preview_pixmap)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("CanSat Flight Data Studio V12.56 Interface")
    win = MainWindow()
    win.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
