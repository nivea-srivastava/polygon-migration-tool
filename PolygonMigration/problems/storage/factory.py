import logging

from django.conf import settings

from .base import StorageBackend

logger = logging.getLogger(__name__)


def get_storage() -> StorageBackend:
    """
    Return the StorageBackend configured by STORAGE_PROVIDER.

    Implemented providers in this build:
      - 's3' : Upstash Blob (S3-compatible)

    Azure is NOT implemented in this build.
    """
    provider = getattr(settings, "STORAGE_PROVIDER", None)
    if not provider:
        raise ValueError(
            "STORAGE_PROVIDER is not set. Add STORAGE_PROVIDER=s3 to your .env "
            "and restart the server."
        )
    provider = provider.strip().lower()

    if provider == "s3":
        from .upstash_backend import UpstashStorageBackend

        if not getattr(settings, "UPSTASH_BLOB_TOKEN", None):
            raise ValueError(
                "UPSTASH_BLOB_TOKEN is required when STORAGE_PROVIDER=s3."
            )
        logger.debug("Selected storage backend: upstash (s3)")
        return UpstashStorageBackend()

    raise ValueError(
        f"STORAGE_PROVIDER={provider!r} is not implemented in this build. "
        f"Supported: 's3'."
    )