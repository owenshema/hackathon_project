from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"

    # Local Postgres default for this hackathon machine
    database_url: str = (
        "postgresql+asyncpg://postgres:1234@localhost:5432/hackathon_db"
    )
    # Set true only if CREATE EXTENSION vector works on your Postgres
    use_pgvector: bool = False

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "google/diffusiongemma-26b-a4b-it"

    # Speed knobs — WhatsApp/Teams need sub-~15s replies
    llm_timeout_seconds: float = 25.0
    llm_max_tokens: int = 400
    llm_retries: int = 1
    use_local_embeddings: bool = False
    platform_fast_mode: bool = True

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    @property
    def active_llm(self) -> str:
        if self.nvidia_api_key:
            return "nvidia"
        if self.groq_api_key:
            return "groq"
        if self.gemini_api_key:
            return "gemini"
        return "none"

    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "unipods_verify_token"
    whatsapp_app_secret: str = ""

    # Wassenger WhatsApp API (preferred for this hackathon)
    wassenger_api_key: str = ""
    wassenger_device_id: str = ""
    wassenger_phone: str = ""

    teams_app_id: str = ""
    teams_app_password: str = ""
    teams_tenant_id: str = ""

    upload_dir: str = "./uploads"
    max_upload_mb: int = 50

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def use_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def store_embeddings_as_json(self) -> bool:
        """True when pgvector is unavailable (Windows Postgres without the extension)."""
        return self.use_sqlite or not self.use_pgvector


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
