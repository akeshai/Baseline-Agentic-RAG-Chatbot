from pydantic_settings import BaseSettings, SettingsConfigDict


class CrawlSettings(BaseSettings):
    crawl_default_max_depth: int = 2
    crawl_default_max_pages: int = 20
    crawl_timeout: float = 15.0
    crawl_user_agent: str = "ChatBotCrawler/0.1"

    # Concurrency defaults
    concurrency_strategy: str = (
        "concurrent"  # "single" (sequential) or "concurrent" (parallel)
    )
    concurrency_limit: int = 3

    # Storage defaults
    raw_storage_type: str = "object"  # default to "object" to avoid storing whole HTML in database directly
    object_storage_root: str = "storage_buckets"
    raw_html_bucket: str = "crawls"

    # Object storage provider: "local" or "minio"
    object_storage_provider: str = "local"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = CrawlSettings()
