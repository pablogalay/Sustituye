from datetime import date, datetime, time
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
class Teacher(Base):
    __tablename__='teachers'
    id: Mapped[int]=mapped_column(primary_key=True); first_name: Mapped[str]=mapped_column(String(80)); last_name: Mapped[str]=mapped_column(String(80)); email: Mapped[str]=mapped_column(String(255),unique=True); active: Mapped[bool]=mapped_column(Boolean,default=True); password_hash: Mapped[str|None]=mapped_column(String(255),nullable=True)
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
    __tablename__='assignment_statistics'; teacher_id: Mapped[int]=mapped_column(ForeignKey('teachers.id',ondelete='CASCADE'),primary_key=True); timeslot_id: Mapped[int]=mapped_column(ForeignKey('timeslots.id',ondelete='CASCADE'),primary_key=True); assignment_count: Mapped[int]=mapped_column(Integer,default=0)
class Absence(Base):
    __tablename__='absences'; __table_args__=(UniqueConstraint('date','timeslot_id','substitute_teacher_id'),)
    id: Mapped[int]=mapped_column(primary_key=True); date: Mapped[date]=mapped_column(Date); timeslot_id: Mapped[int]=mapped_column(ForeignKey('timeslots.id')); absent_teacher_id: Mapped[int]=mapped_column(ForeignKey('teachers.id')); class_group_id: Mapped[int]=mapped_column(ForeignKey('class_groups.id')); classroom_id: Mapped[int]=mapped_column(ForeignKey('classrooms.id')); task_left: Mapped[str]=mapped_column(Text); observations: Mapped[str|None]=mapped_column(Text,nullable=True); substitute_teacher_id: Mapped[int|None]=mapped_column(ForeignKey('teachers.id'),nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
