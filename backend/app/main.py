import os
from datetime import date
from types import SimpleNamespace
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph, PageBreak
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .auth import hash_password, login, verify
from .backup import router as backup_router
from .database import get_db
from .models import Absence, AssignmentStatistic, Availability, ClassGroup, Classroom, EmailLog, HallwayDuty, Teacher, TimeSlot
from .schemas import AbsenceIn, AbsenceManualAssignIn, AbsenceOut, AbsenceUpdateIn, AdminAbsenceUpdateIn, AvailabilityIn, EmailLogOut, LoginIn, TeacherIn, TeacherOut, TimeSlotOut
from .services import HALLWAY_POST_LABELS, HALLWAY_POSTS, assign_hallway_duties, assign_substitute, credit_manual_assignment, ensure_hallway_duties_for_date, release_substitute_credit, send_absence_updated_email, send_substitution_cancelled_email, send_substitution_email
app=FastAPI(title='Teacher Substitution Manager')
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:3000','http://localhost:5173'],allow_methods=['*'],allow_headers=['*'])
app.include_router(backup_router)
@app.middleware('http')
async def administrator_only(request:Request,call_next):
    if request.url.path in {'/auth/login','/docs','/openapi.json','/redoc'} or request.method=='OPTIONS': return await call_next(request)
    header=request.headers.get('authorization','')
    if not header.startswith('Bearer '): return JSONResponse(status_code=401,content={'detail':'Authentication required'})
    try: request.state.user=verify(header[7:])
    except HTTPException as error: return JSONResponse(status_code=error.status_code,content={'detail':error.detail})
    return await call_next(request)
@app.post('/auth/login')
def authenticate(payload:LoginIn,db:Session=Depends(get_db)): return login(payload.email,payload.password,db)
@app.get('/auth/me')
def current_user(request:Request): return request.state.user
def require_admin(request:Request):
    if request.state.user.get('role') != 'admin': raise HTTPException(403,'Administrator permission required')
@app.get('/teachers',response_model=list[TeacherOut])
def teachers(request:Request,db:Session=Depends(get_db)):
    return db.scalars(select(Teacher).order_by(Teacher.last_name)).all()
@app.post('/teachers',response_model=TeacherOut,status_code=201)
def create_teacher(payload:TeacherIn,request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    if not payload.password or len(payload.password) < 8: raise HTTPException(422,'A teacher password must contain at least 8 characters')
    values=payload.model_dump(exclude={'password'})
    item=Teacher(**values,password_hash=hash_password(payload.password) if payload.password else None); db.add(item); db.commit(); db.refresh(item); return item
@app.put('/teachers/{teacher_id}',response_model=TeacherOut)
def update_teacher(teacher_id:int,payload:TeacherIn,request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    item=db.get(Teacher,teacher_id)
    if not item: raise HTTPException(404,'Teacher not found')
    for key,value in payload.model_dump(exclude={'password'}).items(): setattr(item,key,value)
    if payload.password:
        if len(payload.password) < 8: raise HTTPException(422,'A teacher password must contain at least 8 characters')
        item.password_hash=hash_password(payload.password)
    db.commit(); return item
@app.delete('/teachers/{teacher_id}',status_code=204)
def delete_teacher(teacher_id:int,request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    item=db.get(Teacher,teacher_id)
    if not item: raise HTTPException(404,'Teacher not found')
    related_absences=db.scalar(select(Absence.id).where(or_(Absence.absent_teacher_id==teacher_id,Absence.substitute_teacher_id==teacher_id)))
    if related_absences:
        raise HTTPException(409,'No se puede eliminar este profesor porque tiene ausencias o sustituciones asociadas')
    db.query(Availability).filter(Availability.teacher_id==teacher_id).delete(synchronize_session=False)
    db.query(AssignmentStatistic).filter(AssignmentStatistic.teacher_id==teacher_id).delete(synchronize_session=False)
    db.query(HallwayDuty).filter(HallwayDuty.teacher_id==teacher_id).delete(synchronize_session=False)
    db.delete(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'No se puede eliminar este profesor porque tiene registros relacionados')

@app.delete('/absences/{absence_id}',status_code=204)
def delete_absence(absence_id:int,request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    item=db.get(Absence,absence_id)
    if not item: raise HTTPException(404,'Absence not found')
    day,timeslot_id,substitute_teacher_id=item.date,item.timeslot_id,item.substitute_teacher_id
    # Snapshot the details the cancellation email needs before the row is gone.
    group=db.get(ClassGroup,item.class_group_id); classroom=db.get(Classroom,item.classroom_id)
    cancelled=SimpleNamespace(id=item.id,date=item.date,timeslot_id=item.timeslot_id,absent_teacher_id=item.absent_teacher_id,
        class_group=group.name if group else None,classroom=classroom.name if classroom else None)
    db.delete(item)
    db.flush()
    # The substitution no longer exists, so the credit it earned the substitute
    # teacher toward the weighted assignment counters must be given back.
    if substitute_teacher_id is not None: release_substitute_credit(db,substitute_teacher_id,timeslot_id)
    # The teacher who was absent (or covering this class) is free again, so the
    # corridor posts of that session are staffed once more with everyone available.
    assign_hallway_duties(db,day,timeslot_id)
    db.commit()
    if substitute_teacher_id is not None: send_substitution_cancelled_email(db,cancelled,substitute_teacher_id)
@app.get('/timeslots',response_model=list[TimeSlotOut])
def timeslots(db:Session=Depends(get_db)):
    weekdays=case({'Monday':1,'Tuesday':2,'Wednesday':3,'Thursday':4,'Friday':5},value=TimeSlot.weekday,else_=6)
    return db.scalars(select(TimeSlot).order_by(weekdays,TimeSlot.period_number)).all()
@app.get('/groups')
def groups(db:Session=Depends(get_db)): return db.scalars(select(ClassGroup).order_by(ClassGroup.name)).all()
@app.get('/classrooms')
def classrooms(db:Session=Depends(get_db)): return db.scalars(select(Classroom).order_by(Classroom.name)).all()
@app.get('/availability')
def availability(db:Session=Depends(get_db)): return [{'teacher_id':x.teacher_id,'timeslot_id':x.timeslot_id,'duty_type':x.duty_type} for x in db.scalars(select(Availability)).all()]
@app.put('/availability')
def set_availability(payload:AvailabilityIn,request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    if not db.get(Teacher,payload.teacher_id): raise HTTPException(404,'Teacher not found')
    valid_slots=set(db.scalars(select(TimeSlot.id)).all())
    if any(x.timeslot_id not in valid_slots or x.duty_type not in {'GUARD','SUPPORT'} for x in payload.entries): raise HTTPException(422,'Invalid duty availability')
    db.query(Availability).filter_by(teacher_id=payload.teacher_id).delete()
    db.add_all([Availability(teacher_id=payload.teacher_id,timeslot_id=x.timeslot_id,duty_type=x.duty_type) for x in payload.entries]); db.commit(); return {'ok':True}
@app.post('/absences',response_model=AbsenceOut,status_code=201)
def create_absence(payload:AbsenceIn,request:Request,db:Session=Depends(get_db)):
    user=request.state.user
    if user.get('role') == 'teacher' and payload.absent_teacher_id != user.get('teacher_id'): raise HTTPException(403,'Teachers can only register their own absence')
    group_name=payload.class_group.strip(); classroom_name=payload.classroom.strip()
    if not group_name or not classroom_name: raise HTTPException(422,'Group and classroom are required')
    group=db.scalar(select(ClassGroup).where(ClassGroup.name==group_name))
    if not group: group=ClassGroup(name=group_name); db.add(group); db.flush()
    classroom=db.scalar(select(Classroom).where(Classroom.name==classroom_name))
    if not classroom: classroom=Classroom(name=classroom_name); db.add(classroom); db.flush()
    duplicate=db.scalar(select(Absence).where(
        Absence.date==payload.date,
        Absence.timeslot_id==payload.timeslot_id,
        Absence.absent_teacher_id==payload.absent_teacher_id,
        Absence.class_group_id==group.id,
        Absence.classroom_id==classroom.id,
        Absence.task_left==payload.task_left,
        Absence.observations==payload.observations,
    ))
    if duplicate: raise HTTPException(409,'Ya existe una sustitución idéntica registrada')
    absence=Absence(date=payload.date,timeslot_id=payload.timeslot_id,absent_teacher_id=payload.absent_teacher_id,class_group_id=group.id,classroom_id=classroom.id,task_left=payload.task_left,observations=payload.observations)
    db.add(absence); db.flush()

    # This teacher may already be covering someone else's absence in this same
    # slot; since they're now absent too, that duty must move to someone else.
    # Resolved before picking a substitute for the new absence itself so it
    # doesn't compete with (and starve) this higher-priority reassignment.
    conflicting=db.scalars(select(Absence).where(
        Absence.date==payload.date,
        Absence.timeslot_id==payload.timeslot_id,
        Absence.substitute_teacher_id==payload.absent_teacher_id,
        Absence.id!=absence.id,
    )).all()
    for other in conflicting:
        release_substitute_credit(db,payload.absent_teacher_id,payload.timeslot_id)
        other.substitute_teacher_id=None
        db.flush()
        other.substitute_teacher_id=assign_substitute(db,other)
        db.flush()

    absence.substitute_teacher_id=assign_substitute(db,absence)
    db.flush()
    # Covering a class always wins over a corridor post, so the posts of this
    # session are recomputed only after every substitution has been resolved.
    assign_hallway_duties(db,payload.date,payload.timeslot_id)
    db.commit(); db.refresh(absence)
    if absence.substitute_teacher_id is not None:
        send_substitution_email(db, absence, absence.substitute_teacher_id)
    for other in conflicting:
        db.refresh(other)
        if other.substitute_teacher_id is not None:
            send_substitution_email(db, other, other.substitute_teacher_id)
    return absence
@app.patch('/absences/{absence_id}/force-assign',response_model=AbsenceOut)
def force_assign_absence(absence_id:int,payload:AbsenceManualAssignIn,request:Request,db:Session=Depends(get_db)):
    """Lets an administrator force-assign ANY active teacher to a pending absence,
    bypassing GUARD/SUPPORT availability entirely: classes must be covered in an
    emergency even when nobody signed up for guard duty in that session. The weighted
    duty counter is still credited exactly like an automatic assignment, so the extra
    effort is remembered and the engine leans on this teacher less often afterwards.

    If the chosen teacher is already covering a different absence in this same
    date/session, that older assignment is released (its credit reversed) so nothing
    is double-booked, and the automatic engine gets one attempt to backfill it before
    it's left pending."""
    require_admin(request)
    absence=db.get(Absence,absence_id)
    if not absence: raise HTTPException(404,'Absence not found')
    if absence.substitute_teacher_id is not None: raise HTTPException(400,'Esta ausencia ya tiene una sustitución asignada')
    substitute=db.get(Teacher,payload.substitute_id)
    if not substitute: raise HTTPException(404,'Teacher not found')
    if not substitute.active: raise HTTPException(422,'El profesor sustituto no está activo')
    if substitute.id==absence.absent_teacher_id: raise HTTPException(422,'El profesor ausente no puede sustituirse a sí mismo')
    if db.scalar(select(Absence.id).where(Absence.date==absence.date,Absence.timeslot_id==absence.timeslot_id,Absence.absent_teacher_id==substitute.id)):
        raise HTTPException(422,'Ese profesor también está ausente en esta sesión y no puede cubrirla')

    # The chosen teacher may already be covering a different absence in this same
    # date/session; forcing them onto this one too would silently double their
    # commitment, so that older assignment is released first (and, best-effort, refilled).
    conflicting=db.scalar(select(Absence).where(
        Absence.date==absence.date, Absence.timeslot_id==absence.timeslot_id,
        Absence.substitute_teacher_id==substitute.id, Absence.id!=absence.id,
    ))
    # Released and flushed FIRST, freeing `substitute` from the DB's uniqueness
    # constraint (date, timeslot, substitute) before `absence` claims that same slot below.
    if conflicting:
        release_substitute_credit(db,substitute.id,absence.timeslot_id)
        conflicting.substitute_teacher_id=None
        db.flush()

    # Assigned and flushed BEFORE the backfill below runs: assign_substitute()'s own
    # "occupied" check reads Absence.substitute_teacher_id directly, so `substitute`
    # must already show up there as covering `absence`, or the backfill could pick
    # them straight back onto `conflicting` too, double-booking the same teacher into
    # both absences at this exact date/session.
    absence.substitute_teacher_id=substitute.id
    db.flush()

    if conflicting:
        conflicting.substitute_teacher_id=assign_substitute(db,conflicting)
        db.flush()

    # Unconditional: credit_manual_assignment() doesn't check GUARD/SUPPORT availability,
    # which is exactly the bypass this endpoint requires.
    credit_manual_assignment(db,substitute.id,absence.timeslot_id)
    db.flush()
    # Covering a class always wins over a corridor post, so the posts of this
    # session are recomputed in case the substitute (or whoever backfills the
    # freed-up absence below) was staffing one.
    assign_hallway_duties(db,absence.date,absence.timeslot_id)
    db.commit(); db.refresh(absence)
    send_substitution_email(db,absence,substitute.id)
    if conflicting:
        db.refresh(conflicting)
        if conflicting.substitute_teacher_id is not None:
            send_substitution_email(db,conflicting,conflicting.substitute_teacher_id)
    return absence
@app.get('/sessions/{day}/{timeslot_id}/teachers-status')
def teachers_status(day:date,timeslot_id:int,request:Request,db:Session=Depends(get_db)):
    """Every active teacher's status for one date/session, so an administrator
    force-assigning a substitute can see at a glance who already has guard/support
    duty there, who's already committed elsewhere, and who is simply free to draft."""
    require_admin(request)
    if not db.get(TimeSlot,timeslot_id): raise HTTPException(404,'Session not found')
    duty_by_teacher={x.teacher_id:x.duty_type for x in db.scalars(select(Availability).where(Availability.timeslot_id==timeslot_id))}
    absent_ids=set(db.scalars(select(Absence.absent_teacher_id).where(Absence.date==day,Absence.timeslot_id==timeslot_id)))
    busy_ids=set(db.scalars(select(Absence.substitute_teacher_id).where(Absence.date==day,Absence.timeslot_id==timeslot_id,Absence.substitute_teacher_id.is_not(None))))
    def status_of(teacher_id:int)->str:
        if teacher_id in absent_ids: return 'ABSENT'
        if teacher_id in busy_ids: return 'BUSY'
        duty=duty_by_teacher.get(teacher_id)
        return duty if duty in ('GUARD','SUPPORT') else 'UNAVAILABLE'
    teachers=db.scalars(select(Teacher).where(Teacher.active.is_(True)).order_by(Teacher.last_name)).all()
    return [{'id':t.id,'first_name':t.first_name,'last_name':t.last_name,'status':status_of(t.id)} for t in teachers]
@app.patch('/absences/{absence_id}',response_model=AbsenceOut)
def update_absence(absence_id:int,payload:AbsenceUpdateIn,request:Request,db:Session=Depends(get_db)):
    """Lets the teacher who reported an absence (or an administrator) correct the task
    left for the group or the classroom after the fact — for example because they
    forgot part of the instructions. date/timeslot/class_group are deliberately not
    editable: changing any of them would mean recalculating availability and the
    assigned substitute, so the teacher is expected to delete and recreate the
    absence instead. If a substitute is already assigned, they are re-notified so
    they don't show up with the outdated task or classroom."""
    user=request.state.user
    absence=db.get(Absence,absence_id)
    if not absence: raise HTTPException(404,'Absence not found')
    if user.get('role')=='teacher' and absence.absent_teacher_id!=user.get('teacher_id'):
        raise HTTPException(403,'Solo puedes editar tus propias ausencias')
    if absence.date<date.today():
        raise HTTPException(409,'No se puede editar una ausencia de una fecha pasada')
    task_left=payload.task_left.strip() if payload.task_left is not None else None
    classroom_name=payload.classroom.strip() if payload.classroom is not None else None
    if task_left is None and classroom_name is None:
        raise HTTPException(422,'Debes indicar la tarea o el aula a modificar')
    if task_left is not None:
        if not task_left: raise HTTPException(422,'La tarea no puede estar vacía')
        absence.task_left=task_left
    if classroom_name is not None:
        if not classroom_name: raise HTTPException(422,'El aula no puede estar vacía')
        classroom=db.scalar(select(Classroom).where(Classroom.name==classroom_name))
        if not classroom: classroom=Classroom(name=classroom_name); db.add(classroom); db.flush()
        absence.classroom_id=classroom.id
    db.commit(); db.refresh(absence)
    if absence.substitute_teacher_id is not None:
        send_absence_updated_email(db,absence,absence.substitute_teacher_id)
    return absence
@app.put('/absences/{absence_id}',response_model=AbsenceOut)
def admin_update_absence(absence_id:int,payload:AdminAbsenceUpdateIn,request:Request,db:Session=Depends(get_db)):
    """Full administrative edit: date, session, group, classroom, task, observations and
    the assigned substitute can all change in one action. Unlike the teacher-facing PATCH,
    this bypasses GUARD/SUPPORT availability entirely (like force-assign) and allows
    editing absences in the past, since it's a correction tool for administrators.

    Keeps the weighted duty counters consistent across the edit ('el baile de
    contadores'):
      1. RELEASE — if the substitute or the date/session changes, whoever was assigned
         before is released from their OLD (teacher, timeslot) counter and told their
         assignment was cancelled, describing what they had originally signed up for.
      2. UPDATE — the absence's fields are overwritten with the new values.
      3. ASSIGN/NOTIFY — a genuinely new assignment (new substitute, or the same
         substitute on a new date/session) credits the NEW counter and sends a
         "new assignment" email; an unchanged substitute on an unchanged date/session
         only gets a "details updated" email if the task/classroom/group/observations
         changed. A no-op save (nothing actually different) sends nothing.
    Every mutation from here through the commit is wrapped so a failure rolls back
    instead of leaving a half-applied edit with corrupted counters.
    """
    require_admin(request)
    absence=db.get(Absence,absence_id)
    if not absence: raise HTTPException(404,'Absence not found')
    if not db.get(TimeSlot,payload.timeslot_id): raise HTTPException(404,'Session not found')

    group_name=payload.class_group.strip(); classroom_name=payload.classroom.strip(); task_left=payload.task_left.strip()
    if not group_name or not classroom_name or not task_left: raise HTTPException(422,'Group, classroom and task are required')

    new_substitute=None
    if payload.substitute_id is not None:
        new_substitute=db.get(Teacher,payload.substitute_id)
        if not new_substitute: raise HTTPException(404,'Substitute teacher not found')
        if not new_substitute.active: raise HTTPException(422,'El profesor sustituto no está activo')
        if new_substitute.id==absence.absent_teacher_id: raise HTTPException(422,'El profesor ausente no puede sustituirse a sí mismo')
        if db.scalar(select(Absence.id).where(Absence.date==payload.date,Absence.timeslot_id==payload.timeslot_id,Absence.absent_teacher_id==new_substitute.id)):
            raise HTTPException(422,'Ese profesor también está ausente en esa sesión y no puede cubrirla')

    # Snapshot everything the cancellation email needs before anything is mutated: it
    # must describe what the previous substitute originally signed up to cover, not the
    # (possibly different) new date/session/group/classroom.
    old_date,old_timeslot_id,old_substitute_id=absence.date,absence.timeslot_id,absence.substitute_teacher_id
    old_group=db.get(ClassGroup,absence.class_group_id); old_classroom=db.get(Classroom,absence.classroom_id)
    old_snapshot=SimpleNamespace(id=absence.id,date=old_date,timeslot_id=old_timeslot_id,absent_teacher_id=absence.absent_teacher_id,
        class_group=old_group.name if old_group else None,classroom=old_classroom.name if old_classroom else None)

    session_changed=payload.date!=old_date or payload.timeslot_id!=old_timeslot_id
    substitute_changed=payload.substitute_id!=old_substitute_id
    details_changed=(group_name!=old_snapshot.class_group or classroom_name!=old_snapshot.classroom
        or task_left!=absence.task_left or payload.observations!=absence.observations)
    release_needed=old_substitute_id is not None and (substitute_changed or session_changed)
    assign_needed=new_substitute is not None and (substitute_changed or session_changed)

    try:
        group=db.scalar(select(ClassGroup).where(ClassGroup.name==group_name))
        if not group: group=ClassGroup(name=group_name); db.add(group); db.flush()
        classroom=db.scalar(select(Classroom).where(Classroom.name==classroom_name))
        if not classroom: classroom=Classroom(name=classroom_name); db.add(classroom); db.flush()

        duplicate=db.scalar(select(Absence).where(
            Absence.id!=absence.id, Absence.date==payload.date, Absence.timeslot_id==payload.timeslot_id,
            Absence.absent_teacher_id==absence.absent_teacher_id, Absence.class_group_id==group.id,
            Absence.classroom_id==classroom.id, Absence.task_left==task_left, Absence.observations==payload.observations,
        ))
        if duplicate: raise HTTPException(409,'Ya existe una sustitución idéntica registrada')

        # The new substitute may already be covering a different absence in the new
        # date/session; assigning them here too would silently double their commitment,
        # so that older assignment is released first (and, best-effort, backfilled).
        # Released and flushed FIRST, freeing `new_substitute` from the DB's uniqueness
        # constraint (date, timeslot, substitute) before `absence` claims that slot below.
        conflicting=None
        if assign_needed:
            conflicting=db.scalar(select(Absence).where(
                Absence.date==payload.date, Absence.timeslot_id==payload.timeslot_id,
                Absence.substitute_teacher_id==new_substitute.id, Absence.id!=absence.id,
            ))
            if conflicting:
                release_substitute_credit(db,new_substitute.id,payload.timeslot_id)
                conflicting.substitute_teacher_id=None
                db.flush()

        # UPDATE applied and flushed BEFORE the conflict backfill below: assign_substitute()'s
        # own "occupied" check reads Absence.substitute_teacher_id/date/timeslot_id directly,
        # so `absence` must already show the NEW date/session/substitute here, or the backfill
        # could pick `new_substitute` straight back onto `conflicting` too, double-booking the
        # same teacher into both absences at this exact date/session.
        absence.date=payload.date
        absence.timeslot_id=payload.timeslot_id
        absence.class_group_id=group.id
        absence.classroom_id=classroom.id
        absence.task_left=task_left
        absence.observations=payload.observations
        absence.substitute_teacher_id=new_substitute.id if new_substitute else None
        db.flush()

        if conflicting:
            conflicting.substitute_teacher_id=assign_substitute(db,conflicting)
            db.flush()

        if release_needed: release_substitute_credit(db,old_substitute_id,old_timeslot_id)

        # Unconditional: credit_manual_assignment() doesn't check GUARD/SUPPORT
        # availability, which is exactly the bypass this endpoint requires.
        if assign_needed: credit_manual_assignment(db,new_substitute.id,payload.timeslot_id)
        db.flush()

        # Corridor posts may need restaffing on both the old and the new session (they
        # only differ when the date/session actually changed).
        assign_hallway_duties(db,old_date,old_timeslot_id)
        if session_changed: assign_hallway_duties(db,payload.date,payload.timeslot_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(absence)

    if release_needed:
        send_substitution_cancelled_email(db,old_snapshot,old_substitute_id)
    if assign_needed:
        send_substitution_email(db,absence,new_substitute.id)
    elif new_substitute and not substitute_changed and not session_changed and details_changed:
        send_absence_updated_email(db,absence,new_substitute.id)
    if conflicting:
        db.refresh(conflicting)
        if conflicting.substitute_teacher_id is not None:
            send_substitution_email(db,conflicting,conflicting.substitute_teacher_id)
    return absence
@app.get('/absences',response_model=list[AbsenceOut])
def absences(request:Request,date_from:date|None=None,date_to:date|None=None,teacher_id:int|None=None,substitute_id:int|None=None,group_id:int|None=None,db:Session=Depends(get_db)):
    q=select(Absence).order_by(Absence.date.desc(),Absence.id.desc())
    if request.state.user.get('role') == 'teacher': q=q.where(or_(Absence.absent_teacher_id==request.state.user['teacher_id'],Absence.substitute_teacher_id==request.state.user['teacher_id']))
    if date_from:q=q.where(Absence.date>=date_from)
    if date_to:q=q.where(Absence.date<=date_to)
    if teacher_id:q=q.where(Absence.absent_teacher_id==teacher_id)
    if substitute_id:q=q.where(Absence.substitute_teacher_id==substitute_id)
    if group_id:q=q.where(Absence.class_group_id==group_id)
    return db.scalars(q).all()
@app.get('/hallway-duties')
def hallway_duties(request:Request,day:date|None=None,db:Session=Depends(get_db)):
    """Corridor duty roster of one school day, visible to the whole staff.

    The three posts of every session are (re)staffed on read, so the roster always
    reflects the substitutions registered so far without anyone having to generate it.
    """
    target=day or date.today()
    duties=ensure_hallway_duties_for_date(db,target)
    db.commit()
    return {'date':target,'posts':[{'post':post,'label':HALLWAY_POST_LABELS[post]} for post in HALLWAY_POSTS],
            'duties':[{'id':x.id,'date':x.date,'timeslot_id':x.timeslot_id,'post':x.post,'label':HALLWAY_POST_LABELS.get(x.post,x.post),'teacher_id':x.teacher_id} for x in duties]}
@app.post('/admin/sync-educamadrid')
def sync_educamadrid(request:Request):
    """Triggers the EducaMadrid substitution sync service on demand. The service is
    only reachable on the internal Docker network; SYNC_TRIGGER_TOKEN is an extra
    guard since it drives real browser automation with EducaMadrid credentials."""
    require_admin(request)
    url=os.getenv('SYNC_SERVICE_URL','http://sync:8080')
    token=os.getenv('SYNC_TRIGGER_TOKEN','')
    try:
        response=httpx.post(f'{url}/run',headers={'X-Sync-Token':token},timeout=230.0)
    except httpx.RequestError:
        raise HTTPException(503,'El servicio de sincronización con EducaMadrid no está disponible ahora mismo.')
    if response.status_code!=200:
        raise HTTPException(502,f'El servicio de sincronización devolvió un error: {response.text}')
    return response.json()
@app.get('/admin/email-logs',response_model=list[EmailLogOut])
def email_logs(request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    return db.scalars(select(EmailLog).order_by(EmailLog.sent_at.desc(),EmailLog.id.desc())).all()
@app.post('/admin/reset-year')
def reset_year(request:Request,db:Session=Depends(get_db)):
    """Clears operational data (substitutions and assignment statistics) to start a new
    academic year, while keeping teachers, availability, groups, classrooms and timeslots."""
    require_admin(request)
    absences_deleted=db.query(Absence).delete(synchronize_session=False)
    statistics_deleted=db.query(AssignmentStatistic).delete(synchronize_session=False)
    hallway_duties_deleted=db.query(HallwayDuty).delete(synchronize_session=False)
    db.commit()
    return {'absences_deleted':absences_deleted,'statistics_deleted':statistics_deleted,'hallway_duties_deleted':hallway_duties_deleted}
@app.get('/statistics')
def statistics(request:Request,db:Session=Depends(get_db)):
    user=request.state.user
    teachers=db.scalars(select(Teacher).order_by(Teacher.last_name)).all()
    if user.get('role') == 'teacher': teachers=[t for t in teachers if t.id==user.get('teacher_id')]
    slots={x.id:x for x in db.scalars(select(TimeSlot)).all()}; stats=db.scalars(select(AssignmentStatistic)).all()
    hallway={teacher_id:float(total or 0) for teacher_id,total in db.execute(select(HallwayDuty.teacher_id,func.sum(HallwayDuty.weight)).where(HallwayDuty.teacher_id.is_not(None)).group_by(HallwayDuty.teacher_id)).all()}
    return [{'teacher':{'id':t.id,'name':f'{t.first_name} {t.last_name}','duty_weight':t.duty_weight},'total':sum(x.assignment_count for x in stats if x.teacher_id==t.id),'hallway_total':hallway.get(t.id,0),'by_slot':[{'timeslot_id':x.timeslot_id,'label':f'{slots[x.timeslot_id].weekday} P{slots[x.timeslot_id].period_number}','count':x.assignment_count} for x in stats if x.teacher_id==t.id]} for t in teachers]
@app.get('/reports/guard-duty.pdf')
def guard_duty_report(request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    """PDF document of scheduled Guard and Support Guard duties by teacher/session."""
    teachers=db.scalars(select(Teacher).order_by(Teacher.last_name,Teacher.first_name)).all()
    slots=db.scalars(select(TimeSlot).order_by(TimeSlot.id)).all()
    by_key={(x.teacher_id,x.timeslot_id):x.duty_type for x in db.scalars(select(Availability)).all()}
    output=BytesIO(); doc=SimpleDocTemplate(output,pagesize=landscape(A4),rightMargin=12*mm,leftMargin=12*mm,topMargin=12*mm,bottomMargin=12*mm)
    styles=getSampleStyleSheet(); story=[]; days=['Monday','Tuesday','Wednesday','Thursday','Friday']
    for number,teacher in enumerate(teachers):
        if number: story.append(PageBreak())
        guard=sum(1 for s in slots if by_key.get((teacher.id,s.id))=='GUARD'); support=sum(1 for s in slots if by_key.get((teacher.id,s.id))=='SUPPORT')
        story += [Paragraph('Guard duty report',styles['Title']),Paragraph(f'{teacher.first_name} {teacher.last_name} - Guard duties: {guard} - Support Guard duties: {support}',styles['Heading2']),Spacer(1,5*mm)]
        header=['Session']+days; rows=[header]
        for period in range(1,8):
            reference=next((s for s in slots if s.period_number==period),None)
            row=[f'{reference.start_time.strftime("%H:%M")}-{reference.end_time.strftime("%H:%M")}' if reference else f'Session {period}']
            for day in days:
                slot=next((s for s in slots if s.weekday==day and s.period_number==period),None)
                kind=by_key.get((teacher.id,slot.id)) if slot else None
                row.append('Guard' if kind=='GUARD' else 'Support' if kind=='SUPPORT' else '-')
            rows.append(row)
        table=Table(rows,colWidths=[31*mm]+[45*mm]*5)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1d4ed8')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('BACKGROUND',(0,1),(0,-1),colors.HexColor('#dbeafe')),('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#94a3b8')),('ALIGN',(0,0),(-1,-1),'CENTER'),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('FONTSIZE',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.append(table)
    doc.build(story); output.seek(0)
    return StreamingResponse(output,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename=guard-duty-report.pdf'})
@app.get('/dashboard')
def dashboard(request:Request,db:Session=Depends(get_db)):
    require_admin(request)
    today=date.today(); rows=db.scalars(select(Absence).where(Absence.date==today)).all()
    duties=ensure_hallway_duties_for_date(db,today); db.commit()
    return {'date':today,'substitutions':rows,'absence_count':len(rows),'covering_count':sum(x.substitute_teacher_id is not None for x in rows),'unassigned_count':sum(x.substitute_teacher_id is None for x in rows),
            'hallway_duties':[{'timeslot_id':x.timeslot_id,'post':x.post,'label':HALLWAY_POST_LABELS.get(x.post,x.post),'teacher_id':x.teacher_id} for x in duties],
            'hallway_uncovered_count':sum(x.teacher_id is None for x in duties)}
