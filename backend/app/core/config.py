from functools import lru_cache

from pydantic import AliasChoices, Field
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

    # --- Auth (JWT bearer + Argon2 local accounts) ---
    # Required when APP_ENV=production (security.jwt_secret() fails closed);
    # otherwise a random key is generated at process start and persisted to the
    # gitignored backend/var/jwt_secret — never derived from other settings.
    jwt_secret: str = ""
    jwt_ttl_seconds: int = 7 * 24 * 3600
    admin_emails: str = ""  # comma-separated emails granted ADMIN on register

    # --- Mail: real SMTP when configured, otherwise a local file outbox ---
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    email_from: str = "AdmitGraph <no-reply@admitgraph.local>"
    email_outbox_dir: str = "var/outbox"

    # --- In-process scheduler (monitor checks, freshness, reminders) ---
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 30
    monitor_max_checks_per_tick: int = 5

    # --- SerpApi reliability (per serpapi_docs/SERPAPI_INTEGRATION.md) ---
    serpapi_cache_ttl_seconds: int = 6 * 3600
    serpapi_max_concurrency: int = 3
    serpapi_circuit_threshold: int = 5  # consecutive failures before breaker opens

    # --- Password reset / email verification links (P2-14) ---
    # Base URL of the frontend used to build {frontend_url}/reset-password?...
    # and {frontend_url}/verify-email?... links in outgoing emails.
    frontend_url: str = "http://localhost:3000"

    # --- Document file uploads (P2-15): multipart storage root (gitignored) ---
    upload_dir: str = "var/uploads"

    # --- Daily search budget (audit P1-8, app/core/budget.py) ---
    # Aggregate spend bound on top of the per-IP rate limits and the P1-7
    # concurrency caps: at most this many search_runs rows per day (UTC).
    # 0 = unlimited. The AGRAPH_-prefixed names are the documented env vars
    # (AliasChoices keeps the pydantic field name usable in code/tests too).
    daily_search_budget: int = Field(
        default=500,
        validation_alias=AliasChoices("AGRAPH_DAILY_SEARCH_BUDGET", "daily_search_budget"),
    )
    # Per-user variant keyed on the nullable search_runs.user_id column.
    daily_search_budget_per_user: int = Field(
        default=0,  # 0 = unlimited
        validation_alias=AliasChoices(
            "AGRAPH_DAILY_SEARCH_BUDGET_PER_USER", "daily_search_budget_per_user"
        ),
    )

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
