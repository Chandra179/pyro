from __future__ import annotations

from functools import lru_cache
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
    # Cap so a hung/slow model raises instead of blocking forever — only raised exceptions
    # advance the cascade, so a stall would otherwise never trigger fallback.
    timeout: float = 60.0
    stream_timeout: float = 60.0
    # TokenRouter (api.tokenrouter.com): OpenAI-compatible proxy, model IDs are its own aliases.
    tokenrouter_model: str = "deepseek/deepseek-v4-flash-0731"
    tokenrouter_api_base: str = "https://api.tokenrouter.com/v1"
    # TokenRouter's rotating time-boxed "-free" promos, not a stable free tier.
    tokenrouter_free_models: list[str] = [
        "qwen/qwen3.8-max-free",
    ]
    # OpenCode Zen (opencode.ai): another OpenAI-compatible passthrough, same shape as above.
    opencode_api_base: str = "https://opencode.ai/zen/v1"
    opencode_free_models: list[str] = [
        "big-pickle",
        "deepseek-v4-flash-free",
    ]
    openrouter_free_models: list[str] = [
        "openrouter/openai/gpt-oss-20b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/google/gemma-4-31b-it:free",
        "openrouter/openrouter/free",
    ]
    groq_model: str = "groq/llama-3.3-70b-versatile"
    gemini_model: str = "gemini/gemini-2.5-flash"
    openai_model: str = "gpt-4o-mini"

    # Separate same-tier retry: num_retries/cooldown_time above only govern advancing to the
    # *next* cascade tier, no help when the rate-limited tier is the only one configured.
    rate_limit_max_retries: int = 5
    rate_limit_wait_seconds: int = 65


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
        "/about",
    ]


class PromptsConfig(BaseModel):
    """Paths (relative to the top-level prompts/ dir) for each stage's templates.

    Extraction prompts live under prompts/extraction/<variant>/ (see pyro.prompts.list_variants) —
    the dashboard lets a run pick its variant. The merge prompt has no variant concept.
    """

    extraction_system: str = "extraction/default/system.md"
    extraction_user: str = "extraction/default/user.md"
    merge_system: str = "merge/system.md"
    merge_user: str = "merge/user.md"


class ArangoConfig(BaseModel):
    host: str = "http://localhost:8529"
    database: str = "pyro"
    articles_collection: str = "articles"
    entities_collection: str = "entities"
    relationships_collection: str = "relationships"
    jobs_collection: str = "jobs"


class ArticleStage(BaseModel):
    """One pipeline stage. `key` must match db/articles.py's list_summaries stage strings — this
    is the single source of truth for ordering/labels/colors, so the dashboard never hardcodes
    its own copy."""

    key: str
    label: str
    variant: str


class CleanConfig(BaseModel):
    boilerplate_tags: list[str] = [
        "nav",
        "header",
        "footer",
        "aside",
        "script",
        "style",
        "noscript",
        "form",
    ]
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
    # Fields stay flat here (not grouped into a *Config) when they're meant to be tuned per
    # deployment via a plain env var — no env_nested_delimiter is set, so nested BaseModel groups
    # below can only be overridden through config.yaml, never a flat env var.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", yaml_file=_CONFIG_YAML_PATH
    )

    # Secrets — env/.env only, never sourced from config.yaml.
    openrouter_api_key: str | None = None
    google_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    tokenrouter_api_key: str | None = None
    opencode_api_key: str | None = None

    arango_username: str = "root"
    arango_password: str | None = None

    # Kept low enough to stay under a free tier's typical ~20 RPM ceiling given real call latency.
    extraction_concurrency: int = 5

    # Cap on concurrently running dashboard pipeline jobs across all companies — each does
    # Playwright scraping (CPU/memory heavy) and shares the same LLM cascade, so uncapped
    # bulk-submission would have them all contend at once. A job beyond this cap waits
    # ("pending") rather than running; see api/jobs.py's `_JOB_SLOTS`.
    max_concurrent_jobs: int = 3

    # Free-tier models are prone to repetition-loop collapse; low temperature + frequency
    # penalty discourages reusing an already-emitted token.
    extraction_temperature: float = 0.3
    extraction_frequency_penalty: float = 0.4
    # Fallback cap for cascade tiers litellm doesn't recognize; known models get their own limit.
    extraction_max_tokens: int = 2000

    chunk_token_threshold: int = 8000
    chunk_overlap_tokens: int = 500
    code_block_line_threshold: int = 15

    # Single fixed high-capability model, not part of the extraction cascade.
    graph_model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    graph_max_tokens: int = 8000
    graph_article_limit: int | None = None
    # rapidfuzz token_sort_ratio (0-100) threshold for the deterministic pre-pass. High by
    # design: a false merge silently fuses two real systems and is harder to notice than a
    # missed merge, which the LLM tier still catches. See graph/resolve.py.
    graph_fuzzy_threshold: int = 92
    # Cap on existing entity names shown to the merge prompt (nearest by similarity once
    # exceeded), so prompt size stays flat as a company's graph grows. None disables the cap.
    graph_candidate_names_limit: int | None = 40

    # Companies processed at once by merge-graph-pending; each company's own merge is still
    # serialized by cli.py's _MERGE_LOCKS.
    merge_pending_concurrency: int = 3

    # Fixed taxonomy tagged on extracted entities/relationships; kept for a future
    # cross-company comparison feature, not used to classify/group today.
    domains: list[str] = [
        "Authentication",
        "Recommendation Engine",
        "Messaging & Real-Time",
        "Compute Orchestration",
        "Observability",
        "Data Platform",
        "ML/Detection",
        "Media/Content Pipeline",
        "Network Infrastructure",
        "Developer Platform",
        "Other",
    ]

    article_stages: list[ArticleStage] = [
        ArticleStage(key="scraped", label="Scraped", variant="neutral"),
        ArticleStage(key="cleaned", label="Cleaned", variant="amber"),
        ArticleStage(key="extracted", label="Extracted", variant="emerald"),
        ArticleStage(key="merged", label="Merged", variant="accent"),
    ]

    router: RouterConfig = RouterConfig()
    scrape: ScrapeConfig = ScrapeConfig()
    sitemap: SitemapConfig = SitemapConfig()
    clean: CleanConfig = CleanConfig()
    prompts: PromptsConfig = PromptsConfig()
    arango: ArangoConfig = ArangoConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority, highest first: init kwargs > env/.env > config.yaml > field defaults.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=_CONFIG_YAML_PATH),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide Settings instance, cached since constructing one re-reads disk.

    Callers needing per-call overrides construct `Settings(...)` directly instead.
    """
    return Settings()
