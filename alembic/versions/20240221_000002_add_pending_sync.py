"""add pending sync table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

revision = "20240221_000002"
down_revision = "20231101_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_sync",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("amo_contact_id", sa.Integer, nullable=False, unique=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime, nullable=False, server_default=func.now()),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
    )
    op.create_index("ix_pending_sync_amo_contact_id", "pending_sync", ["amo_contact_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_pending_sync_amo_contact_id", table_name="pending_sync")
    op.drop_table("pending_sync")
