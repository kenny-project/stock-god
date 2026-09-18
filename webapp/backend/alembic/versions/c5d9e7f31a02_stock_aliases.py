"""stock aliases

Revision ID: c5d9e7f31a02
Revises: a3f8c1d92b47
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d9e7f31a02'
down_revision: Union[str, Sequence[str], None] = 'a3f8c1d92b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # JSON 数组存字符串别名；server_default 保证 SQLite ALTER ADD COLUMN 后旧行有值
    op.add_column('stock', sa.Column('aliases', sa.JSON(), nullable=False,
                                     server_default='[]'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stock', 'aliases')
