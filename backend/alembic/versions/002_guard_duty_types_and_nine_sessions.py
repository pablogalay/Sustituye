"""guard duty types and sessions 8/9"""
from alembic import op
import sqlalchemy as sa
from datetime import time
revision, down_revision, branch_labels, depends_on = '002', '001', None, None
def upgrade():
    op.add_column('availability',sa.Column('duty_type',sa.String(20),nullable=False,server_default='GUARD'))
    bind=op.get_bind()
    for day in ['Monday','Tuesday','Wednesday','Thursday','Friday']:
        for period in [8,9]:
            bind.execute(sa.text('INSERT INTO timeslots (weekday, period_number, start_time, end_time) VALUES (:day,:period,:start,:end) ON CONFLICT (weekday, period_number) DO NOTHING'),{'day':day,'period':period,'start':time(8+period-1,0),'end':time(8+period-1,50)})
def downgrade():
    op.drop_column('availability','duty_type')
