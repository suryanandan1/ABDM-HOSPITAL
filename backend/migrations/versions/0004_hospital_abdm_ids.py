"""hospital ABDM HIP/HIU service ids (several hospitals on one bridge)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17 10:00:00.000000

Columns are added in place with unique indexes: a batch rebuild of `hospitals` would drop the table, which SQLite
refuses (foreign keys are enforced) once patients and staff reference it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('DROP TABLE IF EXISTS _alembic_tmp_hospitals')  # left over by an earlier failed batch attempt
    op.add_column('hospitals', sa.Column('abdm_hip_id', sa.String(length=50), nullable=True))
    op.add_column('hospitals', sa.Column('abdm_hiu_id', sa.String(length=50), nullable=True))
    op.create_index('uq_hospitals_abdm_hip_id', 'hospitals', ['abdm_hip_id'], unique=True)
    op.create_index('uq_hospitals_abdm_hiu_id', 'hospitals', ['abdm_hiu_id'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_hospitals_abdm_hiu_id', table_name='hospitals')
    op.drop_index('uq_hospitals_abdm_hip_id', table_name='hospitals')
    op.drop_column('hospitals', 'abdm_hiu_id')
    op.drop_column('hospitals', 'abdm_hip_id')
