from pydantic_settings import BaseSettings, SettingsConfigDict

from app.configs.yaml_loader import yaml_config

ingest_yaml = yaml_config.get("ingestion", {})


class IngestSettings(BaseSettings):
    """
    Ingestion & Chunker configuration settings loader.
    Defaults to config.yaml value if present, with env variable overrides.
    """

    target_html_selector: str = ingest_yaml.get(
        "target_html_selector", "main.page-content"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = IngestSettings()
