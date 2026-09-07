"""Application configuration — loaded once from environment / .env.

Every tunable named in DESIGN.md / DESIGN_QA.md lives here as a typed
field, never hardcoded elsewhere in the app.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Postgres (source of truth, DESIGN.md "Queue choice") ---
    DATABASE_URL: str = "sqlite:///./local_dev.db"

    # --- RabbitMQ ---
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    QUEUE_NAME: str = "jobs"

    # --- Blur detection (DESIGN.md "Blur detection & determinism") ---
    BLUR_THRESHOLD: float = 100.0

    # --- Reconciler (DESIGN.md §5 / Appendix) ---
    PROCESSING_TIMEOUT_SECONDS: int = 120
    RECONCILER_INTERVAL_SECONDS: int = 5

    # --- Retries (DESIGN.md §6) ---
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_BASE_SECONDS: float = 2.0

    # --- Images ---
    # Container-internal base directory every image_path is resolved
    # against and validated to stay inside (EDGECASE.md 1.2 — path
    # traversal). The *host* folder that gets bind-mounted here is
    # controlled separately, by SAMPLE_IMAGES_DIR, so people can keep
    # the actual image files out of git and just point this at
    # wherever they unzipped the shared sample_images/ folder.
    IMAGE_BASE_DIR: str = "/app/sample_images"
    # Host-side path (used by docker-compose.yml's volume mapping only
    # — the app code never reads this one directly, it reads
    # IMAGE_BASE_DIR, which is where that host path ends up *inside*
    # the container).
    SAMPLE_IMAGES_DIR: str = "./sample_images"
    # EDGECASE.md 1.5 — reject before decode, not after the worker OOMs
    # on a 500MB TIFF or a decompression-bomb PNG.
    MAX_IMAGE_SIZE_BYTES: int = 20_000_000  # 20MB

    # --- API ---
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
