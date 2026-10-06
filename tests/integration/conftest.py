"""Fixtures compartidas de integración: PostgreSQL limpio y migrado con Alembic."""

import os

import pytest
from sqlalchemy import create_engine, text

from sports_analytics.db.session import normalize_url

URL = os.environ.get("TEST_DATABASE_URL")


def migrate_fresh(url: str):
    from alembic import command
    from alembic.config import Config

    eng = create_engine(normalize_url(url))
    try:
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return eng


@pytest.fixture(scope="module")
def engine():
    if not URL:
        pytest.skip("TEST_DATABASE_URL no definida")
    eng = migrate_fresh(URL)
    yield eng
    eng.dispose()
