from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://admitgraph:admitgraph_dev_password@localhost:5432/admitgraph"
    redis_url: str = "redis://localhost:6379/0"
    serpapi_api_key: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    cors_origins: str = "http://localhost:3000"
    app_env: str = "development"
    log_level: str = "INFO"
    rate_limit_per_minute: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
