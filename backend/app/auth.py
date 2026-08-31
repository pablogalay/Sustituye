import os, jwt, base64, hashlib, hmac, secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Teacher
SECRET=os.getenv('JWT_SECRET','unsafe-development-secret')
def hash_password(password:str):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
    return f'pbkdf2_sha256${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}'
def verify_password(password:str,stored:str|None):
    if not stored: return False
    try:
        _,salt,digest=stored.split('$')
        candidate=hashlib.pbkdf2_hmac('sha256',password.encode(),base64.b64decode(salt),310000)
        return hmac.compare_digest(candidate,base64.b64decode(digest))
    except (ValueError, TypeError): return False
def login(email:str,password:str,db:Session):
    if email == os.getenv('ADMIN_EMAIL','admin@school.local') and password == os.getenv('ADMIN_PASSWORD','admin123'):
        claims={'sub':email,'role':'admin'}
    else:
        teacher=db.scalar(select(Teacher).where(Teacher.email==email))
        if not teacher or not teacher.active or not verify_password(password,teacher.password_hash): raise HTTPException(status_code=401,detail='Invalid credentials')
        claims={'sub':email,'role':'teacher','teacher_id':teacher.id}
    claims['exp']=datetime.now(timezone.utc)+timedelta(hours=12)
    return {'access_token':jwt.encode(claims,SECRET,algorithm='HS256'),'token_type':'bearer'}
def verify(token:str):
    try: return jwt.decode(token,SECRET,algorithms=['HS256'])
    except jwt.PyJWTError: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail='Invalid or expired token')
