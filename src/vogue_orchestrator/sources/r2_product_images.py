"""Read selected product images from the stock-agent Cloudflare R2 layout.

This adapter mirrors the stock-agent object-key convention without importing
the sibling project or downloading the full image collection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class R2ImageConfigurationError(ValueError):
    """The optional R2 image settings are incomplete or invalid."""


class R2ImageUnavailable(RuntimeError):
    """A selected R2 product image could not be read."""


@dataclass(frozen=True)
class R2ProductImageSettings:
    account_id: str
    bucket: str
    image_prefix: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    endpoint: str = ""

    @property
    def endpoint_url(self) -> str:
        return self.endpoint or f"https://{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls) -> "R2ProductImageSettings | None":
        """Return None when R2 images are not configured; never load dotenv here."""
        account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
        bucket = os.getenv("R2_BUCKET", "").strip()
        image_prefix = os.getenv("R2_IMAGE_PREFIX", "").strip().strip("/")
        access_key = (os.getenv("R2_ACCESS_KEY_ID", "") or os.getenv("Access Key ID", "")).strip()
        secret_key = (os.getenv("R2_SECRET_ACCESS_KEY", "") or os.getenv("Secret Access Key", "")).strip()
        if not any((account_id, bucket, image_prefix, access_key, secret_key)):
            return None
        missing = [name for name, value in (
            ("R2_ACCOUNT_ID", account_id), ("R2_BUCKET", bucket),
            ("R2_IMAGE_PREFIX", image_prefix), ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret_key),
        ) if not value]
        if missing:
            raise R2ImageConfigurationError("R2 상품 이미지 설정이 불완전합니다: " + ", ".join(missing))
        if any(ch in account_id for ch in "/:@?#"):
            raise R2ImageConfigurationError("R2_ACCOUNT_ID 형식이 올바르지 않습니다.")
        endpoint = os.getenv("R2_ENDPOINT", "").strip().rstrip("/")
        if endpoint:
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise R2ImageConfigurationError("R2_ENDPOINT는 인증 정보를 포함하지 않는 HTTPS endpoint여야 합니다.")
        return cls(account_id, bucket, image_prefix, access_key, secret_key, endpoint)


class R2ProductImageStore:
    """Fetch only requested images from ``prefix/<first3>/<article_id>.jpg``."""

    def __init__(self, settings: R2ProductImageSettings, client=None, cache_dir: Path | None = None):
        self.settings = settings
        self.client = client if client is not None else self._client(settings)
        self.cache_dir = cache_dir or Path(os.getenv("VOGUE_R2_IMAGE_CACHE_DIR", "data/local/r2_product_images"))

    @staticmethod
    def _client(settings: R2ProductImageSettings):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - exercised in minimal installs
            raise R2ImageConfigurationError("R2 이미지 조회에는 boto3가 필요합니다.") from exc
        return boto3.client(
            "s3", endpoint_url=settings.endpoint_url, region_name="auto",
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            config=Config(connect_timeout=5, read_timeout=20,
                          retries={"max_attempts": 2, "mode": "standard"}),
        )

    def object_key(self, article_id: str) -> str:
        normalized = str(article_id).strip()
        if not normalized.isdigit():
            raise ValueError("article_id는 숫자 문자열이어야 합니다.")
        return f"{self.settings.image_prefix}/{normalized[:3]}/{normalized}.jpg"

    def load(self, article_id: str, *, max_bytes: int = 8_000_000) -> tuple[bytes, str]:
        key = self.object_key(article_id)
        destination = self.cache_dir / (key.replace("/", "_") + ".jpg")
        if destination.is_file():
            content = destination.read_bytes()
            return self._validate(content, max_bytes)
        try:
            response = self.client.get_object(Bucket=self.settings.bucket, Key=key)
            expected_size = response.get("ContentLength")
            if expected_size is not None and expected_size > max_bytes:
                raise R2ImageUnavailable("R2 이미지가 허용 크기를 초과했습니다.")
            body = response["Body"]
            content = body.read(max_bytes + 1)
            close = getattr(body, "close", None)
            if callable(close):
                close()
            if expected_size is not None and expected_size != len(content):
                raise R2ImageUnavailable("R2 이미지 크기가 응답 metadata와 일치하지 않습니다.")
            content, mime_type = self._validate(content, max_bytes)
            self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = destination.with_suffix(".part")
            temp.write_bytes(content)
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            os.replace(temp, destination)
            return content, mime_type
        except R2ImageUnavailable:
            raise
        except Exception:
            raise R2ImageUnavailable("R2 상품 이미지를 가져오지 못했습니다.") from None

    @staticmethod
    def _validate(content: bytes, max_bytes: int) -> tuple[bytes, str]:
        if not content or len(content) > max_bytes:
            raise R2ImageUnavailable("R2 이미지가 비어 있거나 허용 크기를 초과했습니다.")
        if not content.startswith(b"\xff\xd8\xff"):
            raise R2ImageUnavailable("R2 상품 이미지가 JPEG 형식이 아닙니다.")
        return content, "image/jpeg"

