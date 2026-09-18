"""stock is_favorite

Revision ID: a3f8c1d92b47
Revises: 021f26ceb3ef
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f8c1d92b47'
down_revision: Union[str, Sequence[str], None] = '021f26ceb3ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('stock', sa.Column('is_favorite', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stock', 'is_favorite')
