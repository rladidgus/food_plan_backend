import psycopg2
import os
import time

# 환경 변수에서 DB 정보 가져오기 (기본값 설정)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")

def get_db_connection():
    """DB가 준비될 때까지 연결을 시도합니다."""
    while True:
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS
            )
            return conn
        except psycopg2.OperationalError:
            print("데이터베이스 연결 대기 중 (2초 후 재시도)...")
            time.sleep(2)

def init_db():
    """테이블 생성 및 초기화"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS diet_plan (
            id SERIAL PRIMARY KEY,
            food_name VARCHAR(100),
            calories INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_recommendation(cal):
    """간단한 식단 추천 로직"""
    if cal < 1500:
        return "닭가슴살 샌드위치", 400
    elif 1500 <= cal < 2500:
        return "불고기 덮밥", 650
    else:
        return "스테이크와 구운 야채", 900

def main():
    init_db()
    print("\n" + "="*30)
    print("  🥗 식단 계획 AI 서비스 (v3.10)")
    print("="*30)
    
    try:
        user_input = input("\n오늘의 목표 칼로리를 입력하세요: ")
        goal = int(user_input)
        
        food, kcal = get_recommendation(goal)
        
        print(f"\n🤖 AI 추천: '{food}'")
        print(f"🔥 예상 칼로리: {kcal}kcal")
        
        # 데이터 저장
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO diet_plan (food_name, calories) VALUES (%s, %s)",
            (food, kcal)
        )
        conn.commit()
        print("\n✅ 추천 결과가 DB에 저장되었습니다.")
        
        cur.close()
        conn.close()
    except ValueError:
        print("❌ 숫자를 입력해 주세요.")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    main()