import mimetypes
import os
from uuid import uuid4

import boto3
from fastapi import HTTPException


def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_S3_REGION"),
    )


def upload_image_to_s3(file_content, file_name: str):
    """
    파일 내용과 이름을 받아 S3에 업로드하고 URL을 반환함
    """
    s3 = get_s3_client()
    bucket_name = os.getenv("AWS_S3_BUCKET_NAME")
    region = os.getenv("AWS_S3_REGION")

    if not bucket_name or not region:
        raise HTTPException(status_code=500, detail="AWS S3 환경변수 설정이 필요합니다.")

    unique_filename = f"{uuid4()}_{file_name}"
    content_type = mimetypes.guess_type(file_name)[0] or "image/jpeg"

    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=unique_filename,
            Body=file_content,
            ContentType=content_type,
        )

        s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{unique_filename}"
        return s3_url

    except Exception as e:
        print(f"❌ S3 업로드 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="이미지 저장 중 오류가 발생했습니다.")
