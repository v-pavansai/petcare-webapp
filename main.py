import os
import secrets
import base64
import hashlib as _sha256_compat  
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
from PIL import Image
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt

from google import genai

load_dotenv()

app = FastAPI(title="PetCare API")

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
DATABASE_URL    = os.getenv("DATABASE_URL")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("CRITICAL SECURITY ERROR: SECRET_KEY is not set in environment variables.")

ALGORITHM       = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

SMTP_SERVER   = os.getenv("SMTP_SERVER")
SMTP_PORT     = int(os.getenv("SMTP_PORT"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:8000")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── DATABASE ───────────────────────────────────────────────────────────────────
try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    engine.connect().close()
except Exception as e:
    print(f"Database connection failed: {e}")

if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    email           = Column(String(100), unique=True, index=True, nullable=False)
    username        = Column(String(50), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

class OTPCode(Base):
    __tablename__ = "otp_codes"
    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String(100), index=True, nullable=False)
    otp_code   = Column(String(6), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Pet(Base):
    __tablename__ = "pets"
    id          = Column(Integer, primary_key=True, index=True)
    owner_email = Column(String(100), index=True, nullable=False)
    pet_type    = Column(String(50), nullable=False)
    breed       = Column(String(100), nullable=False)
    name        = Column(String(50), nullable=False)
    age         = Column(String(50), nullable=False)

class Vaccine(Base):
    __tablename__ = "vaccines"
    id          = Column(Integer, primary_key=True, index=True)
    pet_id      = Column(Integer, index=True, nullable=False)
    name        = Column(String(100), nullable=False)
    date        = Column(String(50), nullable=False)
    owner_email = Column(String(100), index=True, nullable=False)

# NEW: Database Rate Limiting for Serverless
class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    email           = Column(String(100), primary_key=True, index=True)
    failed_attempts = Column(Integer, default=0)
    locked_until    = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ── PASSWORD HASHING (bcrypt) ──────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password.startswith(("$2b$", "$2a$", "$2y$")):
        return _sha256_compat.sha256(plain_password.encode()).hexdigest() == hashed_password
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# ── JWT AUTHENTICATION ─────────────────────────────────────────────────────────
http_bearer = HTTPBearer()

def create_access_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": email, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_email(
    credentials: HTTPAuthorizationCredentials = Security(http_bearer),
    db: Session = Depends(get_db),
) -> str:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token.")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token is invalid or has expired.")
    return email

# ── OTP BRUTE-FORCE PROTECTION (Database Backed) ──────────────────────────────
MAX_OTP_ATTEMPTS    = 5
OTP_LOCKOUT_MINUTES = 15

def _check_otp_lockout(email: str, db: Session):
    record = db.query(AuthRateLimit).filter(AuthRateLimit.email == email).first()
    if record and record.locked_until and datetime.utcnow() < record.locked_until:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Wait {OTP_LOCKOUT_MINUTES} minutes."
        )

def _record_otp_failure(email: str, db: Session):
    record = db.query(AuthRateLimit).filter(AuthRateLimit.email == email).first()
    if not record:
        record = AuthRateLimit(email=email, failed_attempts=1)
        db.add(record)
    else:
        record.failed_attempts += 1
        if record.failed_attempts >= MAX_OTP_ATTEMPTS:
            record.locked_until = datetime.utcnow() + timedelta(minutes=OTP_LOCKOUT_MINUTES)
            record.failed_attempts = 0
    db.commit()

def _clear_otp_attempts(email: str, db: Session):
    db.query(AuthRateLimit).filter(AuthRateLimit.email == email).delete()
    db.commit()

# ── EMAIL ──────────────────────────────────────────────────────────────────────
def send_email_sync(to_email: str, otp: str):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        return # Silently fail in production to avoid logging OTPs
    try:
        msg = MIMEMultipart()
        msg['From']    = SMTP_USERNAME
        msg['To']      = to_email
        msg['Subject'] = "PetCare - Your Verification Code"
        body = f"Your PetCare verification code is: {otp}\nExpires in 10 minutes."
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
    except Exception:
        pass

# ── PYDANTIC SCHEMAS (With Limits) ─────────────────────────────────────────────
class EmailRequest(BaseModel):
    email: EmailStr
    purpose: str = Field(default="login", pattern="^(login|signup|forgot)$")

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=64)

class UsernameUpdate(BaseModel):
    new_username: str = Field(..., max_length=50)

class VerifyRequest(BaseModel):
    email:     EmailStr
    otp_code:  str
    username:  Optional[str] = Field(None, max_length=50)
    password:  Optional[str] = Field(None, max_length=64)

class ResetPasswordRequest(BaseModel):
    email:        EmailStr
    otp_code:     str
    new_password: str = Field(..., max_length=64)

class PetRequest(BaseModel):
    pet_type: str = Field(..., max_length=50)
    breed:    str = Field(..., max_length=100)
    name:     str = Field(..., max_length=50)
    age:      str = Field(..., max_length=50)

class PetUpdate(BaseModel):
    pet_type: str = Field(..., max_length=50)
    breed:    str = Field(..., max_length=100)
    name:     str = Field(..., max_length=50)
    age:      str = Field(..., max_length=50)

class VaccineCreate(BaseModel):
    pet_id: int
    name:   str = Field(..., max_length=100)
    date:   str = Field(..., max_length=50)

class VaccineUpdate(BaseModel):
    date: str = Field(..., max_length=50)

class PetContext(BaseModel):
    name:    str
    species: str
    breed:   str
    age:     str

class AnalyzeRequest(BaseModel):
    type:       str
    value:      str
    petContext: PetContext

# ── HELPER ────────────────────────────────────────────────────────────────────
def _validate_password_strength(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.post("/api/check-credentials")
async def check_credentials(request: LoginRequest, db: Session = Depends(get_db)):
    safe_email = request.email.lower().strip()
    user = db.query(User).filter(User.email == safe_email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Wrong Credentials!")
    
    if not user.hashed_password.startswith(("$2b$", "$2a$", "$2y$")):
        user.hashed_password = get_password_hash(request.password)
        db.commit()
    return {"message": "Credentials valid!"}

@app.post("/api/send-otp")
async def send_otp(request: EmailRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    safe_email = request.email.lower().strip()
    
    # Cleanup old OTPs
    cutoff = datetime.utcnow() - timedelta(hours=24)
    db.query(OTPCode).filter(OTPCode.created_at < cutoff).delete()

    if request.purpose == "signup":
        if db.query(User).filter(User.email == safe_email).first():
            raise HTTPException(status_code=400, detail="Email already registered. Please login.")
    if request.purpose == "forgot":
        if not db.query(User).filter(User.email == safe_email).first():
            raise HTTPException(status_code=404, detail="Account not found. Please sign up.")

    recent_otp = db.query(OTPCode).filter(OTPCode.email == safe_email).order_by(OTPCode.created_at.desc()).first()
    if recent_otp:
        if datetime.utcnow() - recent_otp.created_at < timedelta(minutes=1):
            raise HTTPException(status_code=429, detail="Please wait 1 minute before requesting a new OTP.")

    db.query(OTPCode).filter(OTPCode.email == safe_email).delete()
    
    generated_otp = str(secrets.randbelow(900000) + 100000)
    db.add(OTPCode(email=safe_email, otp_code=generated_otp))
    db.commit()
    
    background_tasks.add_task(send_email_sync, safe_email, generated_otp)
    return {"message": "OTP generated!"}

@app.post("/api/verify-login")
async def verify_login(request: VerifyRequest, db: Session = Depends(get_db)):
    safe_email = request.email.lower().strip()
    _check_otp_lockout(safe_email, db)

    recent_otp = db.query(OTPCode).filter(OTPCode.email == safe_email).order_by(OTPCode.created_at.desc()).first()
    if not recent_otp or datetime.utcnow() - recent_otp.created_at > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="OTP expired or not found.")

    if not secrets.compare_digest(recent_otp.otp_code, request.otp_code):
        _record_otp_failure(safe_email, db)
        raise HTTPException(status_code=401, detail="Invalid OTP code.")

    db.delete(recent_otp)
    _clear_otp_attempts(safe_email, db)

    user = db.query(User).filter(User.email == safe_email).first()
    if user:
        true_username = user.username
    else:
        if not request.password:
            raise HTTPException(status_code=400, detail="Password is required.")
        _validate_password_strength(request.password)
        db.add(User(email=safe_email, username=request.username or "User", hashed_password=get_password_hash(request.password)))
        true_username = request.username or "User"
    
    db.commit()
    return {"message": "Login successful!", "username": true_username, "token": create_access_token(safe_email)}

@app.post("/api/verify-otp")
async def verify_otp_only(request: VerifyRequest, db: Session = Depends(get_db)):
    safe_email = request.email.lower().strip()
    _check_otp_lockout(safe_email, db)

    recent_otp = db.query(OTPCode).filter(OTPCode.email == safe_email).order_by(OTPCode.created_at.desc()).first()
    if not recent_otp or datetime.utcnow() - recent_otp.created_at > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="OTP expired or not found.")

    if not secrets.compare_digest(recent_otp.otp_code, request.otp_code):
        _record_otp_failure(safe_email, db)
        raise HTTPException(status_code=401, detail="Invalid OTP code.")

    _clear_otp_attempts(safe_email, db)
    return {"message": "OTP verified successfully!"}

@app.post("/api/reset-password")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    safe_email = request.email.lower().strip()
    _check_otp_lockout(safe_email, db)
    _validate_password_strength(request.new_password)

    recent_otp = db.query(OTPCode).filter(OTPCode.email == safe_email).order_by(OTPCode.created_at.desc()).first()
    if not recent_otp or datetime.utcnow() - recent_otp.created_at > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="OTP expired or not found.")

    if not secrets.compare_digest(recent_otp.otp_code, request.otp_code):
        _record_otp_failure(safe_email, db)
        raise HTTPException(status_code=401, detail="Invalid OTP.")

    user = db.query(User).filter(User.email == safe_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.hashed_password = get_password_hash(request.new_password)
    db.delete(recent_otp)  
    db.commit()
    _clear_otp_attempts(safe_email, db)
    return {"message": "Password updated successfully!"}

# ── PET & VACCINE ROUTES ──────────────────────────────────────────────────────

@app.get("/api/pets/{email}")
async def get_user_pets(email: str, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    if email.lower().strip() != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    return db.query(Pet).filter(Pet.owner_email == current_email).all()

@app.post("/api/pets")
async def add_pet(request: PetRequest, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    db.add(Pet(owner_email=current_email, pet_type=request.pet_type, breed=request.breed, name=request.name, age=request.age))
    db.commit()
    return {"message": "Pet added successfully!"}

@app.delete("/api/pets/{pet_id}")
async def delete_pet(pet_id: int, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    pet = db.query(Pet).filter(Pet.id == pet_id, Pet.owner_email == current_email).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found.")
    db.delete(pet)
    db.commit()
    return {"message": "Pet deleted successfully!"}

@app.put("/api/pets/{pet_id}")
async def update_pet(pet_id: int, request: PetUpdate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    pet = db.query(Pet).filter(Pet.id == pet_id, Pet.owner_email == current_email).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found.")
    pet.pet_type, pet.breed, pet.name, pet.age = request.pet_type, request.breed, request.name, request.age
    db.commit()
    return {"message": "Pet updated successfully!"}

@app.post("/api/vaccines")
async def create_vaccine(vax: VaccineCreate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    pet = db.query(Pet).filter(Pet.id == vax.pet_id, Pet.owner_email == current_email).first()
    if not pet:
        raise HTTPException(status_code=403, detail="Invalid pet ID or access denied.")
    
    new_vax = Vaccine(pet_id=vax.pet_id, name=vax.name, date=vax.date, owner_email=current_email)
    db.add(new_vax)
    db.commit()
    return new_vax

@app.get("/api/vaccines/{email}")
async def get_vaccines(email: str, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    if email.lower().strip() != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    return db.query(Vaccine).filter(Vaccine.owner_email == current_email).all()

@app.put("/api/vaccines/{vax_id}")
async def update_vaccine(vax_id: int, update_data: VaccineUpdate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    vax = db.query(Vaccine).filter(Vaccine.id == vax_id, Vaccine.owner_email == current_email).first()
    if not vax:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    vax.date = update_data.date
    db.commit()
    return {"message": "Vaccine updated successfully"}

@app.delete("/api/vaccines/{vax_id}")
async def delete_vaccine(vax_id: int, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    vax = db.query(Vaccine).filter(Vaccine.id == vax_id, Vaccine.owner_email == current_email).first()
    if not vax:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    db.delete(vax)
    db.commit()
    return {"message": "Vaccine deleted successfully"}

# ── AI DIAGNOSTIC ROUTE (protected) ──────────────────────────────────────────
@app.post("/api/analyze-health")
async def analyze_health(request: AnalyzeRequest, current_email: str = Depends(get_current_email)):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key is missing.")

    if request.type == "image":
        if len(request.value) > 7_000_000:
            raise HTTPException(status_code=413, detail="Payload too large. Maximum image size is ~5MB.")
        Image.MAX_IMAGE_PIXELS = 15_000_000

    prompt = (
        f"You are a professional virtual veterinary assistant inside the PetCare mobile app. "
        f"The user is asking for advice regarding their pet: a {request.petContext.age} old "
        f"{request.petContext.breed} ({request.petContext.species}) named {request.petContext.name}.\n\n"
        "INSTRUCTIONS:\n"
        "- Keep your response extremely concise, brief, and structured for a small mobile screen.\n"
        "- FIRST LINE: Output an estimated 'Severity Assessment:' (Mild, Moderate, or Severe/Emergency) based on the symptoms. \n"
        "- Give 2-3 immediate, actionable home care tips or things to monitor.\n"
        "- DO NOT USE MARKDOWN. Do not use asterisks (*) for bolding or bullets. Use standard dashes (-) for lists.\n"
        "- IMPORTANT: You MUST end with a single-sentence disclaimer: "
        "'Disclaimer: I am an AI assistant. Please consult a qualified vet for proper medical diagnosis.'"
    )

    contents = [prompt]
    if request.type == "text":
        contents.append(f"Symptoms: {request.value}")
    elif request.type == "image":
        try:
            _, encoded = request.value.split(",", 1)
            contents.append(Image.open(BytesIO(base64.b64decode(encoded))))
        except Exception:
            raise HTTPException(status_code=400, detail="Failed to process image.")

    try:
        response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=contents)
        return {"analysis": response.text}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to generate AI response.")

# ── USER ROUTES (protected) ───────────────────────────────────────────────────

@app.put("/api/users/{email}/username")
async def update_username(email: str, request: UsernameUpdate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    # Normalize the email for a secure comparison
    if email.lower().strip() != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
        
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    user.username = request.new_username
    db.commit()
    return {"message": "Username updated successfully!"}
