"""network administrators: users.hospital_id may be empty (a network administrator belongs to no hospital)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17 18:00:00.000000

SQLite rebuilds the users table for this (batch mode); init_db.migrate() turns foreign key enforcement off for the
rebuild and checks afterwards that every reference is intact.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('hospital_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM users WHERE hospital_id IS NULL")):
        raise RuntimeError("Network administrator accounts exist (users without a hospital); remove them first")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('hospital_id', existing_type=sa.Integer(), nullable=False)
