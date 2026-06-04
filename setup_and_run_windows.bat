@echo off
echo Running CanSat Flight Data Studio LATEST FIXED USE THIS...
py -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_app.py
pause
