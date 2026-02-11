import requests
import json
import sys

# 테스트용 토큰 (main.py에서 이 토큰을 허용하도록 수정했음)
TEST_TOKEN = "TEST_TOKEN_1234"
BASE_URL = "http://localhost:8000"

def test_generate_diet_plan():
    url = f"{BASE_URL}/api/diet-plan/1day"
    headers = {
        # Bearer 스키마를 명시적으로 포함
        "Authorization": f"Bearer {TEST_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # payload
    payload = {
        "goal_type": "diet",
        "target_calorie": 1800,
        "preferred_foods": ["피자", "초콜릿", "삼겹살"]
    }
    
    print(f"Testing URL: {url}")
    print(f"Token: {TEST_TOKEN}")
    print("Sending request...")
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("\n✅ Success!")
            data = response.json()
            plan = data.get("plan", {})
            print(f"Goal: {plan.get('goal_type')}")
            
            for day in plan.get("days", []):
                print(f"--- {day.get('day_label')} ---")
                print(f" Breakfast: {day['breakfast']['name']}")
                print(f" Lunch:     {day['lunch']['name']}")
                print(f" Dinner:    {day['dinner']['name']}")
        else:
            print(f"\n❌ Failed")
            print(response.text)
            
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    test_generate_diet_plan()
