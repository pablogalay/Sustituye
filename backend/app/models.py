from datetime import date, datetime, time
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
class Teacher(Base):
    __tablename__='teachers'
    # Multiplies how much each guard duty (substitution or hallway) counts towards this
    # teacher's load-balancing counters. A part-time teacher set above 1 (e.g. 1.5) racks
    # up "load" faster per duty, so the lowest-counter selection picks them less often.
    id: Mapped[int]=mapped_column(primary_key=True); first_name: Mapped[str]=mapped_column(String(80)); last_name: Mapped[str]=mapped_column(String(80)); email: Mapped[str]=mapped_column(String(255),unique=True); active: Mapped[bool]=mapped_column(Boolean,default=True); password_hash: Mapped[str|None]=mapped_column(String(255),nullable=True); duty_weight: Mapped[float]=mapped_column(Float,default=1.0,server_default='1')
class TimeSlot(Base):
    __tablename__='timeslots'; __table_args__=(UniqueConstraint('weekday','period_number'),)
    id: Mapped[int]=mapped_column(primary_key=True); weekday: Mapped[str]=mapped_column(String(9)); period_number: Mapped[int]; start_time: Mapped[time]=mapped_column(Time); end_time: Mapped[time]=mapped_column(Time)
class Availability(Base):
    __tablename__='availability'; __table_args__=(UniqueConstraint('teacher_id','timeslot_id'),)
    id: Mapped[int]=mapped_column(primary_key=True); teacher_id: Mapped[int]=mapped_column(ForeignKey('teachers.id',ondelete='CASCADE')); timeslot_id: Mapped[int]=mapped_column(ForeignKey('timeslots.id',ondelete='CASCADE')); duty_type: Mapped[str]=mapped_column(String(20),default='GUARD',server_default='GUARD')
class ClassGroup(Base):
    __tablename__='class_groups'; id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(80),unique=True)
class Classroom(Base):
    __tablename__='classrooms'; id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(80),unique=True)
class AssignmentStatistic(Base):
    # A float so a part-time teacher's duty_weight (e.g. 1.5) can accumulate here directly.
    __tablename__='assignment_statistics'; teacher_id: Mapped[int]=mapped_column(ForeignKey('teachers.id',ondelete='CASCADE'),primary_key=True); timeslot_id: Mapped[int]=mapped_column(ForeignKey('timeslots.id',ondelete='CASCADE'),primary_key=True); assignment_count: Mapped[float]=mapped_column(Float,default=0.0,server_default='0')
class Absence(Base):
    __tablename__='absences'; __table_args__=(UniqueConstraint('date','timeslot_id','substitute_teacher_id'),)
    id: Mapped[int]=mapped_column(primary_key=True); date: Mapped[date]=mapped_column(Date); timeslot_id: Mapped[int]=mapped_column(ForeignKey('timeslots.id')); absent_teacher_id: Mapped[int]=mapped_column(ForeignKey('teachers.id')); class_group_id: Mapped[int]=mapped_column(ForeignKey('class_groups.id')); classroom_id: Mapped[int]=mapped_column(ForeignKey('classrooms.id')); task_left: Mapped[str]=mapped_column(Text); observations: Mapped[str|None]=mapped_column(Text,nullable=True); substitute_teacher_id: Mapped[int|None]=mapped_column(ForeignKey('teachers.id'),nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class HallwayDuty(Base):
    __tablename__='hallway_duties'; __table_args__=(UniqueConstraint('date','timeslot_id','post'),)
    # weight stores the assigned teacher's duty_weight at assignment time, so the load
    # tally reflects the weight that applied then even if it's changed since.
    id: Mapped[int]=mapped_column(primary_key=True); date: Mapped[date]=mapped_column(Date); timeslot_id: Mapped[int]=mapped_column(ForeignKey('timeslots.id',ondelete='CASCADE')); post: Mapped[str]=mapped_column(String(20)); teacher_id: Mapped[int|None]=mapped_column(ForeignKey('teachers.id'),nullable=True); weight: Mapped[float]=mapped_column(Float,default=1.0,server_default='1')
