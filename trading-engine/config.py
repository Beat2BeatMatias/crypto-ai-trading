"""Engine settings derived from environment variables (Pydantic Settings)."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    """Static, env-derived settings. Runtime config lives in the DB (config_store)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(..., alias="DATABASE_URL")

    binance_api_key: str = Field(..., alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(..., alias="BINANCE_API_SECRET")
    binance_testnet: bool = Field(True, alias="BINANCE_TESTNET")

    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")
    ollama_base_url: str = Field("https://ollama.com/api", alias="OLLAMA_BASE_URL")
    ollama_api_key: str | None = Field(None, alias="OLLAMA_API_KEY")

    trading_mode: str = Field("PAPER_TRADING", alias="TRADING_MODE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    symbol: str = "BTC/USDT"


_settings: EngineSettings | None = None


def get_settings() -> EngineSettings:
    """Memoised settings accessor."""
    global _settings
    if _settings is None:
        _settings = EngineSettings()
    return _settings
