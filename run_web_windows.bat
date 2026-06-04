@echo off
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
pause
