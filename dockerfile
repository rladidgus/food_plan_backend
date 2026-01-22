# 1. 파이썬 3.10 공식 이미지 사용
FROM python:3.10-slim

# 2. 시스템 패키지 설치 (PostgreSQL 접속 라이브러리 빌드용)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 3. 작업 디렉토리 설정
WORKDIR /app

# 4. 의존성 파일 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 소스 코드 복사
COPY . .

# 6. 실행 명령어 (-u 옵션은 로그가 즉시 출력되게 함)
CMD ["python", "-u", "main.py"]