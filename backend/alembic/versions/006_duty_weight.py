"""per-teacher duty weight for part-time staff

Revision ID: 006_duty_weight
Revises: 005_hallway_duties
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = '006_duty_weight'
down_revision = '005_hallway_duties'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('teachers', sa.Column('duty_weight', sa.Float(), nullable=False, server_default='1'))
    op.add_column('hallway_duties', sa.Column('weight', sa.Float(), nullable=False, server_default='1'))
    # assignment_count must hold fractional values now that it can accumulate a
    # part-time teacher's duty_weight (e.g. 1.5) instead of always incrementing by 1.
    op.alter_column('assignment_statistics', 'assignment_count',
        existing_type=sa.Integer(), type_=sa.Float(), server_default='0',
        postgresql_using='assignment_count::double precision')

def downgrade():
    op.alter_column('assignment_statistics', 'assignment_count',
        existing_type=sa.Float(), type_=sa.Integer(), server_default='0',
        postgresql_using='round(assignment_count)::integer')
    op.drop_column('hallway_duties', 'weight')
    op.drop_column('teachers', 'duty_weight')
