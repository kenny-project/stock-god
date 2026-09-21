"""analysis/dcf_report add generator_version column

Revision ID: d8c3a5e71b94
Revises: f9a2b4c6d8e0
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8c3a5e71b94'
down_revision: Union[str, Sequence[str], None] = 'f9a2b4c6d8e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """记录产物由哪一版生成器产出（单一定义点 scripts/dataversion.py）：
    md 头部 `生成器版本: analysis-v2` / `生成器版本: dcf-v1` 解析后落库；
    NULL = 版本机制引入前的 legacy 旧数据。SQLite ADD COLUMN 原生支持，
    统一走 batch_alter_table 与既有迁移风格一致。"""
    for table in ('analysis', 'dcf_report'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column('generator_version', sa.String(length=16), nullable=True))


def downgrade() -> None:
    """SQLite 无 DROP COLUMN 原生 ALTER 语法，回滚必须 batch_alter_table 重建表；
    旧行数据随列一并丢弃（版本机制下线属回退场景，无损保留无意义）。"""
    for table in ('dcf_report', 'analysis'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('generator_version')
