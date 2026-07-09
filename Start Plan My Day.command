#!/bin/bash
cd "$(dirname "$0")"
NEW=$(python3 -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())" 2>/dev/null)
OLD=$(cat .req_installed.sha 2>/dev/null)
if [ "$NEW" != "$OLD" ]; then
  echo "Requirements changed - installing/updating dependencies..."
  python3 -m pip install -r requirements.txt && echo "$NEW" > .req_installed.sha
else
  echo "Dependencies up to date."
fi
(sleep 4; open http://localhost:8501) >/dev/null 2>&1 &
python3 -m streamlit run app.py --server.port 8501
