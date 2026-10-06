"""Carga y valida los YAML de configuración de negocio.

Cada YAML se convierte en un modelo Pydantic; un error de tipeo en la
configuración falla al arrancar, no a mitad del pipeline.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parent


class CompetitionKind(StrEnum):
    LEAGUE = "league"
    INTERNATIONAL_CLUB = "international_club"
    INTERNATIONAL_NATIONAL = "international_national"


class SeasonMode(StrEnum):
    CALENDAR = "calendar"
    SPLIT = "split"


class FootballCompetition(BaseModel):
    key: str
    name: str
    country: str
    api_football_id: int
    kind: CompetitionKind
    season_mode: SeasonMode


class CompetitionsConfig(BaseModel):
    include_friendlies: bool = False
    football: list[FootballCompetition]

    @model_validator(mode="after")
    def _unique(self) -> CompetitionsConfig:
        ids = [c.api_football_id for c in self.football]
        keys = [c.key for c in self.football]
        if len(ids) != len(set(ids)):
            raise ValueError("api_football_id duplicado en competitions.yaml")
        if len(keys) != len(set(keys)):
            raise ValueError("key duplicada en competitions.yaml")
        return self

    def by_api_id(self) -> dict[int, FootballCompetition]:
        return {c.api_football_id: c for c in self.football}


class TennisConfig(BaseModel):
    singles_only: bool = True
    min_rank_id: int = 2
    grand_slam_rank_id: int = 4
    tours: list[str]
    include_categories: dict[str, list[str]]
    exclude_patterns: list[str]
    surfaces: dict[str, list[str]]


def _weights_sum_to_one(weights: dict[str, float], name: str) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Los pesos de {name} deben sumar 1 (suman {total:.4f})")
    if any(w < 0 for w in weights.values()):
        raise ValueError(f"Pesos negativos en {name}")


class ModelsConfig(BaseModel):
    """Se mantiene como dict tipado parcialmente: la estructura completa está
    documentada en models.yaml y cada modelo valida su propia sección."""

    version: str
    recency: dict[str, Any]
    football: dict[str, Any]
    tennis: dict[str, Any]
    calibration: dict[str, Any]
    confidence: dict[str, float]

    @model_validator(mode="after")
    def _check_weights(self) -> ModelsConfig:
        _weights_sum_to_one(self.football["ensemble_weights_1x2"], "football.ensemble_weights_1x2")
        _weights_sum_to_one(
            self.tennis["ensemble_weights_winner"], "tennis.ensemble_weights_winner"
        )
        if not 0 < self.confidence["medium"] < self.confidence["high"] <= 1:
            raise ValueError("Umbrales de confianza inválidos: 0 < medium < high <= 1")
        return self


class AppConfig(BaseModel):
    competitions: CompetitionsConfig
    tennis: TennisConfig
    models: ModelsConfig = Field(...)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_config(config_dir: Path | None = None) -> AppConfig:
    base = config_dir or CONFIG_DIR
    return AppConfig(
        competitions=CompetitionsConfig(**_read_yaml(base / "competitions.yaml")),
        tennis=TennisConfig(**_read_yaml(base / "tennis.yaml")),
        models=ModelsConfig(**_read_yaml(base / "models.yaml")),
    )


@lru_cache
def get_config() -> AppConfig:
    return load_config()
