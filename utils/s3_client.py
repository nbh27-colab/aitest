from functools import lru_cache

from config.settings import MinIOSettings
from src.data.minIO.minIO_manager import PrivateS3

@lru_cache(maxsize=1)
def get_s3() -> PrivateS3:
    settings = MinIOSettings()
    return PrivateS3(
        private_url=settings.MINIO_PRIVATE_URL,
        public_url=settings.MINIO_PUBLIC_URL,
        region=settings.MINIO_REGION,
        user=settings.MINIO_USER,
        password=settings.MINIO_PASSWORD,
    )