import io, json
from datetime import date, datetime, time
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session
from .database import get_db
from .models import Absence, AssignmentStatistic, Availability, ClassGroup, Classroom, Teacher, TimeSlot

router = APIRouter(prefix='/backup', tags=['backup'])

# Parent -> child order (respects foreign keys for INSERT). Deletion during
# import runs this list in reverse so children are cleared before the parents
# they reference, avoiding FK violations without needing to disable
# constraints (which typically requires elevated Postgres privileges).
TABLES = [
    ('class_groups', ClassGroup),
    ('classrooms', Classroom),
    ('teachers', Teacher),
    ('timeslots', TimeSlot),
    ('availability', Availability),
    ('assignment_statistics', AssignmentStatistic),
    ('absences', Absence),
]


def require_admin(request: Request):
    if request.state.user.get('role') != 'admin': raise HTTPException(403, 'Administrator permission required')


def _serialize_value(value):
    return value.isoformat() if isinstance(value, (date, datetime, time)) else value


def _serialize_row(model, row):
    return {column.name: _serialize_value(getattr(row, column.name)) for column in model.__table__.columns}


def _deserialize_value(column, value):
    if value is None: return None
    python_type = column.type.python_type
    if python_type in (date, datetime, time) and isinstance(value, str): return python_type.fromisoformat(value)
    return value


@router.get('/export')
def export_database(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    payload = {
        'version': 1,
        'exported_at': datetime.utcnow().isoformat(),
        'tables': {name: [_serialize_row(model, row) for row in db.query(model).all()] for name, model in TABLES},
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')
    filename = f"backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    return StreamingResponse(io.BytesIO(body), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@router.post('/import')
async def import_database(request: Request, db: Session = Depends(get_db), file: UploadFile = File(...)):
    require_admin(request)
    try:
        payload = json.loads(await file.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(422, 'El archivo no es un JSON válido')
    tables = payload.get('tables') if isinstance(payload, dict) else None
    if not isinstance(tables, dict):
        raise HTTPException(422, 'El archivo no tiene el formato de copia de seguridad esperado')

    try:
        for name, model in reversed(TABLES):
            db.query(model).delete(synchronize_session=False)
        for name, model in TABLES:
            rows = tables.get(name) or []
            if not isinstance(rows, list): raise ValueError(f'"{name}" debe ser una lista')
            columns = model.__table__.columns
            mappings = [{c.name: _deserialize_value(c, row.get(c.name)) for c in columns if c.name in row} for row in rows]
            if mappings: db.bulk_insert_mappings(model, mappings)
        if db.bind.dialect.name == 'postgresql':
            for name, model in TABLES:
                primary_key = model.__table__.primary_key.columns.keys()
                if primary_key == ['id']:
                    db.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), COALESCE((SELECT MAX(id) FROM {name}), 1), (SELECT MAX(id) FROM {name}) IS NOT NULL)"
                    ))
    except (IntegrityError, DataError, KeyError, ValueError, TypeError, AttributeError) as error:
        db.rollback()
        raise HTTPException(422, f'No se pudo restaurar la copia de seguridad: datos no válidos o incompletos ({error})')

    db.commit()
    return {'ok': True, 'tables_restored': {name: len(tables.get(name) or []) for name, _ in TABLES}}
