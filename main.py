import os
import random
import base64
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
from PIL import Image
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from typing import List, Optional

# --- IMPORT THE GEMINI SDK ---
from google import genai

app = FastAPI(title="PetCare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 1. DATABASE, EMAIL & AI CONNECTION SETUP ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Email Configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

try:
    # ADDED: pool_pre_ping and pool_recycle to prevent Neon SSL drop errors!
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    connection = engine.connect()
    connection.close()
    print("Successfully connected to Neon PostgreSQL!")
except Exception as e:
    print(f"Database connection failed: {e}")

if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    print("Successfully connected to Gemini AI!")
else:
    print("WARNING: GEMINI_API_KEY not found in .env file.")

# --- 2. DATABASE SESSION & MODELS ---
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), unique=True, index=True, nullable=False)
    username = Column(String(50), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class OTPCode(Base):
    __tablename__ = "otp_codes"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), index=True, nullable=False)
    otp_code = Column(String(6), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Pet(Base):
    __tablename__ = "pets"
    id = Column(Integer, primary_key=True, index=True)
    owner_email = Column(String(100), index=True, nullable=False) 
    pet_type = Column(String(50), nullable=False)
    breed = Column(String(100), nullable=False)
    name = Column(String(50), nullable=False)
    age = Column(String(50), nullable=False)

class Vaccine(Base):
    __tablename__ = "vaccines"
    id = Column(Integer, primary_key=True, index=True)
    pet_id = Column(Integer, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    date = Column(String(50), nullable=False)
    owner_email = Column(String(100), index=True, nullable=False)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- UTILS ---
def verify_password(plain_password: str, hashed_password: str):
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

def get_password_hash(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def send_email_sync(to_email: str, otp: str):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print(f"SMTP missing. Pretend email sent to {to_email} with code: {otp}")
        return
        
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = to_email
        msg['Subject'] = "PetCare - Your Verification Code"

        body = f"Hello!\n\nYour PetCare verification code is: {otp}\n\nThis code will expire in 10 minutes.\n\nBest regards,\nThe PetCare Team"
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"Email successfully sent to {to_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# --- 3. PYDANTIC SCHEMAS ---
class EmailRequest(BaseModel):
    email: EmailStr
    purpose: Optional[str] = "login"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UsernameUpdate(BaseModel):
    new_username: str

class VerifyRequest(BaseModel):
    email: EmailStr
    otp_code: str
    username: Optional[str] = None
    password: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp_code: str
    new_password: str

class PetRequest(BaseModel):
    owner_email: EmailStr
    pet_type: str
    breed: str
    name: str
    age: str

class PetUpdate(BaseModel):
    pet_type: str
    breed: str
    name: str
    age: str

class VaccineCreate(BaseModel):
    pet_id: int
    name: str
    date: str
    owner_email: EmailStr

class VaccineUpdate(BaseModel):
    date: str

class PetContext(BaseModel):
    name: str
    species: str
    breed: str
    age: str

class AnalyzeRequest(BaseModel):
    type: str
    value: str
    petContext: PetContext


# --- 4. API ROUTES ---

@app.get("/")
async def welcome():
    return {"message": "Welcome to the PetCare API!"}

@app.post("/api/check-credentials")
async def check_credentials(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Wrong Credentials!")
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

    generated_otp = str(random.randint(100000, 999999))
    new_otp_entry = OTPCode(email=request.email, otp_code=generated_otp)

    db.add(new_otp_entry)
    db.commit()
    
    # Hand the email sending process to a background task so the frontend doesn't hang!
    background_tasks.add_task(send_email_sync, request.email, generated_otp)
    
    return {"message": "OTP successfully generated and queued for sending!"}

@app.post("/api/verify-login")
async def verify_login(request: VerifyRequest, db: Session = Depends(get_db)):
    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    
    if not recent_otp:
        raise HTTPException(status_code=404, detail="No OTP found for this email.")
        
    time_elapsed = datetime.utcnow() - recent_otp.created_at
    if time_elapsed > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="This OTP has expired. Please request a new one.")
        
    if recent_otp.otp_code != request.otp_code:
        raise HTTPException(status_code=401, detail="Invalid OTP code. Please try again.")
        
    user = db.query(User).filter(User.email == request.email).first()
    
    if user:
        true_username = user.username
    else:
        if not request.password:
            raise HTTPException(status_code=400, detail="Password is required to sign up.")
            
        hashed = get_password_hash(request.password)
        new_user = User(email=request.email, username=request.username, hashed_password=hashed)
        db.add(new_user)
        db.commit()
        true_username = request.username
        
    return {"message": "Login successful!", "username": true_username}

@app.post("/api/verify-otp")
async def verify_otp_only(request: VerifyRequest, db: Session = Depends(get_db)):
    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    
    if not recent_otp:
        raise HTTPException(status_code=404, detail="No OTP found for this email.")
        
    time_elapsed = datetime.utcnow() - recent_otp.created_at
    if time_elapsed > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="This OTP has expired. Please request a new one.")
        
    if recent_otp.otp_code != request.otp_code:
        raise HTTPException(status_code=401, detail="Invalid OTP code. Please try again.")
        
    return {"message": "OTP verified successfully!"}

@app.post("/api/reset-password")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    recent_otp = db.query(OTPCode).filter(OTPCode.email == request.email).order_by(OTPCode.created_at.desc()).first()
    
    if not recent_otp or recent_otp.otp_code != request.otp_code:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
        
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    user.hashed_password = get_password_hash(request.new_password)
    db.commit()
    
    return {"message": "Password updated successfully!"}

@app.get("/api/pets/{email}")
async def get_user_pets(email: str, db: Session = Depends(get_db)):
    user_pets = db.query(Pet).filter(Pet.owner_email == email).all()
    return user_pets

@app.post("/api/pets")
async def add_pet(request: PetRequest, db: Session = Depends(get_db)):
    new_pet = Pet(
        owner_email=request.owner_email,
        pet_type=request.pet_type,
        breed=request.breed,
        name=request.name,
        age=request.age
    )
    db.add(new_pet)
    db.commit()
    return {"message": "Pet added successfully!"}

@app.delete("/api/pets/{pet_id}")
async def delete_pet(pet_id: int, db: Session = Depends(get_db)):
    pet_to_delete = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet_to_delete:
        raise HTTPException(status_code=404, detail="Pet not found.")
    db.delete(pet_to_delete)
    db.commit()
    return {"message": "Pet deleted successfully!"}

@app.put("/api/pets/{pet_id}")
async def update_pet(pet_id: int, request: PetUpdate, db: Session = Depends(get_db)):
    pet_to_update = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet_to_update:
        raise HTTPException(status_code=404, detail="Pet not found.")
        
    pet_to_update.pet_type = request.pet_type
    pet_to_update.breed = request.breed
    pet_to_update.name = request.name
    pet_to_update.age = request.age
    
    db.commit()
    return {"message": "Pet updated successfully!"}

@app.get("/api/diet/{email}")
async def get_diet_plan(email: str, db: Session = Depends(get_db)):
    pets = db.query(Pet).filter(Pet.owner_email == email).all()
    if not pets:
        return {"diets": []}

    diet_plans = []
    for pet in pets:
        food_type = "Standard Pet Food"
        meals = "2 times a day"
        avoid = "Human junk food, overly spicy food"

        if pet.pet_type == "Dog":
            avoid = "Chocolate, Grapes, Onions, Garlic, Indian Sweets (high sugar/ghee)"
            if "month" in pet.age.lower():
                food_type = "Puppy Kibble (e.g., Drools Puppy or Pedigree PRO) mixed with a little warm water or plain curd."
                meals = "3 to 4 small meals a day"
            else:
                food_type = "Adult Dog Food (e.g., Royal Canin, Purepet) or home-cooked plain boiled chicken and rice."
        elif pet.pet_type == "Cat":
            avoid = "Dairy (Milk can cause upset stomachs), Raw Fish, Spiced Meats"
            if "month" in pet.age.lower():
                food_type = "Kitten Wet/Dry Mix (e.g., Whiskas Kitten or Meat Up). Can mix with plain unseasoned boiled chicken broth."
                meals = "3 times a day"
            else:
                food_type = "Adult Cat Food (e.g., Drools, Whiskas) or freshly boiled, de-boned local white fish."
        elif pet.pet_type == "Bird":
            avoid = "Avocado, Apple Seeds, Caffeine, Salty Snacks"
            food_type = "Local Seed Mix (Bajra/Kangni), soaked chana, and fresh local fruits like papaya or guava."
            meals = "Available all day (Refill daily)"

        diet_plans.append({
            "pet_name": pet.name,
            "pet_type": pet.pet_type,
            "food": food_type,
            "meals": meals,
            "avoid": avoid
        })
    return {"diets": diet_plans}

@app.post("/api/vaccines")
async def create_vaccine(vax: VaccineCreate, db: Session = Depends(get_db)):
    new_vax = Vaccine(
        pet_id=vax.pet_id,
        name=vax.name,
        date=vax.date,
        owner_email=vax.owner_email
    )
    db.add(new_vax)
    db.commit()
    db.refresh(new_vax) 
    return new_vax

@app.get("/api/vaccines/{email}")
async def get_vaccines(email: str, db: Session = Depends(get_db)):
    user_vaxes = db.query(Vaccine).filter(Vaccine.owner_email == email).all()
    return user_vaxes

@app.put("/api/vaccines/{vax_id}")
async def update_vaccine(vax_id: int, update_data: VaccineUpdate, db: Session = Depends(get_db)):
    vax_to_update = db.query(Vaccine).filter(Vaccine.id == vax_id).first()
    if not vax_to_update:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    vax_to_update.date = update_data.date
    db.commit()
    return {"message": "Vaccine updated successfully"}

@app.delete("/api/vaccines/{vax_id}")
async def delete_vaccine(vax_id: int, db: Session = Depends(get_db)):
    vax_to_delete = db.query(Vaccine).filter(Vaccine.id == vax_id).first()
    if not vax_to_delete:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    db.delete(vax_to_delete)
    db.commit()
    return {"message": "Vaccine deleted successfully"}

# ==========================================
# 5. DIAGNOSTIC ROUTE
# ==========================================
@app.post("/api/analyze-health")
async def analyze_health(request: AnalyzeRequest):
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
        except Exception as e:
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
    
@app.put("/api/users/{email}/username")
async def update_username(email: str, request: UsernameUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    user.username = request.new_username
    db.commit()
    
    return {"message": "Username updated successfully!"}