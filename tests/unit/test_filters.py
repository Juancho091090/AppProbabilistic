import pytest

from sports_analytics.data.filters import (
    Surface,
    filter_football_league,
    filter_tennis_event,
    normalize_surface,
)

# ------------------------------------------------------------- fútbol


@pytest.mark.parametrize(
    "league_id,name", [(39, "Premier League"), (239, "Primera A"), (13, "CONMEBOL Libertadores")]
)
def test_football_whitelisted(app_config, league_id, name):
    result = filter_football_league(league_id, name, app_config.competitions)
    assert result.included
    assert result.competition.api_football_id == league_id


def test_football_unknown_league_excluded(app_config):
    # 40 = Championship (Inglaterra), no autorizada
    result = filter_football_league(40, "Championship", app_config.competitions)
    assert not result.included
    assert result.reason == "not_in_whitelist"


def test_football_friendlies_excluded(app_config):
    result = filter_football_league(667, "Friendlies Clubs", app_config.competitions)
    assert not result.included
    assert result.reason == "friendly_excluded"


def test_friendlies_allowed_when_configured(app_config):
    cfg = app_config.competitions.model_copy(update={"include_friendlies": True})
    # Aun permitido, un amistoso solo entra si su ID está en la lista blanca
    assert not filter_football_league(667, "Friendlies Clubs", cfg).included


# ------------------------------------------------------------- tenis


@pytest.mark.parametrize(
    "category,tournament,tour",
    [
        ("ATP", "Roland Garros", "ATP"),
        ("ATP Masters 1000", "Madrid", "ATP"),
        ("ATP 500", "Basel", "ATP"),
        ("ATP 250", "Bogotá", "ATP"),
        ("WTA 1000", "Indian Wells", "WTA"),
        ("WTA 250", "Bogotá", "WTA"),
        ("Grand Slam", "Wimbledon Women Singles", "WTA"),
        ("ATP Finals", "Turin", "ATP"),
    ],
)
def test_tennis_main_tour_included(app_config, category, tournament, tour):
    result = filter_tennis_event(category, tournament, app_config.tennis, tour_hint=tour)
    assert result.included, result.reason
    assert result.tour == tour


@pytest.mark.parametrize(
    "category,tournament",
    [
        ("Challenger", "Challenger Lima"),
        ("ATP Challenger Tour", "Bogotá"),
        ("ITF Men", "M15 Cancun"),
        ("ITF Women", "W35 Santo Domingo"),
        ("Futures", "Medellín"),
        ("Junior", "Roland Garros Boys"),
        ("Exhibition", "Mubadala World Tennis Championship"),
        ("WTA 125", "Cali"),
        ("UTR Pro Tennis Series", "Florida"),
    ],
)
def test_tennis_secondary_circuits_excluded(app_config, category, tournament):
    result = filter_tennis_event(category, tournament, app_config.tennis)
    assert not result.included
    assert result.reason.startswith("excluded:")


def test_tennis_doubles_excluded(app_config):
    result = filter_tennis_event("ATP 500", "Basel Doubles", app_config.tennis)
    assert not result.included
    assert result.reason == "doubles_excluded"


def test_tennis_unknown_tour_excluded(app_config):
    assert not filter_tennis_event("Local Open", "Club X", app_config.tennis).included


def test_itf_word_boundary(app_config):
    # 'itf' dentro de otra palabra no debe excluir
    result = filter_tennis_event("ATP 250", "Pitfield Open", app_config.tennis, tour_hint="ATP")
    assert result.included


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hard", Surface.HARD),
        ("Indoor Hard", Surface.HARD),
        ("Red clay", Surface.CLAY),
        ("Grass", Surface.GRASS),
        (None, Surface.UNKNOWN),
        ("Sand", Surface.UNKNOWN),
    ],
)
def test_surface_normalization(app_config, raw, expected):
    assert normalize_surface(raw, app_config.tennis) == expected
