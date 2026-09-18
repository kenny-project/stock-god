"""stock index membership flags

Revision ID: b7e4a9c05d13
Revises: c5d9e7f31a02
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4a9c05d13'
down_revision: Union[str, Sequence[str], None] = 'c5d9e7f31a02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default 保证 SQLite ALTER ADD COLUMN 后旧行有值
    op.add_column('stock', sa.Column('in_sp500', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column('stock', sa.Column('in_ndx100', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stock', 'in_ndx100')
    op.drop_column('stock', 'in_sp500')
