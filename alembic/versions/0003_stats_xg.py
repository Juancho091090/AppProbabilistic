"""xG y tiros al arco en football_statistics

Revision ID: 0003_stats_xg
Revises: 0002_market_odds
Create Date: 2026-10-07 17:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stats_xg"
down_revision: str | Sequence[str] | None = "0002_market_odds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("home_shots_on_target", sa.Integer()),
    ("away_shots_on_target", sa.Integer()),
    ("home_xg", sa.Float()),
    ("away_xg", sa.Float()),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("football_statistics", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column("football_statistics", name)
