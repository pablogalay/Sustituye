"""corridor guard duties (ground, first and second floor) per session

Revision ID: 005_hallway_duties
Revises: 004_seven_sessions
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = '005_hallway_duties'
down_revision = '004_seven_sessions'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('hallway_duties',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('date', sa.Date, nullable=False),
        sa.Column('timeslot_id', sa.Integer, sa.ForeignKey('timeslots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('post', sa.String(length=20), nullable=False),
        sa.Column('teacher_id', sa.Integer, sa.ForeignKey('teachers.id')),
        sa.UniqueConstraint('date', 'timeslot_id', 'post', name='uq_hallway_duty_post'))
    op.create_index('ix_hallway_duties_date_timeslot', 'hallway_duties', ['date', 'timeslot_id'])

def downgrade():
    op.drop_index('ix_hallway_duties_date_timeslot', table_name='hallway_duties')
    op.drop_table('hallway_duties')
