import os
import boto3
from botocore.exceptions import ClientError

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
BUCKET_NAME = "certifake-data"

s3_client = boto3.client(
    's3',
    endpoint_url=MINIO_ENDPOINT,
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
)

def init_s3():
    try:
        s3_client.create_bucket(Bucket=BUCKET_NAME)
    except ClientError as e:
        if e.response['Error']['Code'] != 'BucketAlreadyOwnedByYou':
            print("Bucket creation failed:", e)

def upload_file_bytes(file_key, file_bytes):
    init_s3()
    s3_client.put_object(Bucket=BUCKET_NAME, Key=file_key, Body=file_bytes)
    return f"{MINIO_ENDPOINT}/{BUCKET_NAME}/{file_key}"

def download_file_bytes(file_key):
    response = s3_client.get_object(Bucket=BUCKET_NAME, Key=file_key)
    return response['Body'].read()
