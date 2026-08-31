"""initial schema"""
from alembic import op
import sqlalchemy as sa
revision, down_revision, branch_labels, depends_on = '001', None, None, None
def upgrade():
    op.create_table('teachers', sa.Column('id',sa.Integer,primary_key=True),sa.Column('first_name',sa.String(80),nullable=False),sa.Column('last_name',sa.String(80),nullable=False),sa.Column('email',sa.String(255),nullable=False,unique=True),sa.Column('active',sa.Boolean,nullable=False,server_default=sa.true()))
    op.create_table('timeslots',sa.Column('id',sa.Integer,primary_key=True),sa.Column('weekday',sa.String(9),nullable=False),sa.Column('period_number',sa.Integer,nullable=False),sa.Column('start_time',sa.Time,nullable=False),sa.Column('end_time',sa.Time,nullable=False),sa.UniqueConstraint('weekday','period_number',name='uq_timeslot_weekday_period'))
    op.create_table('class_groups',sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String(80),nullable=False,unique=True))
    op.create_table('classrooms',sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String(80),nullable=False,unique=True))
    op.create_table('availability',sa.Column('id',sa.Integer,primary_key=True),sa.Column('teacher_id',sa.Integer,sa.ForeignKey('teachers.id',ondelete='CASCADE'),nullable=False),sa.Column('timeslot_id',sa.Integer,sa.ForeignKey('timeslots.id',ondelete='CASCADE'),nullable=False),sa.UniqueConstraint('teacher_id','timeslot_id',name='uq_availability'))
    op.create_table('assignment_statistics',sa.Column('teacher_id',sa.Integer,sa.ForeignKey('teachers.id',ondelete='CASCADE'),primary_key=True),sa.Column('timeslot_id',sa.Integer,sa.ForeignKey('timeslots.id',ondelete='CASCADE'),primary_key=True),sa.Column('assignment_count',sa.Integer,nullable=False,server_default='0'))
    op.create_table('absences',sa.Column('id',sa.Integer,primary_key=True),sa.Column('date',sa.Date,nullable=False),sa.Column('timeslot_id',sa.Integer,sa.ForeignKey('timeslots.id'),nullable=False),sa.Column('absent_teacher_id',sa.Integer,sa.ForeignKey('teachers.id'),nullable=False),sa.Column('class_group_id',sa.Integer,sa.ForeignKey('class_groups.id'),nullable=False),sa.Column('classroom_id',sa.Integer,sa.ForeignKey('classrooms.id'),nullable=False),sa.Column('task_left',sa.Text,nullable=False),sa.Column('observations',sa.Text),sa.Column('substitute_teacher_id',sa.Integer,sa.ForeignKey('teachers.id')),sa.Column('created_at',sa.DateTime,nullable=False,server_default=sa.text('CURRENT_TIMESTAMP')),sa.UniqueConstraint('date','timeslot_id','substitute_teacher_id',name='uq_substitute_per_slot'))
    op.create_index('ix_absences_date_timeslot','absences',['date','timeslot_id'])
def downgrade():
    op.drop_table('absences');op.drop_table('assignment_statistics');op.drop_table('availability');op.drop_table('classrooms');op.drop_table('class_groups');op.drop_table('timeslots');op.drop_table('teachers')
