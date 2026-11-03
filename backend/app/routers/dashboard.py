from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Doctor, Encounter, LabOrder, LabReport, Patient, User
from app.schemas import AuditLogOut, DashboardOut, Page
from app.security import permissions_for, require
from app.services import paginate

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(user: User = Depends(require("dashboard:view")), db: Session = Depends(get_db)):
    hid = user.hospital_id

    def count(model, *conditions) -> int:
        return db.scalar(select(func.count()).select_from(model).where(model.hospital_id == hid, *conditions))

    start_of_day = datetime.combine(datetime.now(timezone.utc).date(), time.min)
    permissions = permissions_for(user)
    can_see_patients = "patient:read" in permissions
    return {
        "stats": {
            "total_patients": count(Patient),
            "patients_today": count(Patient, Patient.created_at >= start_of_day),
            "abha_verified_patients": count(Patient, Patient.abha_verified.is_(True)),
            "active_doctors": count(Doctor, Doctor.is_active.is_(True)),
            "active_staff": count(User, User.is_active.is_(True)),
            "consultations_today": count(Encounter, Encounter.visit_at >= start_of_day,
                                         Encounter.status != "cancelled"),
            "lab_orders_open": count(LabOrder, LabOrder.status.in_(("ordered", "sample_collected", "in_progress"))),
            "lab_reports_awaiting_approval": count(LabReport, LabReport.status == "pending_approval"),
        },
        "recent_patients": db.scalars(select(Patient).where(Patient.hospital_id == hid)
                                      .order_by(Patient.created_at.desc()).limit(5)).all()
                           if can_see_patients else [],
        "recent_activity": db.scalars(select(AuditLog).where(AuditLog.hospital_id == hid)
                                      .order_by(AuditLog.id.desc()).limit(10)).all()
                           if "audit:read" in permissions else [],
    }


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def list_audit_logs(action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
                    user_id: int | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                    user: User = Depends(require("audit:read")), db: Session = Depends(get_db)):
    query = select(AuditLog).where(AuditLog.hospital_id == user.hospital_id).order_by(AuditLog.id.desc())
    if action:
        query = query.where(AuditLog.action.startswith(action))
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    return paginate(db, query, page, page_size)
