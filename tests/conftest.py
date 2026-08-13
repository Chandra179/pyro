import pytest

# litellm's import-time `load_dotenv()` leaks the real `.env` into the process
# environment; pydantic-settings' `_env_file=None` only skips the file source,
# not os.environ, so real keys would otherwise bleed into "no keys configured"
# tests. Clear them for every test so router/config tests stay isolated.
_PROVIDER_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "TOKENROUTER_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
