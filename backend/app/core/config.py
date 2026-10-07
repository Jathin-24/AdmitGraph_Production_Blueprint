from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://admitgraph:admitgraph_dev_password@localhost:5432/admitgraph"
    redis_url: str = "redis://localhost:6379/0"
    serpapi_api_key: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_providers: str = ""
    llm_api_keys: str = ""
    llm_models: str = ""
    cors_origins: str = "http://localhost:3000"
    app_env: str = "development"
    log_level: str = "INFO"
    rate_limit_per_minute: int = 60

    @property
    def llm_endpoints(self) -> list[dict[str, str]]:
        providers = [p.strip() for p in self.llm_providers.split(",") if p.strip()]
        keys = [k.strip() for k in self.llm_api_keys.split(",") if k.strip()]
        models = [m.strip() for m in self.llm_models.split(",") if m.strip()]
        base_urls = {"openrouter": "https://openrouter.ai/api/v1", "groq": "https://api.groq.com/openai/v1"}
        out = []
        for i, provider in enumerate(providers):
            if i < len(keys) and i < len(models) and provider in base_urls:
                out.append(
                    {
                        "provider": provider,
                        "api_key": keys[i],
                        "model": models[i],
                        "base_url": base_urls[provider],
                    }
                )
        return out

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
