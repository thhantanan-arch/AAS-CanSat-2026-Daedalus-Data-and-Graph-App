#!/usr/bin/env bash
set -e
python3 -m pip install -r requirements.txt
python3 -m streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
