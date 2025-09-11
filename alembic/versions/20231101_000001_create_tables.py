"""create links and tokens tables

Revision ID: 20231101_000001
Revises: 
Create Date: 2023-11-01 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "20231101_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("amo_contact_id", sa.String, nullable=False, unique=True),
        sa.Column("google_resource_name", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("system", sa.String, nullable=False, index=True),
        sa.Column("access_token", sa.String, nullable=False),
        sa.Column("refresh_token", sa.String, nullable=True),
        sa.Column("expiry", sa.DateTime, nullable=True),
        sa.Column("scopes", sa.String, nullable=True),
        sa.Column("account_id", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("tokens")
    op.drop_table("links")
