"""add teacher account password hashes

Revision ID: 003_teacher_accounts
Revises: 002_guard_duty_types
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = '003_teacher_accounts'
down_revision = '002'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('teachers', sa.Column('password_hash', sa.String(length=255), nullable=True))

def downgrade():
    op.drop_column('teachers', 'password_hash')
