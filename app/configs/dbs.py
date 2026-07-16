from urllib.parse import quote_plus

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_type: str = "postgresql"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "chatbot"
    db_user: str = "postgres"
    db_password: SecretStr = SecretStr("postgres")
    redis_url: str = "redis://localhost:6379/0"
    vector_dim: int = 384

    # Vector store configuration
    vector_store_type: str = "pgvector"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "chatbot_chunks"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @computed_field
    @property
    def database_url(self) -> str:
        password = quote_plus(self.db_password.get_secret_value())

        if self.db_type == "postgresql":
            return (
                f"postgresql://{self.db_user}:{password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )

        if self.db_type == "sqlite":
            return f"sqlite:///{self.db_name}"

        raise ValueError(f"Unsupported database type: {self.db_type}")

    @computed_field
    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )

        if self.database_url.startswith("sqlite://"):
            return self.database_url.replace(
                "sqlite://",
                "sqlite+aiosqlite://",
                1,
            )

        return self.database_url


settings = Settings()
