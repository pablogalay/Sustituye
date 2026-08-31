"""seed.main() is designed to be safely re-run (it only inserts rows that are
missing); this verifies that idempotency directly instead of relying on it
being exercised incidentally by other tests, which only ever run it once
against an empty database."""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///./test_seed_unused.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import seed as seed_module
from app.database import Base
from app.models import Availability, ClassGroup, Classroom, Teacher, TimeSlot


def test_seed_main_is_idempotent(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    isolated_session_local = sessionmaker(bind=engine)
    monkeypatch.setattr(seed_module, 'SessionLocal', isolated_session_local)

    seed_module.main()
    with isolated_session_local() as db:
        teachers_after_first_run = db.scalars(select(Teacher)).all()
        groups_after_first_run = db.scalars(select(ClassGroup)).all()
        timeslots_after_first_run = db.scalars(select(TimeSlot)).all()
        availability_after_first_run = db.scalars(select(Availability)).all()
    assert len(teachers_after_first_run) == 20
    assert len(timeslots_after_first_run) == 35  # 5 weekdays x 7 periods
    assert len(availability_after_first_run) > 0

    seed_module.main()  # re-running must not duplicate anything
    with isolated_session_local() as db:
        assert [t.email for t in db.scalars(select(Teacher)).all()] == [t.email for t in teachers_after_first_run]
        assert len(db.scalars(select(ClassGroup)).all()) == len(groups_after_first_run)
        assert len(db.scalars(select(TimeSlot)).all()) == len(timeslots_after_first_run)
        assert len(db.scalars(select(Availability)).all()) == len(availability_after_first_run)
