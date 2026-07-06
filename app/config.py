"""Environment-driven configuration (design doc, Appendix A)."""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ once, at import time. Modules like app.llm.client
# read os.getenv() directly (ANTHROPIC_API_KEY, LLM_DAILY_BUDGET_USD), and
# pydantic-settings' env_file only feeds its own fields — not the process env.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    default_region: str = "NG"
    cache_ttl_seconds: int = 1800          # 30 min hot-query cache
    request_timeout_seconds: float = 10.0  # per outbound HTTP request
    max_retries: int = 3                   # NFR-04
    max_workers: int = 8                   # concurrent adapter fan-out
    circuit_breaker_failures: int = 3      # consecutive failures → degraded
    circuit_breaker_cooldown_seconds: int = 300
    rate_limit_per_min: int = 30           # NFR-08
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 PriceCompareBot/1.0"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
