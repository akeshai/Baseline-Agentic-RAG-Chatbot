from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """
    Configuration settings for LLM and Embedding services.
    Loads configurations from environment variables and fallback to defaults.
    """

    llm_provider: str = "oci"
    embedding_provider: str = "oci"

    # OCI Generative AI client configuration
    oci_config_file: str = Field(
        "app/configs/oci.secret.key",
        validation_alias=AliasChoices("oci_config_path", "oci_config_file"),
    )
    oci_profile: str = "DEFAULT"

    oci_compartment_id: str = Field(
        "", validation_alias=AliasChoices("oci_compartment_id")
    )

    oci_service_endpoint: str = Field(
        "https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com",
        validation_alias=AliasChoices("oci_genai_endpoint", "oci_service_endpoint"),
    )

    # OCI Model IDs
    oci_llm_model_id: str = Field(
        "openai.gpt-oss-20b",
        validation_alias=AliasChoices("oci_model_id", "oci_llm_model"),
    )
    oci_embedding_model_id: str = Field(
        "cohere.embed-multilingual-image-v3.0",
        validation_alias=AliasChoices("oci_embedding_model"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = LLMSettings()
