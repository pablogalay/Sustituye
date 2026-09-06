"""Tests for the corridor guard duties that every session must have on top of the
substitutions: the fill order (ground, first and second floor), the fallback to
Support Guards, the priority of substitutions over a corridor post, and the even
spread of the accumulated load."""
from datetime import date, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Absence, Availability, ClassGroup, Classroom, HallwayDuty, Teacher, TimeSlot
from app.services import HALLWAY_POSTS, assign_hallway_duties, assign_substitute, ensure_hallway_duties_for_date

MONDAY = date(2026, 9, 7)
SATURDAY = date(2026, 9, 12)


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([ClassGroup(name='1A'), Classroom(name='A1')])
    yield session
    session.close()


def make_slot(db, period=1, weekday='Monday'):
    slot = TimeSlot(weekday=weekday, period_number=period, start_time=time(8), end_time=time(9))
    db.add(slot)
    db.flush()
    return slot


def make_teachers(db, slot, count, duty_type='GUARD'):
    teachers = [Teacher(first_name=f'T{i}', last_name=f'L{i}', email=f't{i}-{slot.id}-{duty_type}@x.test') for i in range(count)]
    db.add_all(teachers)
    db.flush()
    db.add_all([Availability(teacher_id=t.id, timeslot_id=slot.id, duty_type=duty_type) for t in teachers])
    db.commit()
    return teachers


def test_three_posts_are_created_and_filled_in_order(db):
    slot = make_slot(db)
    teachers = make_teachers(db, slot, 3)

    duties = assign_hallway_duties(db, MONDAY, slot.id)

    assert [x.post for x in duties] == list(HALLWAY_POSTS)
    assert sorted(x.teacher_id for x in duties) == sorted(t.id for t in teachers)


def test_posts_are_filled_in_order_when_teachers_run_out(db):
    slot = make_slot(db)
    make_teachers(db, slot, 2)

    ground, first, second = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)]

    # The ground floor is staffed first and the second floor is the post left empty.
    assert ground is not None and first is not None
    assert second is None


def test_no_teacher_takes_two_posts_in_the_same_session(db):
    slot = make_slot(db)
    make_teachers(db, slot, 1)

    assigned = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id) if x.teacher_id is not None]

    assert len(assigned) == len(set(assigned)) == 1


def test_no_available_teacher_leaves_every_post_empty(db):
    slot = make_slot(db)

    assert [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)] == [None, None, None]


def test_support_guards_cover_the_posts_left_by_the_guards(db):
    slot = make_slot(db)
    guards = make_teachers(db, slot, 1, duty_type='GUARD')
    supports = make_teachers(db, slot, 2, duty_type='SUPPORT')

    assigned = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)]

    assert assigned[0] == guards[0].id
    assert set(assigned[1:]) == {t.id for t in supports}


def test_absent_teacher_is_not_given_a_corridor_post(db):
    slot = make_slot(db)
    teachers = make_teachers(db, slot, 2)
    db.add(Absence(date=MONDAY, timeslot_id=slot.id, absent_teacher_id=teachers[0].id, class_group_id=1, classroom_id=1, task_left='x'))
    db.commit()

    assigned = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)]

    assert assigned == [teachers[1].id, None, None]


def test_teacher_covering_a_class_is_released_from_their_post(db):
    slot = make_slot(db)
    teachers = make_teachers(db, slot, 2)
    assign_hallway_duties(db, MONDAY, slot.id)
    db.commit()

    # An absence arrives afterwards: covering the class wins over the corridor post.
    absence = Absence(date=MONDAY, timeslot_id=slot.id, absent_teacher_id=teachers[0].id, class_group_id=1, classroom_id=1, task_left='x')
    db.add(absence)
    db.flush()
    absence.substitute_teacher_id = assign_substitute(db, absence)
    db.flush()
    assigned = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)]

    assert absence.substitute_teacher_id == teachers[1].id
    assert assigned == [None, None, None]


def test_a_still_free_teacher_keeps_the_post_already_assigned(db):
    slot = make_slot(db)
    make_teachers(db, slot, 3)
    before = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)]
    db.commit()

    assert [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)] == before


def test_the_least_loaded_teacher_takes_the_next_post(db):
    slot = make_slot(db)
    other = make_slot(db, period=2)
    teachers = make_teachers(db, slot, 2)
    db.add_all([Availability(teacher_id=teachers[0].id, timeslot_id=other.id, duty_type='GUARD')])
    # The first teacher already did four corridor duties in earlier sessions.
    db.add_all([HallwayDuty(date=date(2026, 9, i), timeslot_id=other.id, post='GROUND', teacher_id=teachers[0].id) for i in range(1, 5)])
    db.commit()

    assigned = [x.teacher_id for x in assign_hallway_duties(db, MONDAY, slot.id)]

    assert assigned[0] == teachers[1].id


def test_every_session_of_the_day_gets_its_posts(db):
    slots = [make_slot(db, period=period) for period in (1, 2, 3)]
    for slot in slots:
        make_teachers(db, slot, 3)

    duties = ensure_hallway_duties_for_date(db, MONDAY)
    db.commit()

    assert len(duties) == len(slots) * len(HALLWAY_POSTS)
    assert all(x.teacher_id is not None for x in duties)


def test_weekend_days_have_no_corridor_duties(db):
    slot = make_slot(db)
    make_teachers(db, slot, 3)

    assert ensure_hallway_duties_for_date(db, SATURDAY) == []


def test_repeated_generation_does_not_duplicate_posts(db):
    slot = make_slot(db)
    make_teachers(db, slot, 3)
    ensure_hallway_duties_for_date(db, MONDAY)
    db.commit()
    ensure_hallway_duties_for_date(db, MONDAY)
    db.commit()

    assert db.query(HallwayDuty).count() == len(HALLWAY_POSTS)
