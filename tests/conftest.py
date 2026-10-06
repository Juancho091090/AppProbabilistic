import pytest

from sports_analytics.config.loader import load_config


@pytest.fixture(scope="session")
def app_config():
    return load_config()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Evita que un .env local o variables reales se filtren a los tests."""
    for var in [
        "API_FOOTBALL_KEY",
        "TENNIS_API_KEY",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SMTP_HOST",
        "SMTP_PASSWORD",
        "EMAIL_FROM",
        "EMAIL_TO",
        "DRY_RUN",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
