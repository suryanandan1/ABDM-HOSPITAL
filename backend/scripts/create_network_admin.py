"""Create a network administrator: the one account that manages every hospital (profiles, ABDM IDs, activation and
each hospital's staff accounts). It belongs to no hospital and cannot see patient or clinical data.

Usage, from the project folder or from backend/:  python backend\\scripts\\create_network_admin.py [username]
(default username: superadmin). The password is asked for without being shown. Run init_db.py first.
To change the password later: python backend\\scripts\\reset_password.py superadmin
"""

import getpass
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(BACKEND)  # .env and the default sqlite:///./hospital.db are relative to backend/
sys.path.insert(0, str(BACKEND))

from pydantic import TypeAdapter, ValidationError  # noqa: E402
from sqlalchemy import inspect, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.schemas import Password  # noqa: E402
from app.security import NETWORK_ADMIN, ROLES, hash_password  # noqa: E402


def main() -> None:
    username = (sys.argv[1] if len(sys.argv) > 1 else "superadmin").lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,50}", username):
        sys.exit("Username: 3-50 letters, digits, dots, dashes or underscores")
    with SessionLocal() as db:
        nullable = {c["name"]: c["nullable"] for c in inspect(db.get_bind()).get_columns("users")}
        if not nullable.get("hospital_id"):
            sys.exit("The database is not upgraded yet: run python init_db.py --demo first")
        if db.scalar(select(User.id).where(User.username == username)):
            sys.exit(f"{username} already exists. To change its password: python scripts\\reset_password.py {username}")
        role = db.scalar(select(Role).where(Role.code == NETWORK_ADMIN))
        if role is None:
            info = ROLES[NETWORK_ADMIN]
            role = Role(code=NETWORK_ADMIN, name=info["name"], description=info["description"])
            db.add(role)
        full_name = input("Full name [Network Administrator]: ").strip() or "Network Administrator"
        password = getpass.getpass(f"Password for {username}: ")
        try:
            TypeAdapter(Password).validate_python(password)
        except ValidationError as exc:
            sys.exit("Account not created: " + "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()))
        if getpass.getpass("Type it again: ") != password:
            sys.exit("Account not created: the two entries differ")
        db.add(User(hospital_id=None, role=role, username=username, password_hash=hash_password(password),
                    full_name=full_name))
        db.commit()
    print(f"Network administrator {username} created. Sign in with it to manage every hospital (Hospitals page).")


if __name__ == "__main__":
    main()
