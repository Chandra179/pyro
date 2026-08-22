import pytest

from pyro.config import Settings

# litellm's import-time load_dotenv() leaks the real .env into the process env; clear these so
# "no keys configured" tests stay isolated.
_PROVIDER_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "TOKENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "OPENCODE_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # pydantic-settings still parses .env straight off disk regardless of the env vars above —
    # disable that file source outright to stop real keys leaking in.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
