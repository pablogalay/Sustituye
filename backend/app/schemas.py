from datetime import date, time, datetime
from pydantic import BaseModel, ConfigDict, Field
class TeacherIn(BaseModel):
    first_name: str
    last_name: str
    # School installations often use internal mail domains (for example .local).
    # The database still enforces uniqueness, while this accepts those valid
    # internal identifiers on input and output.
    email: str
    active: bool = True
    password: str | None = None
    # How much each guard duty counts for this teacher's load-balancing counters.
    # Above 1 for part-time staff so they accumulate load faster per duty and are
    # picked less often than full-time colleagues.
    duty_weight: float = Field(1.0, gt=0)
class TeacherOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    active: bool
    duty_weight: float
    model_config=ConfigDict(from_attributes=True)
class TimeSlotOut(BaseModel): id:int; weekday:str; period_number:int; start_time:time; end_time:time; model_config=ConfigDict(from_attributes=True)
class AvailabilityEntryIn(BaseModel):
    timeslot_id: int
    duty_type: str
class AvailabilityIn(BaseModel):
    teacher_id: int
    entries: list[AvailabilityEntryIn]
class AbsenceIn(BaseModel): date:date; timeslot_id:int; absent_teacher_id:int; class_group:str; classroom:str; task_left:str; observations:str|None=None
class AbsenceManualAssignIn(BaseModel): substitute_id: int
class AbsenceUpdateIn(BaseModel):
    # A teacher may only correct what doesn't affect the automatic substitute
    # assignment: the task description and the classroom. date/timeslot/class_group
    # are intentionally absent here — changing those would require recalculating
    # availability and the assigned substitute, so callers must delete and
    # recreate the absence instead of editing them in place.
    task_left: str | None = None
    classroom: str | None = None
class AbsenceOut(BaseModel): id:int; date:date; timeslot_id:int; absent_teacher_id:int; class_group_id:int; classroom_id:int; task_left:str; observations:str|None; substitute_teacher_id:int|None; created_at:datetime; model_config=ConfigDict(from_attributes=True)
class AdminAbsenceUpdateIn(BaseModel):
    # A full administrative replace: unlike AbsenceUpdateIn (the teacher's own
    # task/classroom-only PATCH), every editable field is required here since this is
    # a PUT — the admin UI always sends the complete, current state of the form.
    # substitute_id is explicitly nullable: None unassigns and leaves the absence pending.
    date: date
    timeslot_id: int
    class_group: str
    classroom: str
    task_left: str
    observations: str | None = None
    substitute_id: int | None = None
class EmailLogOut(BaseModel):
    id: int
    recipient_email: str
    subject: str
    sent_at: datetime
    status: str
    error_message: str | None
    model_config=ConfigDict(from_attributes=True)
class LoginIn(BaseModel):
    # Administrator accounts may use internal addresses such as
    # ``admin@school.local``. Authentication compares this identifier with the
    # configured value, so DNS-style email validation must not reject it.
    email: str
    password: str
