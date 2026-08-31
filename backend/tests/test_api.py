"""Basic authenticated API integration coverage (run with pytest)."""
import os
from datetime import date, time
from types import SimpleNamespace
os.environ['DATABASE_URL']='sqlite:///./test_api.db'
os.environ['JWT_SECRET']='test-secret'
import httpx
from fastapi.testclient import TestClient
from app.database import SessionLocal, Base, engine
from app.main import app
from app.models import Absence, AssignmentStatistic, Teacher, TimeSlot
from app.seed import main as seed
Base.metadata.drop_all(engine); Base.metadata.create_all(engine); seed()
client=TestClient(app)
def auth(): return {'Authorization':'Bearer '+client.post('/auth/login',json={'email':'admin@school.local','password':'admin123'}).json()['access_token']}
def test_login_and_protected_teachers_endpoint():
    assert client.get('/teachers').status_code==401
    response=client.get('/teachers',headers=auth())
    assert response.status_code==200 and len(response.json())==20
def test_dashboard_endpoint():
    assert client.get('/dashboard',headers=auth()).status_code==200

def test_create_absence_triggers_notification(monkeypatch):
    called={}
    monkeypatch.setattr('app.main.assign_substitute', lambda db, absence: 1)
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: called.update({'substitute_teacher_id': substitute_teacher_id}) or True)
    response=client.post('/absences',headers=auth(),json={'date':'2026-08-10','timeslot_id':1,'absent_teacher_id':2,'class_group':'1A','classroom':'A1','task_left':'Trabajo','observations':'-'})
    assert response.status_code==201
    assert called['substitute_teacher_id']==1

def test_create_absence_rejects_exact_duplicate(monkeypatch):
    monkeypatch.setattr('app.main.assign_substitute', lambda db, absence: None)
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: True)
    payload={'date':'2026-09-01','timeslot_id':2,'absent_teacher_id':6,'class_group':'DupTest','classroom':'DupRoom','task_left':'Tarea duplicada','observations':None}
    first=client.post('/absences',headers=auth(),json=payload)
    assert first.status_code==201
    duplicate=client.post('/absences',headers=auth(),json=payload)
    assert duplicate.status_code==409
    changed=dict(payload,task_left='Tarea distinta')
    different=client.post('/absences',headers=auth(),json=changed)
    assert different.status_code==201

def test_delete_absence_and_block_teacher_delete(monkeypatch):
    monkeypatch.setattr('app.main.assign_substitute', lambda db, absence: 2)
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: True)
    substitute_teacher=client.post('/teachers',headers=auth(),json={'first_name':'Sust','last_name':'Ituto','email':'sustituto@example.com','active':True,'password':'password123'})
    assert substitute_teacher.status_code==201
    substitute_teacher_id=substitute_teacher.json()['id']
    absent_teacher=client.post('/teachers',headers=auth(),json={'first_name':'Aus','last_name':'Ente','email':'ausente@example.com','active':True,'password':'password123'})
    assert absent_teacher.status_code==201
    absent_teacher_id=absent_teacher.json()['id']
    monkeypatch.setattr('app.main.assign_substitute', lambda db, absence: substitute_teacher_id)
    created=client.post('/absences',headers=auth(),json={'date':'2026-08-10','timeslot_id':1,'absent_teacher_id':absent_teacher_id,'class_group':'1A','classroom':'A1','task_left':'Trabajo','observations':'-'})
    assert created.status_code==201
    assert client.delete(f'/teachers/{substitute_teacher_id}',headers=auth()).status_code==409
    absence_id=created.json()['id']
    assert client.delete(f'/absences/{absence_id}',headers=auth()).status_code==204
    assert client.delete(f'/teachers/{substitute_teacher_id}',headers=auth()).status_code==204

def test_sync_educamadrid_relays_result(monkeypatch):
    captured={}
    class FakeResponse:
        status_code=200
        text=''
        def json(self): return {'fetched':1,'complete':1,'imported':1,'pending':0}
    def fake_post(url,headers=None,timeout=None):
        captured['url']=url; captured['headers']=headers
        return FakeResponse()
    monkeypatch.setattr('app.main.httpx.post', fake_post)
    response=client.post('/admin/sync-educamadrid',headers=auth())
    assert response.status_code==200
    assert response.json()=={'fetched':1,'complete':1,'imported':1,'pending':0}
    assert captured['url'].endswith('/run')
    assert 'X-Sync-Token' in captured['headers']

def test_sync_educamadrid_reports_service_unavailable(monkeypatch):
    def fake_post(url,headers=None,timeout=None):
        raise httpx.RequestError('boom')
    monkeypatch.setattr('app.main.httpx.post', fake_post)
    response=client.post('/admin/sync-educamadrid',headers=auth())
    assert response.status_code==503

def test_reset_year_clears_absences_and_statistics_but_keeps_teachers(monkeypatch):
    monkeypatch.setattr('app.main.assign_substitute', lambda db, absence: 3)
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: True)
    created=client.post('/absences',headers=auth(),json={'date':'2026-08-10','timeslot_id':1,'absent_teacher_id':4,'class_group':'2A','classroom':'A2','task_left':'Trabajo','observations':'-'})
    assert created.status_code==201
    db=SessionLocal()
    db.add(AssignmentStatistic(teacher_id=3,timeslot_id=1,assignment_count=2))
    db.commit(); db.close()
    teachers_before=client.get('/teachers',headers=auth()).json()

    response=client.post('/admin/reset-year',headers=auth())
    assert response.status_code==200
    body=response.json()
    assert body['absences_deleted']>=1
    assert body['statistics_deleted']>=1

    assert client.get('/absences',headers=auth()).json()==[]
    stats=client.get('/statistics',headers=auth()).json()
    assert all(s['total']==0 for s in stats)
    assert client.get('/teachers',headers=auth()).json()==teachers_before

def test_send_substitution_email_uses_transient_group_and_classroom(monkeypatch):
    monkeypatch.setenv('SMTP_HOST','smtp.example.com')
    monkeypatch.setenv('SMTP_PORT','587')
    monkeypatch.setenv('SMTP_USERNAME','user@example.com')
    monkeypatch.setenv('SMTP_PASSWORD','secret')
    monkeypatch.setenv('SMTP_FROM','no-reply@example.com')

    captured={}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):
            self.host=host
            self.port=port
            self.timeout=timeout
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def starttls(self):
            return None
        def login(self, username, password):
            captured['login']=(username,password)
        def send_message(self, message):
            captured['message']=message

    monkeypatch.setattr('app.services.smtplib.SMTP', FakeSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', FakeSMTP)

    substitute=SimpleNamespace(id=1, first_name='Ana', last_name='López', email='ana@example.com')
    absent=SimpleNamespace(first_name='Luis', last_name='Pérez')
    time_slot=SimpleNamespace(weekday='Monday', period_number=2, start_time=time(9,0), end_time=time(10,0))
    absence=SimpleNamespace(
        id=999,
        date=date(2026,8,10),
        timeslot_id=1,
        absent_teacher_id=2,
        task_left='Trabajo',
        observations='-',
        class_group='4B',
        classroom='Aula 12',
    )

    class FakeDB:
        def get(self, model, identifier):
            if model is Teacher and identifier == 1:
                return substitute
            if model is Teacher and identifier == 2:
                return absent
            if model is TimeSlot and identifier == 1:
                return time_slot
            return None

    from app.services import send_substitution_email
    assert send_substitution_email(FakeDB(), absence, substitute.id) is True
    assert '4B' in captured['message'].get_content()
    assert 'Aula 12' in captured['message'].get_content()
