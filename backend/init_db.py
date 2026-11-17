"""Migrate the database to the latest schema, then create roles, the first hospital and its administrator.

Usage:
    python init_db.py            # safe to run repeatedly; applies new migrations, only adds what is missing
    python init_db.py --demo     # also adds sample staff, doctors and patients, two more hospitals (each with
                                 # staff, doctors and a lab catalogue) and shared patients with ABHA addresses
                                 # such as surya@abdm (see app/demo.py); then scripts/seed_shared_records.py
                                 # adds shareable records for the HIP/HIU exchange between the hospitals

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

from app import demo
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


def seed_network(db: Session, primary: Hospital) -> None:
    """The demo ABDM network (app/demo.py): two more hospitals with staff, doctors and a lab catalogue, and the demo
    people registered at each of their hospitals with their one ABHA address. Only adds what is missing."""
    roles = {r.code: r for r in db.scalars(select(Role))}
    simulation = settings.abdm_mode == "simulation"
    hospitals = {demo.PRIMARY: primary}
    for code, profile in demo.HOSPITALS.items():
        hospital = primary if code == demo.PRIMARY else db.scalar(select(Hospital).where(Hospital.code == code))
        if hospital is None:
            hospital = Hospital(code=code, name=profile["name"])
            db.add(hospital)
            print(f"Created hospital {profile['name']} ({code})")
        for field in ("city", "state", "facility_type", "address", "pincode", "phone"):
            if field in profile and not getattr(hospital, field):
                setattr(hospital, field, profile[field])
        if simulation:  # real HIP/HIU IDs are issued by NHA; set them on the Hospital profile in sandbox mode
            hip_id = profile["hip_id"] if code == hospital.code else f"SIM_HIP_{hospital.code}"
            hiu_id = profile["hiu_id"] if code == hospital.code else f"SIM_HIU_{hospital.code}"
            hospital.abdm_hip_id = hospital.abdm_hip_id or hip_id
            hospital.abdm_hiu_id = hospital.abdm_hiu_id or hiu_id
        db.flush()
        hospitals[code] = hospital

    for code, staff in demo.STAFF.items():
        hospital = hospitals[code]
        for username, name, role, doctor in staff:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(hospital_id=hospital.id, role_id=roles[role].id, username=username, full_name=name,
                            password_hash=hash_password(DEMO_PASSWORD))
                db.add(user)
                db.flush()
            if doctor and db.scalar(select(Doctor).where(Doctor.user_id == user.id)) is None:
                db.add(Doctor(hospital_id=hospital.id, user_id=user.id, full_name=name, **doctor))
        if db.scalar(select(LabTest).where(LabTest.hospital_id == hospital.id)) is None:
            db.add_all(LabTest(hospital_id=hospital.id, **test) for test in DEMO_LAB_TESTS)
    db.flush()

    for person in demo.PEOPLE:
        for code in person["hospitals"]:
            hospital = hospitals[code]
            patient = db.scalar(select(Patient).where(Patient.hospital_id == hospital.id,
                                                      Patient.full_name == person["full_name"],
                                                      Patient.mobile == person["mobile"]))
            if patient is None:
                receptionist = db.scalar(select(User).join(Role).where(User.hospital_id == hospital.id,
                                                                       Role.code == "receptionist").order_by(User.id))
                patient = Patient(hospital_id=hospital.id, patient_code=f"tmp-{code}-{person['mobile']}",
                                  full_name=person["full_name"], date_of_birth=person["date_of_birth"],
                                  year_of_birth=person["date_of_birth"].year, gender=person["gender"],
                                  mobile=person["mobile"], city=person["city"], state=person["state"],
                                  registered_by_id=receptionist.id if receptionist else None)
                db.add(patient)
                db.flush()
                patient.patient_code = f"{hospital.code}-{patient.id:06d}"
            taken = db.scalar(select(Patient.id).where(Patient.hospital_id == hospital.id, Patient.id != patient.id,
                                                       Patient.abha_address == person["abha_address"]))
            if not patient.abha_address and not taken:  # as on the patient's ABHA card; verified later with ABDM
                patient.abha_address, patient.abha_number = person["abha_address"], person["abha_number"]
    db.commit()
    print("Demo network ready:")
    for code, hospital in hospitals.items():
        logins = ("admin, dr.mehta, dr.rao, reception, labtech, hims" if code == demo.PRIMARY
                  else ", ".join(u for u, *_ in demo.STAFF[code]))
        print(f"  {hospital.name} ({hospital.code}) HIP {hospital.abdm_hip_id or '-'} / HIU {hospital.abdm_hiu_id or '-'}: {logins}")
    print("  Patients and their ABHA addresses: " + ", ".join(
        f"{p['full_name']} {p['abha_address']} ({'/'.join(p['hospitals'])})" for p in demo.PEOPLE))


def migrate(bind: Engine = engine) -> None:
    """Bring the database to the latest migration. A database created before migrations existed (tables but no
    alembic_version) is first marked as being at the baseline, so only the newer migrations run on it."""
    config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    config.attributes["configure_logger"] = False
    sqlite = bind.dialect.name == "sqlite"
    with bind.connect() as connection:
        if sqlite:
            # Batch migrations rebuild a table (copy, drop, rename). With foreign keys enforced SQLite refuses to
            # drop a table other rows point at, so enforcement is off during migrations (it can only be switched
            # outside a transaction); rows keep their ids, and the check below proves every reference is intact.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                tables = set(inspect(connection).get_table_names())
                if "users" in tables and "alembic_version" not in tables:
                    command.stamp(config, BASELINE_REVISION)
                command.upgrade(config, "head")
                if sqlite:
                    broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    if broken:
                        raise RuntimeError(f"Migration left broken references, not applied: {broken[:5]}")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()


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
            seed_network(db, hospital)
    print("Database initialised.")


if __name__ == "__main__":
    main()
