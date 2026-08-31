"""keep seven sessions per weekday

Revision ID: 004_seven_sessions
Revises: 003_teacher_accounts
Create Date: 2026-08-10
"""
from alembic import op

revision = '004_seven_sessions'
down_revision = '003_teacher_accounts'
branch_labels = None
depends_on = None

def upgrade():
    # Remove dependent records before removing the obsolete eighth and ninth sessions.
    op.execute('DELETE FROM absences WHERE timeslot_id IN (SELECT id FROM timeslots WHERE period_number > 7)')
    op.execute('DELETE FROM assignment_statistics WHERE timeslot_id IN (SELECT id FROM timeslots WHERE period_number > 7)')
    op.execute('DELETE FROM availability WHERE timeslot_id IN (SELECT id FROM timeslots WHERE period_number > 7)')
    op.execute('DELETE FROM timeslots WHERE period_number > 7')

def downgrade():
    # Removed sessions are intentionally not recreated: their timetable is installation-specific.
    pass
