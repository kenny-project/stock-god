"""task download progress columns

Revision ID: e7b3c9d25a41
Revises: b7e4a9c05d13
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b3c9d25a41'
down_revision: Union[str, Sequence[str], None] = 'b7e4a9c05d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('task', sa.Column('progress_done', sa.Integer(), nullable=True))
    op.add_column('task', sa.Column('progress_total', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('task', 'progress_total')
    op.drop_column('task', 'progress_done')
