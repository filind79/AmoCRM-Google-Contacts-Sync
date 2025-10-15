"""add settings table

Revision ID: 20240514_000003
Revises: 20240221_000002
Create Date: 2024-05-14 00:00:03.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "20240514_000003"
down_revision = "20240221_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("settings")
