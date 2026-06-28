import os
import secrets
import base64
import hashlib as _sha256_compat  # Only kept for migrating legacy SHA-256 passwords
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
from pydantic import BaseModel, EmailStr
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
# SECRET_KEY must be set in .env for production; the fallback is random per-restart
SECRET_KEY      = os.getenv("SECRET_KEY")
ALGORITHM       = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

SMTP_SERVER   = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,        # No more wildcard "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── DATABASE ───────────────────────────────────────────────────────────────────
try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    engine.connect().close()
    print("Successfully connected to Neon PostgreSQL!")
except Exception as e:
    print(f"Database connection failed: {e}")

if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    print("Successfully connected to Gemini AI!")
else:
    print("WARNING: GEMINI_API_KEY not found in .env file.")

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
    """Accepts bcrypt hashes (new) and SHA-256 hashes (legacy migration path)."""
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
        raise HTTPException(status_code=401, detail="Token is invalid or has expired. Please log in again.")
    return email

# ── OTP BRUTE-FORCE PROTECTION (in-memory; resets on server restart) ──────────
# Structure: { email: {"count": int, "locked_until": datetime | None} }
_otp_attempts: dict = {}
MAX_OTP_ATTEMPTS    = 5
OTP_LOCKOUT_MINUTES = 15

def _check_otp_lockout(email: str):
    record = _otp_attempts.get(email, {"count": 0, "locked_until": None})
    if record["locked_until"] and datetime.utcnow() < record["locked_until"]:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Please wait {OTP_LOCKOUT_MINUTES} minutes before trying again."
        )

def _record_otp_failure(email: str):
    record = _otp_attempts.get(email, {"count": 0, "locked_until": None})
    record["count"] = record.get("count", 0) + 1
    if record["count"] >= MAX_OTP_ATTEMPTS:
        record["locked_until"] = datetime.utcnow() + timedelta(minutes=OTP_LOCKOUT_MINUTES)
        record["count"] = 0
    _otp_attempts[email] = record

def _clear_otp_attempts(email: str):
    _otp_attempts.pop(email, None)

# ── EMAIL ──────────────────────────────────────────────────────────────────────
def send_email_sync(to_email: str, otp: str):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print(f"SMTP missing. Pretend email sent to {to_email} with code: {otp}")
        return
    try:
        msg = MIMEMultipart()
        msg['From']    = SMTP_USERNAME
        msg['To']      = to_email
        msg['Subject'] = "PetCare - Your Verification Code"
        body = (
            f"Hello!\n\nYour PetCare verification code is: {otp}\n\n"
            "This code will expire in 10 minutes.\n\nBest regards,\nThe PetCare Team"
        )
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"Email successfully sent to {to_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# ── PYDANTIC SCHEMAS ───────────────────────────────────────────────────────────
class EmailRequest(BaseModel):
    email: EmailStr
    purpose: Optional[str] = "login"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UsernameUpdate(BaseModel):
    new_username: str

class VerifyRequest(BaseModel):
    email:     EmailStr
    otp_code:  str
    username:  Optional[str] = None
    password:  Optional[str] = None

class ResetPasswordRequest(BaseModel):
    email:        EmailStr
    otp_code:     str
    new_password: str

class PetRequest(BaseModel):
    owner_email: Optional[EmailStr] = None  # ignored; owner derived from JWT
    pet_type: str
    breed:    str
    name:     str
    age:      str

class PetUpdate(BaseModel):
    pet_type: str
    breed:    str
    name:     str
    age:      str

class VaccineCreate(BaseModel):
    pet_id:      int
    name:        str
    date:        str
    owner_email: Optional[EmailStr] = None  # ignored; owner derived from JWT

class VaccineUpdate(BaseModel):
    date: str

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

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.get("/")
async def welcome():
    return {"message": "Welcome to the PetCare API!"}

# ── AUTH ROUTES (public) ──────────────────────────────────────────────────────

@app.post("/api/check-credentials")
async def check_credentials(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Wrong Credentials!")
    # Silently upgrade legacy SHA-256 hash to bcrypt on successful login
    if not user.hashed_password.startswith(("$2b$", "$2a$", "$2y$")):
        user.hashed_password = get_password_hash(request.password)
        db.commit()
    return {"message": "Credentials valid!"}

@app.post("/api/send-otp")
async def send_otp(request: EmailRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if request.purpose == "signup":
        if db.query(User).filter(User.email == request.email).first():
            raise HTTPException(status_code=400, detail="Email already registered. Please login.")
    if request.purpose == "forgot":
        if not db.query(User).filter(User.email == request.email).first():
            raise HTTPException(status_code=404, detail="Account not found. Please sign up.")

    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    if recent_otp:
        time_since_last_otp = datetime.utcnow() - recent_otp.created_at
        if time_since_last_otp < timedelta(minutes=1):
            raise HTTPException(status_code=400, detail="Please wait 1 minute before requesting a new OTP.")

    # Use cryptographically secure random OTP
    generated_otp = str(secrets.randbelow(900000) + 100000)
    db.add(OTPCode(email=request.email, otp_code=generated_otp))
    db.commit()
    background_tasks.add_task(send_email_sync, request.email, generated_otp)
    return {"message": "OTP successfully generated and queued for sending!"}

@app.post("/api/verify-login")
async def verify_login(request: VerifyRequest, db: Session = Depends(get_db)):
    _check_otp_lockout(request.email)

    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    if not recent_otp:
        raise HTTPException(status_code=404, detail="No OTP found for this email.")

    time_elapsed = datetime.utcnow() - recent_otp.created_at
    if time_elapsed > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="This OTP has expired. Please request a new one.")

    if recent_otp.otp_code != request.otp_code:
        _record_otp_failure(request.email)
        raise HTTPException(status_code=401, detail="Invalid OTP code. Please try again.")

    # OTP is valid — invalidate it immediately to prevent reuse
    db.delete(recent_otp)
    db.commit()
    _clear_otp_attempts(request.email)

    user = db.query(User).filter(User.email == request.email).first()
    if user:
        true_username = user.username
    else:
        if not request.password:
            raise HTTPException(status_code=400, detail="Password is required to sign up.")
        _validate_password_strength(request.password)
        hashed = get_password_hash(request.password)
        new_user = User(email=request.email, username=request.username or "PawCare User", hashed_password=hashed)
        db.add(new_user)
        db.commit()
        true_username = request.username or "PawCare User"

    token = create_access_token(request.email)
    return {"message": "Login successful!", "username": true_username, "token": token}

@app.post("/api/verify-otp")
async def verify_otp_only(request: VerifyRequest, db: Session = Depends(get_db)):
    _check_otp_lockout(request.email)

    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    if not recent_otp:
        raise HTTPException(status_code=404, detail="No OTP found for this email.")

    time_elapsed = datetime.utcnow() - recent_otp.created_at
    if time_elapsed > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="This OTP has expired. Please request a new one.")

    if recent_otp.otp_code != request.otp_code:
        _record_otp_failure(request.email)
        raise HTTPException(status_code=401, detail="Invalid OTP code. Please try again.")

    _clear_otp_attempts(request.email)
    # Do NOT delete OTP here — reset-password endpoint needs to verify it again
    return {"message": "OTP verified successfully!"}

@app.post("/api/reset-password")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    _validate_password_strength(request.new_password)

    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    if not recent_otp:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")

    # FIX: check expiry (was missing before)
    time_elapsed = datetime.utcnow() - recent_otp.created_at
    if time_elapsed > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="This OTP has expired. Please request a new one.")

    if recent_otp.otp_code != request.otp_code:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")

    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.hashed_password = get_password_hash(request.new_password)
    db.delete(recent_otp)  # Invalidate OTP after use
    db.commit()
    return {"message": "Password updated successfully!"}

# ── PET ROUTES (protected) ────────────────────────────────────────────────────

@app.get("/api/pets/{email}")
async def get_user_pets(email: str, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    if email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    return db.query(Pet).filter(Pet.owner_email == email).all()

@app.post("/api/pets")
async def add_pet(request: PetRequest, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    new_pet = Pet(
        owner_email=current_email,   # Always use token identity, ignore request body
        pet_type=request.pet_type,
        breed=request.breed,
        name=request.name,
        age=request.age,
    )
    db.add(new_pet)
    db.commit()
    return {"message": "Pet added successfully!"}

@app.delete("/api/pets/{pet_id}")
async def delete_pet(pet_id: int, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found.")
    if pet.owner_email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    db.delete(pet)
    db.commit()
    return {"message": "Pet deleted successfully!"}

@app.put("/api/pets/{pet_id}")
async def update_pet(pet_id: int, request: PetUpdate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found.")
    if pet.owner_email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    pet.pet_type = request.pet_type
    pet.breed    = request.breed
    pet.name     = request.name
    pet.age      = request.age
    db.commit()
    return {"message": "Pet updated successfully!"}

# ── DIET ROUTE (protected) ────────────────────────────────────────────────────

@app.get("/api/diet/{email}")
async def get_diet_plan(email: str, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    if email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    pets = db.query(Pet).filter(Pet.owner_email == email).all()
    if not pets:
        return {"diets": []}

    diet_plans = []
    for pet in pets:
        food_type = "Standard Pet Food"
        meals     = "2 times a day"
        avoid     = "Human junk food, overly spicy food"

        if pet.pet_type == "Dog":
            avoid = "Chocolate, Grapes, Onions, Garlic, Indian Sweets (high sugar/ghee)"
            if "month" in pet.age.lower():
                food_type = "Puppy Kibble (e.g., Drools Puppy or Pedigree PRO) mixed with a little warm water or plain curd."
                meals     = "3 to 4 small meals a day"
            else:
                food_type = "Adult Dog Food (e.g., Royal Canin, Purepet) or home-cooked plain boiled chicken and rice."
        elif pet.pet_type == "Cat":
            avoid = "Dairy (Milk can cause upset stomachs), Raw Fish, Spiced Meats"
            if "month" in pet.age.lower():
                food_type = "Kitten Wet/Dry Mix (e.g., Whiskas Kitten or Meat Up). Can mix with plain unseasoned boiled chicken broth."
                meals     = "3 times a day"
            else:
                food_type = "Adult Cat Food (e.g., Drools, Whiskas) or freshly boiled, de-boned local white fish."
        elif pet.pet_type == "Bird":
            avoid     = "Avocado, Apple Seeds, Caffeine, Salty Snacks"
            food_type = "Local Seed Mix (Bajra/Kangni), soaked chana, and fresh local fruits like papaya or guava."
            meals     = "Available all day (Refill daily)"

        diet_plans.append({
            "pet_name": pet.name,
            "pet_type": pet.pet_type,
            "food":     food_type,
            "meals":    meals,
            "avoid":    avoid,
        })
    return {"diets": diet_plans}

# ── VACCINE ROUTES (protected) ────────────────────────────────────────────────

@app.post("/api/vaccines")
async def create_vaccine(vax: VaccineCreate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    new_vax = Vaccine(
        pet_id=vax.pet_id,
        name=vax.name,
        date=vax.date,
        owner_email=current_email,   # Always use token identity
    )
    db.add(new_vax)
    db.commit()
    db.refresh(new_vax)
    return new_vax

@app.get("/api/vaccines/{email}")
async def get_vaccines(email: str, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    if email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    return db.query(Vaccine).filter(Vaccine.owner_email == email).all()

@app.put("/api/vaccines/{vax_id}")
async def update_vaccine(vax_id: int, update_data: VaccineUpdate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    vax = db.query(Vaccine).filter(Vaccine.id == vax_id).first()
    if not vax:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    if vax.owner_email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    vax.date = update_data.date
    db.commit()
    return {"message": "Vaccine updated successfully"}

@app.delete("/api/vaccines/{vax_id}")
async def delete_vaccine(vax_id: int, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    vax = db.query(Vaccine).filter(Vaccine.id == vax_id).first()
    if not vax:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    if vax.owner_email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    db.delete(vax)
    db.commit()
    return {"message": "Vaccine deleted successfully"}

# ── AI DIAGNOSTIC ROUTE (protected) ──────────────────────────────────────────

@app.post("/api/analyze-health")
async def analyze_health(request: AnalyzeRequest, current_email: str = Depends(get_current_email)):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key is missing from the server.")

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
        contents.append(f"Here are the symptoms reported by the owner: {request.value}")
    elif request.type == "image":
        try:
            header, encoded = request.value.split(",", 1)
            image_data = base64.b64decode(encoded)
            image = Image.open(BytesIO(image_data))
            contents.append("Analyze this image of the pet. Identify any visible skin conditions, injuries, or unusual signs.")
            contents.append(image)
        except Exception:
            raise HTTPException(status_code=400, detail="Failed to process the uploaded image.")

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
        )
        return {"analysis": response.text}
    except Exception as e:
        print(f"Gemini API Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate AI response.")

# ── USER ROUTES (protected) ───────────────────────────────────────────────────

@app.put("/api/users/{email}/username")
async def update_username(email: str, request: UsernameUpdate, db: Session = Depends(get_db), current_email: str = Depends(get_current_email)):
    if email != current_email:
        raise HTTPException(status_code=403, detail="Access denied.")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    user.username = request.new_username
    db.commit()
    return {"message": "Username updated successfully!"}
