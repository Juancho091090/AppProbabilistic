"""Marca de xG consultado en football_statistics (evita repetir partidos sin xG en la API)

Revision ID: 0004_stats_xg_checked
Revises: 0003_stats_xg
Create Date: 2026-10-16 08:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_stats_xg_checked"
down_revision: str | Sequence[str] | None = "0003_stats_xg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("football_statistics", sa.Column("xg_checked", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("football_statistics", "xg_checked")
