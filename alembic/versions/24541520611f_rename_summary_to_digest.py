"""rename summary to digest

Revision ID: 24541520611f
Revises: b7afbf90dde9
Create Date: 2026-09-22 17:25:41.686845

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '24541520611f'
down_revision: Union[str, Sequence[str], None] = 'b7afbf90dde9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "conversations",
        "summary",
        new_column_name="digest",
        existing_type=sa.String(length=500),
        existing_nullable=False,
    )


def downgrade() -> None:
   op.alter_column(
       "conversations",
       "digest",
       new_column_name="summary",
       existing_type=sa.String(length(500)),
       existing_nullale=False,
   )
