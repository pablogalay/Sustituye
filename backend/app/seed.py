from datetime import time
from sqlalchemy import select
from .database import SessionLocal
from .models import Availability, ClassGroup, Classroom, Teacher, TimeSlot
PERIOD_TIMES=[(time(8,25),time(9,20)),(time(9,20),time(10,15)),(time(10,15),time(11,10)),(time(11,40),time(12,35)),(time(12,35),time(13,30)),(time(13,30),time(14,25)),(time(14,40),time(15,35))]
def main():
  db=SessionLocal()
  try:
    if not db.scalar(select(Teacher.id).limit(1)):
      db.add_all([Teacher(first_name=f'Teacher{i}',last_name=f'Example{i:02}',email=f'teacher{i}@school.local') for i in range(1,21)])
    if not db.scalar(select(ClassGroup.id).limit(1)): db.add_all([ClassGroup(name=x) for x in ['1A','1B','2A','2B','3ESO A','3ESO B']])
    if not db.scalar(select(Classroom.id).limit(1)): db.add_all([Classroom(name=x) for x in ['A102','A103','B201','Lab 2','Gym']])
    db.flush()
    days=['Monday','Tuesday','Wednesday','Thursday','Friday']; slots=[]
    for day in days:
      for period in range(1,8):
        if not db.scalar(select(TimeSlot.id).where(TimeSlot.weekday==day,TimeSlot.period_number==period)):
          start,end=PERIOD_TIMES[period-1]
          slots.append(TimeSlot(weekday=day,period_number=period,start_time=start,end_time=end))
    db.add_all(slots); db.flush(); slots=db.scalars(select(TimeSlot)).all(); teachers=db.scalars(select(Teacher)).all()
    for t in teachers:
      for s in slots:
        if (t.id+s.id)%4 and not db.scalar(select(Availability.id).where(Availability.teacher_id==t.id,Availability.timeslot_id==s.id)): db.add(Availability(teacher_id=t.id,timeslot_id=s.id,duty_type='GUARD' if (t.id+s.id)%5 else 'SUPPORT'))
    db.commit()
  finally: db.close()
if __name__=='__main__': main()
