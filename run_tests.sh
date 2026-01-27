#!/bin/bash
# run_tests.sh - 테스트 실행 스크립트

cd /home/user/food_plan
source venv/bin/activate
export PYTHONPATH=/home/user/food_plan:$PYTHONPATH

echo "🧪 테스트 시작..."
python tests/test_inbody_system.py
