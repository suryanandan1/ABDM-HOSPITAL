"""Re-encrypt every sealed value with the active RECORD_SEALING_KEY.

Use it once after first setting RECORD_SEALING_KEY (moves data off the legacy JWT_SECRET-derived key), and after
each rotation: put the new key in RECORD_SEALING_KEY, the previous one in RECORD_SEALING_OLD_KEYS, run this script,
then remove the old key. Each row is checked to decrypt to the same text before it is saved; the whole run is one
transaction, so a failure changes nothing.

Usage (from backend/, with the venv active):
    python scripts/reseal_records.py --dry-run   # report what would change
    python scripts/reseal_records.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.hiu import records  # noqa: E402
from app.models import HiuDataRequest, HiuPatientSummary, RetrievedRecord, User  # noqa: E402

SEALED_COLUMNS = [(RetrievedRecord, "content"), (HiuPatientSummary, "content"), (HiuDataRequest, "key_material"),
                  (User, "mfa_secret")]  # two-step sign-in secrets


def reseal(db, dry_run: bool = False) -> dict[str, int]:
    """Re-seal every value not yet under the active key; returns the number changed per table."""
    if not settings.record_sealing_key:
        raise SystemExit("Set RECORD_SEALING_KEY in backend/.env first")
    changed: dict[str, int] = {}
    for model, column in SEALED_COLUMNS:
        count = 0
        for row in db.scalars(select(model).where(getattr(model, column).is_not(None))):
            sealed = getattr(row, column)
            if not records.needs_reseal(sealed):
                continue
            text = records.unseal(sealed)
            resealed = records.seal(text)
            assert records.unseal(resealed) == text
            if not dry_run:
                setattr(row, column, resealed)
            count += 1
        changed[model.__tablename__] = count
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return changed


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    with SessionLocal() as db:
        try:
            changed = reseal(db, dry_run)
        except records.SealingKeyMissing as error:
            raise SystemExit(f"{error}. Add that key to RECORD_SEALING_OLD_KEYS and run again; nothing was changed.")
    verb = "Would re-seal" if dry_run else "Re-sealed"
    for table, count in changed.items():
        print(f"{verb} {count} value(s) in {table}")
    print(f"Active key id: {records.active_key_id()}")


if __name__ == "__main__":
    main()
