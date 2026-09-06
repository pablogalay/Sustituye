"""Tests for the per-teacher duty_weight used to balance guard duties (substitutions and
corridor posts) fairly when some teachers work part-time. Instead of a flat +1 per duty,
the load counters increase by the assigned teacher's duty_weight, so a part-time teacher
configured above 1 accumulates load faster per duty and is chosen less often than a
full-time colleague with the same number of actual duties."""
from datetime import date, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Absence, AssignmentStatistic, Availability, HallwayDuty, Teacher, TimeSlot
from app.services import assign_hallway_duties, assign_substitute, release_substitute_credit

MONDAY = date(2026, 9, 7)


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_slot(db):
    slot = TimeSlot(weekday='Monday', period_number=1, start_time=time(8), end_time=time(9))
    db.add(slot)
    db.flush()
    return slot


def absence_for(db, slot, absent_id):
    x = Absence(date=MONDAY, timeslot_id=slot.id, absent_teacher_id=absent_id, class_group_id=1, classroom_id=1, task_left='x')
    db.add(x)
    db.flush()
    return x


# --- default weight preserves the previous +1 behaviour ----------------------


def test_default_duty_weight_is_one(db):
    teacher = Teacher(first_name='A', last_name='A', email='a@x.test')
    db.add(teacher)
    db.flush()
    assert teacher.duty_weight == 1.0


# --- assign_substitute increments by the chosen teacher's weight -------------


def test_assignment_counter_increases_by_the_teachers_duty_weight(db):
    slot = make_slot(db)
    absent = Teacher(first_name='Absent', last_name='One', email='absent@x.test')
    part_time = Teacher(first_name='Part', last_name='Time', email='part@x.test', duty_weight=1.5)
    db.add_all([absent, part_time])
    db.flush()
    db.add(Availability(teacher_id=part_time.id, timeslot_id=slot.id, duty_type='GUARD'))
    db.commit()

    absence = absence_for(db, slot, absent.id)
    assert assign_substitute(db, absence) == part_time.id

    stat = db.get(AssignmentStatistic, (part_time.id, slot.id))
    assert stat.assignment_count == 1.5


def test_full_time_teacher_still_counts_as_one(db):
    slot = make_slot(db)
    absent = Teacher(first_name='Absent', last_name='One', email='absent2@x.test')
    full_time = Teacher(first_name='Full', last_name='Time', email='full@x.test')
    db.add_all([absent, full_time])
    db.flush()
    db.add(Availability(teacher_id=full_time.id, timeslot_id=slot.id, duty_type='GUARD'))
    db.commit()

    absence = absence_for(db, slot, absent.id)
    assign_substitute(db, absence)

    assert db.get(AssignmentStatistic, (full_time.id, slot.id)).assignment_count == 1.0


def test_part_timer_with_same_duty_count_is_deprioritized_over_full_timer(db):
    """Both teachers have covered this slot twice before; the part-time teacher's counter
    (2 x 1.5) is now higher than the full-timer's (2 x 1.0), so the next pick goes to the
    full-timer even though they have done the exact same number of real duties so far."""
    slot = make_slot(db)
    absent = Teacher(first_name='Absent', last_name='One', email='absent3@x.test')
    part_time = Teacher(first_name='Part', last_name='Time', email='pt@x.test', duty_weight=1.5)
    full_time = Teacher(first_name='Full', last_name='Time', email='ft@x.test')
    db.add_all([absent, part_time, full_time])
    db.flush()
    db.add_all([
        Availability(teacher_id=part_time.id, timeslot_id=slot.id, duty_type='GUARD'),
        Availability(teacher_id=full_time.id, timeslot_id=slot.id, duty_type='GUARD'),
        AssignmentStatistic(teacher_id=part_time.id, timeslot_id=slot.id, assignment_count=3.0),  # 2 duties x 1.5
        AssignmentStatistic(teacher_id=full_time.id, timeslot_id=slot.id, assignment_count=2.0),  # 2 duties x 1.0
    ])
    db.commit()

    absence = absence_for(db, slot, absent.id)
    assert assign_substitute(db, absence) == full_time.id


# --- release_substitute_credit undoes exactly the weight that was added -----


def test_release_substitute_credit_subtracts_the_teachers_weight(db):
    slot = make_slot(db)
    teacher = Teacher(first_name='Part', last_name='Time', email='release@x.test', duty_weight=1.5)
    db.add(teacher)
    db.flush()
    db.add(AssignmentStatistic(teacher_id=teacher.id, timeslot_id=slot.id, assignment_count=1.5))
    db.commit()

    release_substitute_credit(db, teacher.id, slot.id)

    assert db.get(AssignmentStatistic, (teacher.id, slot.id)).assignment_count == 0.0


def test_release_substitute_credit_does_not_go_negative(db):
    slot = make_slot(db)
    teacher = Teacher(first_name='Part', last_name='Time', email='release2@x.test', duty_weight=1.5)
    db.add(teacher)
    db.flush()
    db.add(AssignmentStatistic(teacher_id=teacher.id, timeslot_id=slot.id, assignment_count=0.5))
    db.commit()

    release_substitute_credit(db, teacher.id, slot.id)

    assert db.get(AssignmentStatistic, (teacher.id, slot.id)).assignment_count == 0.0


def test_release_substitute_credit_is_a_noop_without_an_existing_statistic(db):
    slot = make_slot(db)
    teacher = Teacher(first_name='Nobody', last_name='Yet', email='nobody@x.test')
    db.add(teacher)
    db.commit()

    release_substitute_credit(db, teacher.id, slot.id)  # must not raise

    assert db.get(AssignmentStatistic, (teacher.id, slot.id)) is None


# --- hallway duty load is weighted the same way -------------------------------


def test_hallway_duty_records_the_assigned_teachers_weight(db):
    slot = make_slot(db)
    teacher = Teacher(first_name='Part', last_name='Time', email='hallway@x.test', duty_weight=1.5)
    db.add(teacher)
    db.flush()
    db.add(Availability(teacher_id=teacher.id, timeslot_id=slot.id, duty_type='GUARD'))
    db.commit()

    duties = assign_hallway_duties(db, MONDAY, slot.id)

    ground = next(d for d in duties if d.post == 'GROUND')
    assert ground.teacher_id == teacher.id
    assert ground.weight == 1.5


def test_part_timer_with_same_corridor_duty_count_is_deprioritized(db):
    """Both teachers have already covered two corridor posts (recorded with their own
    weight); the part-timer's weighted total is higher, so the next open post goes to the
    full-timer despite an equal number of real corridor duties so far."""
    slot = make_slot(db)
    other_slot = TimeSlot(weekday='Monday', period_number=2, start_time=time(9), end_time=time(10))
    db.add(other_slot)
    db.flush()
    part_time = Teacher(first_name='Part', last_name='Time', email='pt2@x.test', duty_weight=1.5)
    full_time = Teacher(first_name='Full', last_name='Time', email='ft2@x.test')
    db.add_all([part_time, full_time])
    db.flush()
    db.add_all([
        Availability(teacher_id=part_time.id, timeslot_id=slot.id, duty_type='GUARD'),
        Availability(teacher_id=full_time.id, timeslot_id=slot.id, duty_type='GUARD'),
        # Two prior corridor duties each, recorded on a different slot so they don't
        # collide with this session's own posts.
        HallwayDuty(date=MONDAY, timeslot_id=other_slot.id, post='GROUND', teacher_id=part_time.id, weight=1.5),
        HallwayDuty(date=MONDAY, timeslot_id=other_slot.id, post='FIRST', teacher_id=part_time.id, weight=1.5),
        HallwayDuty(date=MONDAY, timeslot_id=other_slot.id, post='SECOND', teacher_id=full_time.id, weight=1.0),
    ])
    db.flush()
    db.add(HallwayDuty(date=date(2026, 9, 8), timeslot_id=other_slot.id, post='GROUND', teacher_id=full_time.id, weight=1.0))
    db.commit()

    duties = assign_hallway_duties(db, MONDAY, slot.id)

    ground = next(d for d in duties if d.post == 'GROUND')
    assert ground.teacher_id == full_time.id
