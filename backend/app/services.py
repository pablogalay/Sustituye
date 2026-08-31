import logging
import os
import random
import smtplib
from email.message import EmailMessage
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Absence, AssignmentStatistic, Availability, Teacher, TimeSlot

logger = logging.getLogger(__name__)
def assign_substitute(db:Session, absence:Absence) -> int|None:
    """Choose an eligible teacher with the lowest counter, safely within transaction."""
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
    if stat: stat.assignment_count+=1
    else: db.add(AssignmentStatistic(teacher_id=chosen.id,timeslot_id=absence.timeslot_id,assignment_count=1))
    return chosen.id

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
