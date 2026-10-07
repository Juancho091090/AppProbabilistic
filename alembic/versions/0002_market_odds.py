"""market odds benchmark

Revision ID: 0002_market_odds
Revises: 0001_initial
Create Date: 2026-10-07 11:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_market_odds"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_odds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker_name", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("odd_home", sa.Float(), nullable=False),
        sa.Column("odd_draw", sa.Float(), nullable=False),
        sa.Column("odd_away", sa.Float(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["football_matches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "bookmaker_id", "market"),
    )
    op.create_index("ix_market_odds_match_id", "market_odds", ["match_id"])
    op.create_table(
        "market_odds_checks",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bookmakers", sa.Integer(), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["football_matches.id"]),
        sa.PrimaryKeyConstraint("match_id"),
    )


def downgrade() -> None:
    op.drop_table("market_odds_checks")
    op.drop_index("ix_market_odds_match_id", table_name="market_odds")
    op.drop_table("market_odds")
