#!/bin/bash
# Double-click launcher for the User app (macOS).
cd "$(dirname "$0")" || exit 1
source ~/.zshrc 2>/dev/null
pip3 install -q -r requirements.txt 2>/dev/null
( sleep 4; open "http://localhost:8501" ) &
python3 -m streamlit run app.py --server.port 8501
