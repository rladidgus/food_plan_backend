#!/bin/bash
# run_app.sh - FastAPI 서버 실행 스크립트

cd /home/user/food_plan
source venv/bin/activate
export PYTHONPATH=/home/user/food_plan:$PYTHONPATH

echo "🚀 FastAPI 서버 시작..."
python -m app.main
