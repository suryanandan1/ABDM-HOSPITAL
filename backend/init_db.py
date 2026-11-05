"""Migrate the database to the latest schema, then create roles, the first hospital and its administrator.

Usage:
    python init_db.py            # safe to run repeatedly; applies new migrations, only adds what is missing
    python init_db.py --demo     # also adds sample staff, doctors and patients

Schema changes are Alembic migrations in migrations/versions (create one with
`alembic revision --autogenerate -m "..."` after changing app/models.py, then review it).
"""

import sys
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Doctor, Hospital, LabTest, Patient, Role, User
from app.security import ROLES, hash_password

DEMO_PASSWORD = "Demo@1234"
EXAMPLE_ADMIN_PASSWORD = "ChangeMe123"  # the value in .env.example
BASELINE_REVISION = "0001"  # the schema as created by create_all before migrations existed

# A starter laboratory catalogue (reference ranges are typical adult values; adjust to the lab's own).
DEMO_LAB_TESTS = [
    {"code": "CBC", "name": "Complete Blood Count", "category": "Haematology", "sample_type": "Whole blood (EDTA)",
     "loinc_code": "58410-2", "parameters": [
         {"code": "HB", "name": "Haemoglobin", "unit": "g/dL", "loinc_code": "718-7", "ref_low": 12, "ref_high": 17},
         {"code": "WBC", "name": "Total leucocyte count", "unit": "10^3/uL", "loinc_code": "6690-2", "ref_low": 4, "ref_high": 11},
         {"code": "PLT", "name": "Platelet count", "unit": "10^3/uL", "loinc_code": "777-3", "ref_low": 150, "ref_high": 450},
     ]},
    {"code": "FBS", "name": "Fasting Blood Glucose", "category": "Biochemistry", "sample_type": "Plasma (fluoride)",
     "loinc_code": "1558-6", "parameters": [
         {"code": "GLU", "name": "Glucose, fasting", "unit": "mg/dL", "loinc_code": "1558-6", "ref_low": 70, "ref_high": 100},
     ]},
    {"code": "LIPID", "name": "Lipid Profile", "category": "Biochemistry", "sample_type": "Serum",
     "loinc_code": "57698-3", "parameters": [
         {"code": "CHOL", "name": "Total cholesterol", "unit": "mg/dL", "loinc_code": "2093-3", "ref_high": 200},
         {"code": "TG", "name": "Triglycerides", "unit": "mg/dL", "loinc_code": "2571-8", "ref_high": 150},
         {"code": "HDL", "name": "HDL cholesterol", "unit": "mg/dL", "loinc_code": "2085-9", "ref_low": 40},
         {"code": "LDL", "name": "LDL cholesterol", "unit": "mg/dL", "loinc_code": "13457-7", "ref_high": 100},
     ]},
    {"code": "URINE-RM", "name": "Urine Routine", "category": "Clinical pathology", "sample_type": "Urine",
     "loinc_code": "24356-8", "parameters": [
         {"code": "PROT", "name": "Urine protein", "loinc_code": "2888-6", "ref_text": "Negative"},
         {"code": "SUGAR", "name": "Urine glucose", "loinc_code": "2350-7", "ref_text": "Negative"},
     ]},
]


def seed(db: Session, hospital_name: str, hospital_code: str, admin_username: str, admin_password: str) -> Hospital:
    for code, role in ROLES.items():
        existing = db.scalar(select(Role).where(Role.code == code))
        if existing:
            existing.name, existing.description = role["name"], role["description"]
        else:
            db.add(Role(code=code, name=role["name"], description=role["description"]))
    db.flush()

    hospital = db.scalar(select(Hospital).where(Hospital.code == hospital_code))
    if hospital is None:
        hospital = Hospital(name=hospital_name, code=hospital_code)
        db.add(hospital)
        db.flush()
        print(f"Created hospital {hospital_name} ({hospital_code})")

    if db.scalar(select(User).where(User.username == admin_username)) is None:
        admin_role = db.scalar(select(Role).where(Role.code == "hospital_admin"))
        db.add(User(hospital_id=hospital.id, role_id=admin_role.id, username=admin_username,
                    password_hash=hash_password(admin_password), full_name="Hospital Administrator"))
        print(f"Created administrator '{admin_username}'")
    db.commit()
    return hospital


def seed_demo(db: Session, hospital: Hospital) -> None:
    roles = {r.code: r for r in db.scalars(select(Role))}
    staff = [("dr.mehta", "Dr. Anil Mehta", "doctor"), ("dr.rao", "Dr. Sunita Rao", "doctor"),
             ("reception", "Priya Sharma", "receptionist"),
             ("labtech", "Rahul Verma", "lab_technician"), ("hims", "Kavita Iyer", "health_info_staff")]
    for username, name, role in staff:
        if db.scalar(select(User).where(User.username == username)) is None:
            db.add(User(hospital_id=hospital.id, role_id=roles[role].id, username=username,
                        password_hash=hash_password(DEMO_PASSWORD), full_name=name))
    db.flush()

    if db.scalar(select(Doctor).where(Doctor.hospital_id == hospital.id)) is None:
        mehta = db.scalar(select(User).where(User.username == "dr.mehta"))
        rao = db.scalar(select(User).where(User.username == "dr.rao"))
        db.add_all([
            Doctor(hospital_id=hospital.id, user_id=mehta.id, full_name="Dr. Anil Mehta",
                   registration_number="DMC-45821", specialization="General Medicine",
                   qualification="MBBS, MD", department="Medicine"),
            Doctor(hospital_id=hospital.id, user_id=rao.id, full_name="Dr. Sunita Rao", registration_number="KMC-77310",
                   specialization="Paediatrics", qualification="MBBS, DCH", department="Paediatrics"),
        ])

    if db.scalar(select(LabTest).where(LabTest.hospital_id == hospital.id)) is None:
        db.add_all(LabTest(hospital_id=hospital.id, **test) for test in DEMO_LAB_TESTS)

    if db.scalar(select(Patient).where(Patient.hospital_id == hospital.id)) is None:
        reception = db.scalar(select(User).where(User.username == "reception"))
        samples = [("Ramesh Kumar", date(1978, 4, 12), "M", "9876543210", "Mumbai", "Maharashtra"),
                   ("Sita Devi", date(1985, 9, 3), "F", "9123456780", "Pune", "Maharashtra"),
                   ("Arjun Singh", date(2012, 1, 25), "M", "9988776655", "Nagpur", "Maharashtra")]
        for name, dob, gender, mobile, city, state in samples:
            patient = Patient(hospital_id=hospital.id, patient_code=f"tmp-{mobile}", full_name=name,
                              date_of_birth=dob, year_of_birth=dob.year, gender=gender, mobile=mobile,
                              city=city, state=state, registered_by_id=reception.id)
            db.add(patient)
            db.flush()
            patient.patient_code = f"{hospital.code}-{patient.id:06d}"
    db.commit()
    print(f"Demo data ready. Demo users: {', '.join(u for u, _, _ in staff)} (password {DEMO_PASSWORD})")


def migrate(bind: Engine = engine) -> None:
    """Bring the database to the latest migration. A database created before migrations existed (tables but no
    alembic_version) is first marked as being at the baseline, so only the newer migrations run on it."""
    config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    config.attributes["configure_logger"] = False
    with bind.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if "users" in tables and "alembic_version" not in tables:
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


def main() -> None:
    if not settings.admin_password:
        sys.exit("Set ADMIN_PASSWORD in backend/.env before running init_db.py")
    if settings.admin_password == EXAMPLE_ADMIN_PASSWORD:
        sys.exit("ADMIN_PASSWORD still has the example value from .env.example; choose a strong password")
    migrate()
    with SessionLocal() as db:
        hospital = seed(db, settings.hospital_name, settings.hospital_code.upper(),
                        settings.admin_username.lower(), settings.admin_password)
        if "--demo" in sys.argv:
            seed_demo(db, hospital)
    print("Database initialised.")


if __name__ == "__main__":
    main()
