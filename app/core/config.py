from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EHOS_", env_file=".env", extra="ignore")

    project_name: str = "EHOS Backend API"
    environment: str = "development"
    debug: bool = True

    database_url: str = "postgresql+asyncpg://ehos:ehos@localhost:5434/ehos"
    redis_url: str = "redis://localhost:6379/0"

    seed_data_dir: str = "../crm"
    audit_data_dir: str = "../audit"

    jwt_secret_key: str = "change-me-in-production"
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 30
    jwt_algorithm: str = "HS256"

    cors_origins_raw: str = "http://localhost:3001,http://localhost:3002"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "ehos"
    minio_secret_key: str = "change-me"
    minio_bucket: str = "ehos-media"
    minio_secure: bool = False

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "EHOS Notifications <no-reply@ehos.local>"
    wa_api_url: str = ""
    wa_token: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
