@echo off
if not exist .venv (
    py -m venv .venv
)
call .venv\Scripts\activate
python run_app.py
pause
