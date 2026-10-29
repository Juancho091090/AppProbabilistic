"""Precios de mercados de línea (goles, córners, ambos marcan) y mercado en el registro

Revision ID: 0006_market_lines
Revises: 0005_paper_selections
Create Date: 2026-10-29 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_market_lines"
down_revision: str | Sequence[str] | None = "0005_paper_selections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker_name", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("line", sa.String(length=10), nullable=False),
        sa.Column("odd_yes", sa.Float(), nullable=False),
        sa.Column("odd_no", sa.Float(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["football_matches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "bookmaker_id", "market", "line"),
    )
    op.create_index("ix_market_lines_match_id", "market_lines", ["match_id"])
    op.add_column(
        "paper_selections",
        sa.Column("market", sa.String(length=20), nullable=False, server_default="1x2"),
    )
    op.add_column(
        "paper_selections",
        sa.Column("line", sa.String(length=10), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("paper_selections", "line")
    op.drop_column("paper_selections", "market")
    op.drop_index("ix_market_lines_match_id", table_name="market_lines")
    op.drop_table("market_lines")
