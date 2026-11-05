"""Encryption at rest of retrieved records: separate sealing key, key ids, rotation and re-sealing."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.hiu import records
from app.models import HiuPatientSummary, Patient
from scripts.reseal_records import reseal

KEY_A = "sealing-key-A-long-enough-for-tests-0123456789"
KEY_B = "sealing-key-B-long-enough-for-tests-9876543210"


@pytest.fixture()
def sealing(monkeypatch):
    def configure(active=None, old=""):
        monkeypatch.setattr(settings, "record_sealing_key", active)
        monkeypatch.setattr(settings, "record_sealing_old_keys", old)
    configure()
    return configure


def test_legacy_mode_unchanged(sealing):
    sealed = records.seal("bundle")
    assert not sealed.startswith("v2.")
    assert records.unseal(sealed) == "bundle"
    assert not records.needs_reseal(sealed)  # nothing to move to without a sealing key


def test_sealing_key_is_independent_of_jwt_secret(sealing, monkeypatch):
    legacy = records.seal("old record")
    sealing(KEY_A)
    sealed = records.seal("new record")
    assert sealed.startswith(f"v2.{records.active_key_id()}.")
    assert records.unseal(legacy) == "old record"  # data sealed before the key was set stays readable
    assert records.needs_reseal(legacy) and not records.needs_reseal(sealed)
    monkeypatch.setattr(settings, "jwt_secret", "a-rotated-jwt-secret-that-is-long-enough-xyz")
    assert records.unseal(sealed) == "new record"  # rotating JWT_SECRET no longer affects new data


def test_rotation_needs_the_old_key(sealing):
    sealing(KEY_A)
    sealed = records.seal("record")
    sealing(KEY_B)
    with pytest.raises(records.SealingKeyMissing):
        records.unseal(sealed)
    sealing(KEY_B, old=KEY_A)
    assert records.unseal(sealed) == "record"
    assert records.needs_reseal(sealed)


def test_reseal_moves_rows_to_active_key(client, sealing):
    with SessionLocal() as db:
        patient = db.scalars(select(Patient)).first()
        db.add(HiuPatientSummary(patient_id=patient.id, content=records.seal('{"summary": 1}'), record_ids=[],
                                 model="test"))
        db.commit()
        sealing(KEY_A)
        assert reseal(db, dry_run=True)["hiu_patient_summaries"] == 1
        assert not db.scalars(select(HiuPatientSummary)).one().content.startswith("v2.")  # dry run changed nothing
        assert reseal(db)["hiu_patient_summaries"] == 1
        sealing(KEY_B, old=KEY_A)  # rotate
        assert reseal(db)["hiu_patient_summaries"] == 1
        assert reseal(db)["hiu_patient_summaries"] == 0  # already on the active key
        sealing(KEY_B)  # the old key can now be dropped
        row = db.scalars(select(HiuPatientSummary)).one()
        assert row.content.startswith(f"v2.{records.active_key_id()}.")
        assert records.unseal(row.content) == '{"summary": 1}'


def test_short_sealing_key_refused():
    from pydantic import ValidationError
    from app.config import Settings
    with pytest.raises(ValidationError):
        Settings(jwt_secret="x" * 40, record_sealing_key="too-short")
