from datetime import date, time, datetime
from pydantic import BaseModel, ConfigDict
class TeacherIn(BaseModel):
    first_name: str
    last_name: str
    # School installations often use internal mail domains (for example .local).
    # The database still enforces uniqueness, while this accepts those valid
    # internal identifiers on input and output.
    email: str
    active: bool = True
    password: str | None = None
class TeacherOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    active: bool
    model_config=ConfigDict(from_attributes=True)
class TimeSlotOut(BaseModel): id:int; weekday:str; period_number:int; start_time:time; end_time:time; model_config=ConfigDict(from_attributes=True)
class AvailabilityEntryIn(BaseModel):
    timeslot_id: int
    duty_type: str
class AvailabilityIn(BaseModel):
    teacher_id: int
    entries: list[AvailabilityEntryIn]
class AbsenceIn(BaseModel): date:date; timeslot_id:int; absent_teacher_id:int; class_group:str; classroom:str; task_left:str; observations:str|None=None
class AbsenceOut(BaseModel): id:int; date:date; timeslot_id:int; absent_teacher_id:int; class_group_id:int; classroom_id:int; task_left:str; observations:str|None; substitute_teacher_id:int|None; created_at:datetime; model_config=ConfigDict(from_attributes=True)
class LoginIn(BaseModel):
    # Administrator accounts may use internal addresses such as
    # ``admin@school.local``. Authentication compares this identifier with the
    # configured value, so DNS-style email validation must not reject it.
    email: str
    password: str
