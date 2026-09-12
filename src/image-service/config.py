import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Settings:
    table_name: str
    bucket_name: str
    region: str = "us-east-1"
    endpoint_url: str | Optional = None
    public_s3_endpoint_url: str | Optional = None
    max_upload_bytes: int = 10 * 1024 * 1024               # 10 MB
    upload_url_ttl_seconds: int = 15 * 60                  # pre-signed upload form lifetime
    download_url_ttl_seconds: int = 5 * 60                 # pre-signed download URL lifetime
    unfinished_record_ttl_seconds: int = 24 * 60 * 60      # PENDING/REJECTED records auto-expire

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            table_name=env.get("TABLE_NAME", "images"),
            bucket_name=env.get("BUCKET_NAME", "images-bucket"),
            region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1",
            endpoint_url=env.get("AWS_ENDPOINT_URL") or None,
            public_s3_endpoint_url=env.get("PUBLIC_S3_ENDPOINT_URL") or None,
            max_upload_bytes=int(env.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)),
            upload_url_ttl_seconds=int(env.get("UPLOAD_URL_TTL_SECONDS", 15 * 60)),
            download_url_ttl_seconds=int(env.get("DOWNLOAD_URL_TTL_SECONDS", 5 * 60)),
        )