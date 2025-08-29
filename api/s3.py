import os
from pathlib import Path
import boto3
from botocore.config import Config as BotoConfig
from django.conf import settings


def get_s3_client():
    """
    SDK client for server-side upload/download.
    """
    session = boto3.session.Session(
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    )
    return session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,  # e.g. http://127.0.0.1:9000
        config=BotoConfig(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
        ),
    )


def get_presign_client():
    """
    Separate client for generating presigned URLs that the browser/curl will call.
    Uses S3_PUBLIC_ENDPOINT so the URL host matches what the client reaches.
    """
    public_endpoint = os.getenv("S3_PUBLIC_ENDPOINT", settings.S3_ENDPOINT_URL)
    session = boto3.session.Session(
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    )
    return session.client(
        "s3",
        endpoint_url=public_endpoint,  # e.g. http://127.0.0.1:9000
        config=BotoConfig(
            s3={"addressing_style": "path"},
            signature_version="s3v4",  # ensures AWS4 signing
        ),
    )


def create_presigned_put(key: str, content_type: str | None = None, expires: int | None = None) -> dict:
    """
    Create a presigned PUT URL to upload a single object directly to S3/MinIO.

    IMPORTANT:
    We intentionally DO NOT sign the ContentType parameter to avoid
    'headers not signed' / 'signature does not match' errors when clients
    omit or alter Content-Type. Clients may still send the header; it just
    isn't part of the signature.
    """
    s3 = get_presign_client()
    params = {
        "Bucket": settings.S3_BUCKET,
        "Key": key,
        # Do NOT include "ContentType" in Params (keeps signature header-agnostic).
    }
    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params=params,
        ExpiresIn=expires or settings.S3_PRESIGN_EXPIRE_SECONDS,
        HttpMethod="PUT",
    )
    # Suggest the header back to clients (not required for signature).
    headers = {"Content-Type": content_type} if content_type else {}
    return {"url": url, "headers": headers}


def create_presigned_get(key: str, expires: int | None = None) -> str:
    """
    Create a presigned GET URL to download an object.
    """
    s3 = get_presign_client()
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=expires or settings.S3_PRESIGN_EXPIRE_SECONDS,
        HttpMethod="GET",
    )


def object_url(key: str) -> str:
    """
    Dev convenience: construct a direct object URL against the PUBLIC endpoint.
    Prefer create_presigned_get for production access.
    """
    base = os.getenv("S3_PUBLIC_ENDPOINT", settings.S3_ENDPOINT_URL)
    return f"{base}/{settings.S3_BUCKET}/{key}"


def upload_file(local_path: str, key: str, content_type: str | None = None):
    """
    Upload a single file to S3/MinIO with an optional Content-Type.
    """
    s3 = get_s3_client()
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    s3.upload_file(str(local_path), settings.S3_BUCKET, key, ExtraArgs=extra or None)


def upload_dir(local_dir: str, key_prefix: str):
    """
    Recursively upload all files under local_dir to bucket with prefix key_prefix.
    Sets basic Content-Types for common HLS assets (.m3u8 playlists and .ts segments).
    """
    s3 = get_s3_client()
    base = Path(local_dir)

    for p in base.rglob("*"):
        if not p.is_file():
            continue

        rel = p.relative_to(base)
        key = f"{key_prefix}/{rel}".replace("\\", "/")  # Windows safety

        # Minimal content-type hints for HLS
        extra = {}
        suf = p.suffix.lower()
        if suf == ".m3u8":
            extra["ContentType"] = "application/vnd.apple.mpegurl"
        elif suf in (".ts", ".m2ts"):
            extra["ContentType"] = "video/MP2T"

        s3.upload_file(str(p), settings.S3_BUCKET, key, ExtraArgs=extra or None)
