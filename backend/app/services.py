import logging
import os
import random
import smtplib
from email.message import EmailMessage
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .models import Absence, AssignmentStatistic, Availability, HallwayDuty, Teacher, TimeSlot

logger = logging.getLogger(__name__)
def assign_substitute(db:Session, absence:Absence) -> int|None:
    """Choose an eligible teacher with the lowest weighted counter, safely within transaction.

    Each time a teacher is chosen, their counter for this timeslot rises by their
    duty_weight (1.0 by default) rather than a flat 1, so a part-time teacher configured
    above 1 accumulates load faster per duty and is picked less often than full-time
    colleagues with the same number of actual duties.
    """
    occupied = select(Absence.substitute_teacher_id).where(Absence.date==absence.date, Absence.timeslot_id==absence.timeslot_id, Absence.substitute_teacher_id.is_not(None))
    # Excludes every teacher who is themselves absent that date/timeslot (not just
    # this absence's own absent teacher), so someone out sick can't be handed a
    # substitution for the same period.
    absent = select(Absence.absent_teacher_id).where(Absence.date==absence.date, Absence.timeslot_id==absence.timeslot_id)
    base=select(Teacher,Availability.duty_type).join(Availability, Availability.teacher_id==Teacher.id).where(Availability.timeslot_id==absence.timeslot_id, Teacher.active.is_(True), Teacher.id.not_in(absent), Teacher.id.not_in(occupied)).with_for_update()
    available=db.execute(base).all()
    # A Support Guard is considered only when every Guard is unavailable.
    guard=[teacher for teacher,kind in available if kind=='GUARD']
    candidates=guard or [teacher for teacher,kind in available if kind=='SUPPORT']
    if not candidates: return None
    stats={s.teacher_id:s for s in db.scalars(select(AssignmentStatistic).where(AssignmentStatistic.timeslot_id==absence.timeslot_id, AssignmentStatistic.teacher_id.in_([t.id for t in candidates])).with_for_update())}
    lowest=min(stats.get(t.id).assignment_count if t.id in stats else 0 for t in candidates)
    chosen=random.choice([t for t in candidates if (stats[t.id].assignment_count if t.id in stats else 0)==lowest])
    stat=stats.get(chosen.id)
    if stat: stat.assignment_count+=chosen.duty_weight
    else: db.add(AssignmentStatistic(teacher_id=chosen.id,timeslot_id=absence.timeslot_id,assignment_count=chosen.duty_weight))
    return chosen.id


def release_substitute_credit(db:Session, teacher_id:int, timeslot_id:int) -> None:
    """Undo the weighted credit `teacher_id` earned for covering this timeslot, because
    that coverage is being reassigned to someone else."""
    stat=db.get(AssignmentStatistic,(teacher_id,timeslot_id))
    if not stat or stat.assignment_count<=0: return
    teacher=db.get(Teacher,teacher_id)
    weight=teacher.duty_weight if teacher else 1.0
    stat.assignment_count=max(0.0,stat.assignment_count-weight)

# Corridor posts that must be staffed in every session, in the order they are filled
# once the substitutions of that session are already covered.
HALLWAY_POSTS = ('GROUND', 'FIRST', 'SECOND')
HALLWAY_POST_LABELS = {'GROUND': 'Pasillo planta baja', 'FIRST': 'Pasillo primera planta', 'SECOND': 'Pasillo segunda planta'}
# TimeSlot.weekday values indexed by date.weekday(); Saturday and Sunday have no sessions.
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')


def _free_teachers(db:Session, day, timeslot_id) -> list[tuple[Teacher,str]]:
    """Teachers with duty availability in a session who are neither absent nor covering a class."""
    occupied = select(Absence.substitute_teacher_id).where(Absence.date==day, Absence.timeslot_id==timeslot_id, Absence.substitute_teacher_id.is_not(None))
    absent = select(Absence.absent_teacher_id).where(Absence.date==day, Absence.timeslot_id==timeslot_id)
    return db.execute(select(Teacher,Availability.duty_type).join(Availability, Availability.teacher_id==Teacher.id).where(Availability.timeslot_id==timeslot_id, Teacher.active.is_(True), Teacher.id.not_in(absent), Teacher.id.not_in(occupied))).all()


def _hallway_duty_counts(db:Session, teacher_ids) -> dict[int,float]:
    """Corridor duty load already done per teacher, weighted the same way as
    substitutions: each past duty contributes the duty_weight recorded for it."""
    if not teacher_ids: return {}
    rows = db.execute(select(HallwayDuty.teacher_id, func.sum(HallwayDuty.weight)).where(HallwayDuty.teacher_id.in_(teacher_ids)).group_by(HallwayDuty.teacher_id)).all()
    return {teacher_id:float(total or 0) for teacher_id,total in rows}


def assign_hallway_duties(db:Session, day, timeslot_id) -> list[HallwayDuty]:
    """Keep the three corridor posts of one session staffed with the teachers left free.

    Substitutions always come first: whoever became absent or is now covering a class is
    released from their post, and the empty posts are filled again in order (ground floor,
    first floor, second floor) while free teachers remain. Posts held by someone who is
    still free are kept, so an already published roster is not reshuffled needlessly.
    """
    candidates=_free_teachers(db,day,timeslot_id)
    free_ids={teacher.id for teacher,_ in candidates}
    posts_of_session=select(HallwayDuty).where(HallwayDuty.date==day, HallwayDuty.timeslot_id==timeslot_id)
    duties={x.post:x for x in db.scalars(posts_of_session)}
    missing=[post for post in HALLWAY_POSTS if post not in duties]
    if missing:
        # Two requests may open the same session at once; the unique constraint stops the
        # duplicate inside a savepoint, so the outer transaction survives and the posts
        # created by the other request are used instead.
        try:
            with db.begin_nested():
                for post in missing: db.add(HallwayDuty(date=day,timeslot_id=timeslot_id,post=post))
        except IntegrityError:
            pass
        duties={x.post:x for x in db.scalars(posts_of_session)}
    for post in HALLWAY_POSTS:
        duty=duties[post]
        if duty.teacher_id is not None and duty.teacher_id not in free_ids: duty.teacher_id=None
    taken={x.teacher_id for x in duties.values() if x.teacher_id is not None}
    counts=_hallway_duty_counts(db,free_ids)
    for post in HALLWAY_POSTS:
        if duties[post].teacher_id is not None: continue
        # A Support Guard covers a corridor only when every Guard is already busy.
        pool=[t for t,kind in candidates if kind=='GUARD' and t.id not in taken] or [t for t,kind in candidates if kind=='SUPPORT' and t.id not in taken]
        if not pool: break
        lowest=min(counts.get(t.id,0) for t in pool)
        chosen=random.choice([t for t in pool if counts.get(t.id,0)==lowest])
        duties[post].teacher_id=chosen.id; duties[post].weight=chosen.duty_weight; taken.add(chosen.id); counts[chosen.id]=counts.get(chosen.id,0)+chosen.duty_weight
    db.flush()
    return [duties[post] for post in HALLWAY_POSTS]


def ensure_hallway_duties_for_date(db:Session, day) -> list[HallwayDuty]:
    """Staff the corridor posts of every session of one school day."""
    if day.weekday() >= len(WEEKDAYS): return []
    slots=db.scalars(select(TimeSlot).where(TimeSlot.weekday==WEEKDAYS[day.weekday()]).order_by(TimeSlot.period_number)).all()
    return [duty for slot in slots for duty in assign_hallway_duties(db,day,slot.id)]


def send_substitution_email(db:Session, absence:Absence, substitute_teacher_id:int) -> bool:
    host=os.getenv('SMTP_HOST','').strip()
    port=int(os.getenv('SMTP_PORT','587'))
    username=os.getenv('SMTP_USERNAME','').strip()
    password=os.getenv('SMTP_PASSWORD','').strip()
    sender=os.getenv('SMTP_FROM',username or 'no-reply@localhost').strip()
    use_tls=os.getenv('SMTP_USE_TLS','true').lower() not in {'0','false','no'}
    use_ssl=os.getenv('SMTP_USE_SSL','false').lower() in {'1','true','yes'}
    if not host or not sender:
        logger.info('SMTP not configured; skipping substitution email for teacher_id=%s', substitute_teacher_id)
        return False

    substitute=db.get(Teacher, substitute_teacher_id)
    absent=db.get(Teacher, absence.absent_teacher_id)
    time_slot=db.get(TimeSlot, absence.timeslot_id)
    if not substitute or not time_slot:
        logger.warning('Skipping substitution email because related records are missing for absence_id=%s', absence.id)
        return False

    group_name=getattr(absence,'class_group',None) or 'no disponible'
    classroom_name=getattr(absence,'classroom',None) or 'no disponible'

    message=EmailMessage()
    message['Subject']=f'Sustitución asignada: {time_slot.weekday} sesión {time_slot.period_number}'
    message['From']=sender
    message['To']=substitute.email
    message.set_content(
        '\n'.join([
            f'Hola {substitute.first_name} {substitute.last_name},',
            '',
            'Se te ha asignado una sustitución con estos datos:',
            f'- Fecha: {absence.date.isoformat()}',
            f'- Sesión: {time_slot.weekday} sesión {time_slot.period_number} ({time_slot.start_time.strftime("%H:%M")}-{time_slot.end_time.strftime("%H:%M")})',
            f'- Profesor/a ausente: {absent.first_name} {absent.last_name}' if absent else '- Profesor/a ausente: no disponible',
            f'- Grupo: {group_name}',
            f'- Aula: {classroom_name}',
            f'- Tarea: {absence.task_left}',
            f'- Observaciones: {absence.observations or "-"}',
            '',
            'Recibirás este aviso porque la sustitución te ha sido asignada en el sistema.',
        ])
    )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=10) as smtp:
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                if use_tls:
                    smtp.starttls()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
        return True
    except Exception:
        logger.exception('Failed to send substitution email for absence_id=%s', absence.id)
        return False
