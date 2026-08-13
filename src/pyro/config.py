from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str | None = None
    google_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    tokenrouter_api_key: str | None = None

    # TokenRouter (api.tokenrouter.com) is an OpenAI-compatible multi-provider
    # proxy — model IDs are its own "<provider>/<model-slug>" aliases, not raw
    # upstream model names. No confirmed free tier; treated as a paid tier.
    tokenrouter_model: str = "deepseek/deepseek-v4-flash-0731"

    # Extraction (Pass 1) concurrency, bounded to the active tier's RPM limit.
    extraction_rpm_limit: int = 20
    extraction_concurrency: int = 5

    # Chunking for outlier posts.
    chunk_token_threshold: int = 8000
    chunk_overlap_tokens: int = 500

    # Code block collapsing.
    code_block_line_threshold: int = 15

    # Synthesis batching.
    synthesis_batch_size: int = 50

    # Model used for the single-shot high-reasoning synthesis pass.
    # Largest verified-free OpenRouter model (see router.py) — swap for a paid
    # model via SYNTHESIS_MODEL once you have OPENAI_API_KEY or similar set.
    synthesis_model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


def get_settings() -> Settings:
    return Settings()
