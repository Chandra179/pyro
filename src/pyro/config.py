from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_YAML_PATH = _REPO_ROOT / "config" / "config.yaml"


class RouterConfig(BaseModel):
    num_retries: int = 2
    cooldown_time: int = 30
    allowed_fails: int = 1
    # TokenRouter (api.tokenrouter.com) is an OpenAI-compatible multi-provider
    # proxy — model IDs are its own "<provider>/<model-slug>" aliases, not raw
    # upstream model names. No confirmed free tier; treated as a paid tier.
    tokenrouter_model: str = "deepseek/deepseek-v4-flash-0731"
    tokenrouter_api_base: str = "https://api.tokenrouter.com/v1"
    openrouter_free_models: list[str] = [
        "openrouter/openai/gpt-oss-20b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/google/gemma-4-31b-it:free",
        "openrouter/openrouter/free",
    ]
    groq_model: str = "groq/llama-3.3-70b-versatile"
    gemini_model: str = "gemini/gemini-2.5-flash"
    openai_model: str = "gpt-4o-mini"


class ScrapeConfig(BaseModel):
    concurrency: int = 5
    settle_ms: int = 3000
    max_attempts: int = 3
    retry_backoff_s: int = 8
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    viewport_width: int = 1280
    viewport_height: int = 900
    locale: str = "en-US"
    challenge_markers: list[str] = [
        "Attention Required! | Cloudflare",
        "Just a moment...",
        "cf-chl",
    ]


class SitemapConfig(BaseModel):
    user_agent: str = "Mozilla/5.0 (compatible; pyro-blog-crawler/1.0)"
    non_article_path_segments: list[str] = [
        "/tagged/",
        "/tag/",
        "/category/",
        "/categories/",
        "/author/",
    ]


class CleanConfig(BaseModel):
    boilerplate_tags: list[str] = ["nav", "header", "footer", "aside", "script", "style", "noscript", "form"]
    boilerplate_selectors: list[str] = [
        "[class*=comment]",
        "[id*=comment]",
        "[class*=related]",
        "[class*=sidebar]",
        "[class*=newsletter]",
        "[class*=subscribe]",
        "[class*=cookie]",
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", yaml_file=_CONFIG_YAML_PATH)

    # Secrets — env/.env only, never sourced from config.yaml.
    openrouter_api_key: str | None = None
    google_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    tokenrouter_api_key: str | None = None

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
    synthesis_model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    synthesis_max_tokens: int = 16000

    # Fixed domain taxonomy for extraction classification.
    domains: list[str] = [
        "Authentication",
        "Recommendation Engine",
        "Messaging & Real-Time",
        "Compute Orchestration",
        "Observability",
        "Data Platform",
        "ML/Detection",
        "Media/Content Pipeline",
        "Other",
    ]

    router: RouterConfig = RouterConfig()
    scrape: ScrapeConfig = ScrapeConfig()
    sitemap: SitemapConfig = SitemapConfig()
    clean: CleanConfig = CleanConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority, highest first: init kwargs (e.g. Settings(**overrides) from
        # run_pipeline.py) > env/.env > config/config.yaml (checked-in
        # defaults) > hardcoded field defaults above.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=_CONFIG_YAML_PATH),
            file_secret_settings,
        )


def get_settings() -> Settings:
    return Settings()
