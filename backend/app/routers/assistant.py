"""AI hospital assistant endpoints (read-only; scoped to what the current user may see)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import assistant
from app.ai import AIError
from app.database import get_db
from app.hiu import records as hiu_records
from app.models import Patient, User
from app.security import require
from app.services import audit, get_or_404

router = APIRouter(prefix="/assistant", tags=["assistant"])


def load(db: Session, user: User, patient_id: int, request: Request, action: str) -> tuple[Patient, list[dict]]:
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    items = assistant.corpus(db, user, patient)
    audit(db, user, action, "patient", patient.id, {"records": len(items)}, request)
    db.commit()
    return patient, items


def sources(items: list[dict]) -> dict:
    return {i["id"]: {"type": i["type"], "date": i["date"], "source": i["source"], "link": i["link"]} for i in items}


@router.get("/patients/{patient_id}/overview")
def overview(patient_id: int, request: Request, user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    """Doctor briefing, chronological timeline and lab trends; computed without AI."""
    patient, items = load(db, user, patient_id, request, "assistant.overview")
    return {"briefing": assistant.briefing(items, patient, user),
            "timeline": [{"id": i["id"], "type": i["type"], "date": i["date"], "source": i["source"], "author": i["author"],
                          "link": i["link"]} for i in items],
            "lab_trends": hiu_records.lab_trends(assistant.lab_observations(items)),
            "sources": sources(items)}


@router.post("/patients/{patient_id}/summary")
def summary(patient_id: int, request: Request, user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    _, items = load(db, user, patient_id, request, "assistant.summary")
    if not items:
        raise HTTPException(422, "There are no records you can access for this patient")
    try:
        result = assistant.summarize(items)
    except AIError as exc:
        raise HTTPException(503, f"AI unavailable: {exc}")
    return {**result, "sources": sources(items), "scope": assistant.visible_categories(user)}


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@router.post("/patients/{patient_id}/ask")
def ask(patient_id: int, body: Question, request: Request, user: User = Depends(require("clinical:read")),
        db: Session = Depends(get_db)):
    _, items = load(db, user, patient_id, request, "assistant.question")  # the question text itself is not logged
    if not items:
        return {"statements": [], "information_unavailable": True, "removed_unsupported": 0, "records_considered": 0,
                "unavailable_reason": "There are no records you can access for this patient", "sources": {}}
    try:
        result = assistant.ask(items, body.question)
    except AIError as exc:
        raise HTTPException(503, f"AI unavailable: {exc}")
    return {**result, "sources": sources(items), "scope": assistant.visible_categories(user)}
