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
    """回滚：先删除季报行（quarter 非空），再恢复三列唯一约束并删除 quarter 列。

    存量库中同一 (stock_id, form_type, fiscal_year) 下可能已登记多行季报
    （如 10-Q 的 Q1/Q3 各一行，或季报与年报同 form 同财年），直接重建三列
    唯一索引会撞 UNIQUE 冲突抛 IntegrityError。降级本来就丢失 quarter 维度、
    无法保留全部季报行，故先删季报行是有损但合理的取舍；年报行（quarter
    为 NULL）全部保留，降级后仍可通过重新 upgrade 恢复季度维度再登记。"""
    # 必须在 batch_alter_table 重建表之前删行，否则 UNIQUE 冲突
    op.execute(sa.text("DELETE FROM analysis WHERE quarter IS NOT NULL"))
    with op.batch_alter_table('analysis') as batch_op:
        batch_op.drop_constraint('uq_analysis', type_='unique')
        batch_op.create_unique_constraint(
            'uq_analysis', ['stock_id', 'form_type', 'fiscal_year'])
        batch_op.drop_column('quarter')
