from datetime import date, time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Absence, Availability, AssignmentStatistic, ClassGroup, Classroom, Teacher, TimeSlot
from app.services import assign_substitute
@pytest.fixture()
def db():
 e=create_engine('sqlite://');Base.metadata.create_all(e);s=sessionmaker(bind=e)();
 slot=TimeSlot(weekday='Monday',period_number=1,start_time=time(8),end_time=time(9));a=Teacher(first_name='A',last_name='A',email='a@x.test');b=Teacher(first_name='B',last_name='B',email='b@x.test');c=Teacher(first_name='C',last_name='C',email='c@x.test');g=ClassGroup(name='1A');r=Classroom(name='A1');s.add_all([slot,a,b,c,g,r]);s.flush();s.add_all([Availability(teacher_id=b.id,timeslot_id=slot.id),Availability(teacher_id=c.id,timeslot_id=slot.id)]);s.commit();yield s
def absence(db,sub=None):
 x=Absence(date=date.today(),timeslot_id=1,absent_teacher_id=1,class_group_id=1,classroom_id=1,task_left='x',substitute_teacher_id=sub);db.add(x);db.flush();return x
def test_lowest_counter_wins(db):
 db.add(AssignmentStatistic(teacher_id=2,timeslot_id=1,assignment_count=4));db.add(AssignmentStatistic(teacher_id=3,timeslot_id=1,assignment_count=1));db.commit();x=absence(db);assert assign_substitute(db,x)==3
def test_absent_and_occupied_excluded(db):
 x=absence(db,sub=2);db.commit();new=absence(db);assert assign_substitute(db,new)==3
def test_teacher_absent_elsewhere_excluded_even_if_not_this_absences_absent_teacher(db):
 db.add(Absence(date=date.today(),timeslot_id=1,absent_teacher_id=2,class_group_id=1,classroom_id=1,task_left='y'));db.commit();x=absence(db);assert assign_substitute(db,x)==3
def test_no_available_teacher(db):
 db.query(Availability).delete();db.commit();assert assign_substitute(db,absence(db)) is None
def test_counter_created(db):
 chosen=assign_substitute(db,absence(db));db.flush();assert db.get(AssignmentStatistic,{'teacher_id':chosen,'timeslot_id':1}).assignment_count==1
