from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    vector_dim: int = 384

    # Vector store configuration
    vector_store_type: str = "milvus"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "chatbot_chunks"

    # MongoDB configuration
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "chatbot"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
