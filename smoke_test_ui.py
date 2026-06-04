import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PyQt6 import QtWidgets
from app_v0_3_v1256_interface import MainWindow
app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
win=MainWindow()
assert "Flight Report" in win.pages
assert "Report Summary" not in win.nav_buttons
assert "Report Rule Checks" not in win.nav_buttons
assert hasattr(win, "report_summary")
assert hasattr(win, "report_checks")
win.close()
print("OK")
