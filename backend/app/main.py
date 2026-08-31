import os
from datetime import date
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
from .models import Absence, AssignmentStatistic, Availability, ClassGroup, Classroom, Teacher, TimeSlot
from .schemas import AbsenceIn, AbsenceOut, AvailabilityIn, LoginIn, TeacherIn, TeacherOut, TimeSlotOut
from .services import assign_substitute, send_substitution_email
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
    db.delete(item)
    db.commit()
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
    absence.class_group=group_name
    absence.classroom=classroom_name
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
        stat=db.get(AssignmentStatistic,(payload.absent_teacher_id,payload.timeslot_id))
        if stat and stat.assignment_count>0: stat.assignment_count-=1
        other.substitute_teacher_id=None
        db.flush()
        other.substitute_teacher_id=assign_substitute(db,other)
        db.flush()

    absence.substitute_teacher_id=assign_substitute(db,absence)
    db.commit(); db.refresh(absence)
    if absence.substitute_teacher_id is not None:
        send_substitution_email(db, absence, absence.substitute_teacher_id)
    for other in conflicting:
        db.refresh(other)
        if other.substitute_teacher_id is not None:
            send_substitution_email(db, other, other.substitute_teacher_id)
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
@app.post('/admin/reset-year')
def reset_year(request:Request,db:Session=Depends(get_db)):
    """Clears operational data (substitutions and assignment statistics) to start a new
    academic year, while keeping teachers, availability, groups, classrooms and timeslots."""
    require_admin(request)
    absences_deleted=db.query(Absence).delete(synchronize_session=False)
    statistics_deleted=db.query(AssignmentStatistic).delete(synchronize_session=False)
    db.commit()
    return {'absences_deleted':absences_deleted,'statistics_deleted':statistics_deleted}
@app.get('/statistics')
def statistics(request:Request,db:Session=Depends(get_db)):
    user=request.state.user
    teachers=db.scalars(select(Teacher).order_by(Teacher.last_name)).all()
    if user.get('role') == 'teacher': teachers=[t for t in teachers if t.id==user.get('teacher_id')]
    slots={x.id:x for x in db.scalars(select(TimeSlot)).all()}; stats=db.scalars(select(AssignmentStatistic)).all()
    return [{'teacher':{'id':t.id,'name':f'{t.first_name} {t.last_name}'},'total':sum(x.assignment_count for x in stats if x.teacher_id==t.id),'by_slot':[{'timeslot_id':x.timeslot_id,'label':f'{slots[x.timeslot_id].weekday} P{slots[x.timeslot_id].period_number}','count':x.assignment_count} for x in stats if x.teacher_id==t.id]} for t in teachers]
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
    return {'date':today,'substitutions':rows,'absence_count':len(rows),'covering_count':sum(x.substitute_teacher_id is not None for x in rows),'unassigned_count':sum(x.substitute_teacher_id is None for x in rows)}
