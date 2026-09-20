"""analysis add quarter column and extend unique constraint

Revision ID: f9a2b4c6d8e0
Revises: e7b3c9d25a41
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a2b4c6d8e0'
down_revision: Union[str, Sequence[str], None] = 'e7b3c9d25a41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """SQLite 下改唯一约束需重建表，用 batch_alter_table：
    先加 quarter 列（年报为 NULL），再由旧三列约束换成含 quarter 的四列约束。"""
    with op.batch_alter_table('analysis') as batch_op:
        batch_op.add_column(sa.Column('quarter', sa.String(length=8), nullable=True))
        batch_op.drop_constraint('uq_analysis', type_='unique')
        batch_op.create_unique_constraint(
            'uq_analysis', ['stock_id', 'form_type', 'fiscal_year', 'quarter'])


def downgrade() -> None:
    """回滚：恢复三列唯一约束后删除 quarter 列。

    注意：若已存在同 (stock_id, form_type, fiscal_year) 的多行季报数据，
    重建三列唯一索引时会失败，需先清理季报行再降级。"""
    with op.batch_alter_table('analysis') as batch_op:
        batch_op.drop_constraint('uq_analysis', type_='unique')
        batch_op.create_unique_constraint(
            'uq_analysis', ['stock_id', 'form_type', 'fiscal_year'])
        batch_op.drop_column('quarter')
