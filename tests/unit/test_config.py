import logging

import pytest
import yaml

from sports_analytics.config.loader import load_config
from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import JsonFormatter, redact, register_secret


def test_config_loads(app_config):
    assert len(app_config.competitions.football) >= 20
    assert app_config.models.version == "ensemble_v1"


def test_required_leagues_present(app_config):
    names = {c.key for c in app_config.competitions.football}
    for key in [
        "arg_liga_profesional",
        "col_primera_a",
        "eng_premier_league",
        "esp_laliga",
        "fra_ligue_1",
        "ita_serie_a",
        "bra_serie_a",
        "ger_bundesliga",
        "ger_2_bundesliga",
        "ned_eredivisie",
        "por_primeira_liga",
        "ecu_ligapro",
        "per_liga_1",
        "mex_liga_mx",
        "uefa_champions_league",
        "conmebol_libertadores",
    ]:
        assert key in names


def test_ensemble_weights_must_sum_to_one(tmp_path, app_config):
    src = load_config().models.model_dump()
    src["football"]["ensemble_weights_1x2"]["elo"] = 0.9
    for name in ["competitions.yaml", "tennis.yaml"]:
        (tmp_path / name).write_text(
            yaml.safe_dump(
                app_config.competitions.model_dump(mode="json")
                if name.startswith("comp")
                else app_config.tennis.model_dump(mode="json")
            )
        )
    (tmp_path / "models.yaml").write_text(yaml.safe_dump(src))
    with pytest.raises(ValueError, match="deben sumar 1"):
        load_config(tmp_path)


def test_secrets_not_exposed_in_repr(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "super-secret-value-123")
    settings = Settings()
    assert "super-secret-value-123" not in repr(settings)
    assert settings.api_football_key.get_secret_value() == "super-secret-value-123"


def test_invalid_timezone_rejected(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValueError):
        Settings()


def test_email_recipients_split(monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "a@x.com, b@y.com,")
    assert Settings().email_recipients == ["a@x.com", "b@y.com"]


def test_redaction_of_known_patterns():
    register_secret("my-registered-secret")
    text = "token=abc123 x-apisports-key: KEY999 sk-ant-api03-ABCDEFGHIJ my-registered-secret"
    out = redact(text)
    for leaked in ["abc123", "KEY999", "sk-ant-api03-ABCDEFGHIJ", "my-registered-secret"]:
        assert leaked not in out


def test_json_formatter_redacts_extra_fields():
    record = logging.makeLogRecord(
        {"msg": "call", "levelname": "INFO", "url": "https://x?api_key=SECRET42"}
    )
    out = JsonFormatter().format(record)
    assert "SECRET42" not in out
