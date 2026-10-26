"""Registro simulado: selecciones con precio a la hora del informe y precio de cierre

Revision ID: 0005_paper_selections
Revises: 0004_stats_xg_checked
Create Date: 2026-10-26 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_paper_selections"
down_revision: str | Sequence[str] | None = "0004_stats_xg_checked"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_selections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("rule", sa.String(length=60), nullable=False),
        sa.Column("event_id", sa.String(length=60), nullable=False),
        sa.Column("competition", sa.String(length=200), nullable=False),
        sa.Column("match_label", sa.String(length=200), nullable=False),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection", sa.String(length=10), nullable=False),
        sa.Column("bookmaker_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker_name", sa.String(length=80), nullable=False),
        sa.Column("price_taken", sa.Float(), nullable=False),
        sa.Column("fair_prob_taken", sa.Float(), nullable=False),
        sa.Column("model_prob", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(length=10), nullable=True),
        sa.Column("price_close", sa.Float(), nullable=True),
        sa.Column("fair_prob_close", sa.Float(), nullable=True),
        sa.Column("outcome", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule", "event_id", "selection", name="uq_paper_selection"),
    )
    op.create_index("ix_paper_selections_status", "paper_selections", ["status"])


def downgrade() -> None:
    op.drop_index("ix_paper_selections_status", table_name="paper_selections")
    op.drop_table("paper_selections")
