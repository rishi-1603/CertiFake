import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
BUCKET_NAME = "certifake-data"

# Explicit short timeouts + a small retry budget so that a MinIO/S3 outage
# fails in ~2-3 seconds instead of boto3's default (which can take 8+
# seconds per call before giving up) -- an unresponsive storage backend must
# not make /analyze hang the caller for a long time before returning an
# error.
_client_config = Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1})

s3_client = boto3.client(
    "s3",
    endpoint_url=MINIO_ENDPOINT,
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
    config=_client_config,
)


class StorageUnavailableError(Exception):
    """Raised when the object store cannot be reached or a bucket/object
    operation fails for reasons unrelated to the caller's input. Callers in
    app/api.py catch this and return a clean 503 instead of leaking a raw
    boto3 stack trace / 500 to the client."""


class StorageObjectNotFoundError(Exception):
    """Raised specifically when the requested key does not exist in a
    reachable, healthy bucket -- distinct from StorageUnavailableError so
    callers can correctly return 404 (missing object) instead of 503
    (backend down) for this case."""


def init_s3():
    try:
        s3_client.create_bucket(Bucket=BUCKET_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise StorageUnavailableError(f"Bucket creation failed: {e}") from e
    except BotoCoreError as e:
        raise StorageUnavailableError(f"Could not reach object storage: {e}") from e


def upload_file_bytes(file_key, file_bytes):
    try:
        init_s3()
        s3_client.put_object(Bucket=BUCKET_NAME, Key=file_key, Body=file_bytes)
    except (BotoCoreError, ClientError) as e:
        raise StorageUnavailableError(f"Could not upload to object storage: {e}") from e
    return f"{MINIO_ENDPOINT}/{BUCKET_NAME}/{file_key}"


def download_file_bytes(file_key):
    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=file_key)
        return response["Body"].read()
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            raise StorageObjectNotFoundError(f"Object not found: {file_key}") from e
        raise StorageUnavailableError(f"Could not download from object storage: {e}") from e
    except BotoCoreError as e:
        raise StorageUnavailableError(f"Could not download from object storage: {e}") from e
