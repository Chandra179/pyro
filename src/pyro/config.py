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
    # Per-request cap so a hung/slow model raises a timeout exception instead
    # of blocking forever — needed for num_retries/cooldown fallback to ever
    # trigger on a stall (only raised exceptions advance the cascade).
    timeout: float = 60.0
    stream_timeout: float = 60.0
    # TokenRouter (api.tokenrouter.com) is an OpenAI-compatible multi-provider
    # proxy — model IDs are its own "<provider>/<model-slug>" aliases, not raw
    # upstream model names.
    tokenrouter_model: str = "deepseek/deepseek-v4-flash-0731"
    tokenrouter_api_base: str = "https://api.tokenrouter.com/v1"
    # TokenRouter's own "-free" aliases — rotating time-boxed/capacity-limited
    # promos (e.g. Kimi K3 was free for a stretch), not a stable free tier.
    # Tried before the paid tokenrouter_model tier, same api_base/api_key.
    tokenrouter_free_models: list[str] = [
        "qwen/qwen3.8-max-free",
    ]
    # OpenCode Zen (opencode.ai) — another OpenAI-compatible passthrough, same
    # shape as the TokenRouter tiers above. Model IDs are OpenCode's own
    # rotating "-free" promos (no stable free tier guaranteed long-term).
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

    # Some tiers (e.g. TokenRouter's throttle: "Maximum 1 requests within 1 minutes") are far
    # tighter than num_retries/cooldown_time above, which only governs advancing to the *next*
    # cascade tier on failure — no help when the tier that got rate-limited is the only one
    # configured. This is a separate same-tier retry: wait it out and try again.
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

    Extraction prompts live under prompts/extraction/<variant>/, e.g.
    prompts/extraction/default/system.md — "variant" is a growable set of alternate prompt
    styles for extraction (see pyro.prompts.list_variants). Which variant backs a given run is
    configurable (see build_prompts_config below), which is what the dashboard uses to let a
    run pick its extraction template. The graph-merge prompt has no variant concept (v1 is a
    single fixed template, not user-selectable) — see prompts/merge/.
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

    # ArangoDB connection credentials — non-secret connection shape (host,
    # database/collection names) lives in ArangoConfig below.
    arango_username: str = "root"
    arango_password: str | None = None

    # Extraction (Pass 1) concurrency, bounded to the active tier's RPM limit.
    extraction_rpm_limit: int = 20
    extraction_concurrency: int = 5

    # Decoding controls for extraction calls. Free-tier models are the most prone to
    # repetition-loop collapse — a low temperature plus a frequency penalty discourages
    # the model from reusing a token it's already emitted.
    extraction_temperature: float = 0.3
    extraction_frequency_penalty: float = 0.4
    # Fallback output cap, used only for cascade tiers whose model litellm doesn't
    # recognize (see router._max_tokens_for) — known models get their own real limit.
    extraction_max_tokens: int = 2000

    # Chunking for outlier posts.
    chunk_token_threshold: int = 8000
    chunk_overlap_tokens: int = 500

    # Code block collapsing.
    code_block_line_threshold: int = 15

    # Graph-merge pass: reconciles each article's extracted entities/relationships against the
    # company's entity graph so far. Single fixed high-capability model (same reasoning as the
    # old synthesis_model), not part of the extraction cascade.
    graph_model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    graph_max_tokens: int = 8000
    # Temporary cap on articles merged per run, for cheap test runs.
    graph_article_limit: int | None = None
    # Minimum rapidfuzz token_sort_ratio (0-100) for the deterministic pre-pass to treat an
    # article's entity name as the same system as an existing one without asking the model. High
    # by design: a false merge silently fuses two real systems into one node and is much harder to
    # notice than a missed merge, which the LLM tier still catches. See graph/resolve.py.
    graph_fuzzy_threshold: int = 92
    # Cap on how many existing entity names the merge prompt is shown. Below this count every
    # name is included; above it, only the names most similar to the unresolved entities, so
    # prompt size stays flat as a company's graph grows. None disables the cap.
    graph_candidate_names_limit: int | None = 40

    # Fixed domain taxonomy, tagged on extracted entities/relationships — kept as a shared axis
    # for a future cross-company comparison feature, not used to classify/group anymore.
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide Settings instance.

    Cached because constructing one re-reads both `.env` and `config/config.yaml` off disk. The
    dashboard used to build a fresh Settings inside every read accessor, so a single `/data/panel`
    request — which polls every 4 seconds — did that three times over. Anything that wants
    per-call overrides (run_pipeline.py, api/jobs.py picking a prompt variant) constructs
    `Settings(...)` directly and is unaffected by this cache.
    """
    return Settings()
