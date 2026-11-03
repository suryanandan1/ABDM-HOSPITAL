from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Encounter, LabOrder, LabReport, LabResult, LabTest, User, utcnow
from app.schemas import LabOrderCreate, LabOrderOut, LabReportIn, LabTestIn, LabTestOut, Page, ReasonRequest, SampleCollect
from app.security import require
from app.services import audit, current_doctor, get_or_404, mark_approved, paginate, require_status

router = APIRouter(prefix="/lab", tags=["laboratory"])

# Order: ordered -> sample_collected -> in_progress -> completed, or cancelled before results exist.
# Report: draft/rejected -> pending_approval -> approved (order completed) | rejected.


# ---------- catalogue ----------

@router.get("/tests", response_model=list[LabTestOut])
def list_tests(q: str | None = None, active_only: bool = False,
               user: User = Depends(require("lab:read")), db: Session = Depends(get_db)):
    query = select(LabTest).where(LabTest.hospital_id == user.hospital_id).order_by(LabTest.name)
    if q:
        query = query.where(or_(LabTest.name.ilike(f"%{q}%"), LabTest.code.ilike(f"%{q}%")))
    if active_only:
        query = query.where(LabTest.is_active.is_(True))
    return db.scalars(query).all()


def save_test(db: Session, user: User, test: LabTest, body: LabTestIn, request: Request, action: str) -> LabTest:
    if db.scalar(select(LabTest.id).where(LabTest.hospital_id == user.hospital_id, LabTest.code == body.code,
                                          LabTest.id != (test.id or 0))):
        raise HTTPException(409, "A test with this code already exists")
    for field, value in body.model_dump().items():
        setattr(test, field, value)
    db.add(test)
    db.flush()
    audit(db, user, action, "lab_test", test.id, {"code": test.code, "name": test.name}, request)
    db.commit()
    return test


@router.post("/tests", response_model=LabTestOut, status_code=201)
def create_test(body: LabTestIn, request: Request, user: User = Depends(require("lab:catalog")),
                db: Session = Depends(get_db)):
    return save_test(db, user, LabTest(hospital_id=user.hospital_id), body, request, "lab_test.create")


@router.put("/tests/{test_id}", response_model=LabTestOut)
def update_test(test_id: int, body: LabTestIn, request: Request, user: User = Depends(require("lab:catalog")),
                db: Session = Depends(get_db)):
    # Existing results keep their own copy of names, units and ranges, so editing is safe.
    return save_test(db, user, get_or_404(db, LabTest, test_id, user.hospital_id), body, request, "lab_test.update")


# ---------- orders and samples ----------

@router.get("/orders", response_model=Page[LabOrderOut])
def list_orders(status: str | None = None, patient_id: int | None = None, encounter_id: int | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                user: User = Depends(require("lab:read")), db: Session = Depends(get_db)):
    query = select(LabOrder).where(LabOrder.hospital_id == user.hospital_id).order_by(LabOrder.id.desc())
    if status:
        query = query.where(LabOrder.status.in_(status.split(",")))
    if patient_id:
        query = query.where(LabOrder.patient_id == patient_id)
    if encounter_id:
        query = query.where(LabOrder.encounter_id == encounter_id)
    return paginate(db, query, page, page_size)


@router.post("/orders", response_model=list[LabOrderOut], status_code=201)
def create_orders(body: LabOrderCreate, request: Request, user: User = Depends(require("lab:order")),
                  db: Session = Depends(get_db)):
    doctor = current_doctor(db, user)
    encounter = get_or_404(db, Encounter, body.encounter_id, user.hospital_id)
    if encounter.status == "cancelled":
        raise HTTPException(409, "Cannot order tests on a cancelled consultation")
    orders = []
    for test_id in dict.fromkeys(body.test_ids):
        test = get_or_404(db, LabTest, test_id, user.hospital_id)
        if not test.is_active:
            raise HTTPException(409, f"{test.name} is no longer offered")
        orders.append(LabOrder(hospital_id=user.hospital_id, patient_id=encounter.patient_id, encounter_id=encounter.id,
                               doctor_id=doctor.id, test_id=test.id, priority=body.priority,
                               clinical_notes=body.clinical_notes, created_by_id=user.id))
    db.add_all(orders)
    db.flush()
    for order in orders:
        audit(db, user, "lab_order.create", "lab_order", order.id,
              {"test": order.test.code, "encounter_id": encounter.id}, request)
    db.commit()
    return orders


@router.get("/orders/{order_id}", response_model=LabOrderOut)
def get_order(order_id: int, user: User = Depends(require("lab:read")), db: Session = Depends(get_db)):
    return get_or_404(db, LabOrder, order_id, user.hospital_id)


@router.post("/orders/{order_id}/cancel", response_model=LabOrderOut)
def cancel_order(order_id: int, body: ReasonRequest, request: Request, user: User = Depends(require("lab:order")),
                 db: Session = Depends(get_db)):
    order = get_or_404(db, LabOrder, order_id, user.hospital_id)
    require_status(order, "ordered", "sample_collected", action="cancel order")
    order.status, order.status_reason = "cancelled", body.reason
    audit(db, user, "lab_order.cancel", "lab_order", order.id, {"reason": body.reason}, request)
    db.commit()
    return order


@router.post("/orders/{order_id}/sample", response_model=LabOrderOut)
def collect_sample(order_id: int, body: SampleCollect, request: Request, user: User = Depends(require("lab:sample")),
                   db: Session = Depends(get_db)):
    order = get_or_404(db, LabOrder, order_id, user.hospital_id)
    require_status(order, "ordered", action="collect sample")
    order.status, order.sample_status = "sample_collected", "collected"
    order.sample_barcode = body.sample_barcode or f"S{order.id:07d}"
    order.sample_collected_at = body.collected_at or utcnow()
    order.sample_collected_by_id = user.id
    order.sample_notes = body.notes
    audit(db, user, "lab_sample.collect", "lab_order", order.id, {"barcode": order.sample_barcode}, request)
    db.commit()
    return order


@router.post("/orders/{order_id}/sample/reject", response_model=LabOrderOut)
def reject_sample(order_id: int, body: ReasonRequest, request: Request, user: User = Depends(require("lab:sample")),
                  db: Session = Depends(get_db)):
    """Unusable sample (haemolysed, insufficient...): the order goes back to waiting for a new sample."""
    order = get_or_404(db, LabOrder, order_id, user.hospital_id)
    require_status(order, "sample_collected", action="reject sample")
    order.status, order.sample_status, order.sample_notes = "ordered", "rejected", body.reason
    audit(db, user, "lab_sample.reject", "lab_order", order.id, {"reason": body.reason}, request)
    db.commit()
    return order


# ---------- results and report ----------

def interpret(parameter: dict, numeric: float | None, manual: str | None) -> str | None:
    """Flag numeric values against the reference range; otherwise keep the technician's flag."""
    low, high = parameter.get("ref_low"), parameter.get("ref_high")
    if numeric is None or (low is None and high is None):
        return manual
    if low is not None and numeric < low:
        return "L"
    if high is not None and numeric > high:
        return "H"
    return "N"


def to_number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def report_for(db: Session, user: User, order_id: int) -> tuple[LabOrder, LabReport]:
    order = get_or_404(db, LabOrder, order_id, user.hospital_id)
    if order.report is None:
        raise HTTPException(404, "No results have been entered for this order")
    return order, order.report


@router.put("/orders/{order_id}/report", response_model=LabOrderOut)
def save_results(order_id: int, body: LabReportIn, request: Request, user: User = Depends(require("lab:result")),
                 db: Session = Depends(get_db)):
    order = get_or_404(db, LabOrder, order_id, user.hospital_id)
    require_status(order, "sample_collected", "in_progress", action="enter results")
    report = order.report or LabReport(hospital_id=user.hospital_id, order=order, created_by_id=user.id, status="draft")
    require_status(report, "draft", "rejected", action="edit results")

    parameters = {p["code"]: p for p in order.test.parameters}
    unknown = [r.code for r in body.results if r.code not in parameters]
    if unknown:
        raise HTTPException(422, f"Unknown parameters for {order.test.name}: {', '.join(unknown)}")
    results = []
    for entry in body.results:
        if entry.value is None:
            continue
        p = parameters[entry.code]
        numeric = to_number(entry.value)
        results.append(LabResult(code=p["code"], name=p["name"], loinc_code=p.get("loinc_code"), value=entry.value,
                                 value_numeric=numeric, unit=p.get("unit"), ref_low=p.get("ref_low"),
                                 ref_high=p.get("ref_high"), ref_text=p.get("ref_text"),
                                 interpretation=interpret(p, numeric, entry.interpretation)))
    report.results = results
    report.conclusion = body.conclusion
    report.status = "draft"
    order.status = "in_progress"
    db.add(report)
    db.flush()
    audit(db, user, "lab_report.save", "lab_order", order.id,
          {"entered": len(results), "abnormal": [r.code for r in results if r.interpretation not in (None, "N")]},
          request)
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/report/submit", response_model=LabOrderOut)
def submit_report(order_id: int, request: Request, user: User = Depends(require("lab:result")),
                  db: Session = Depends(get_db)):
    order, report = report_for(db, user, order_id)
    require_status(report, "draft", "rejected", action="submit report")
    entered = {r.code for r in report.results}
    missing = [p["name"] for p in order.test.parameters if p["code"] not in entered]
    if missing:
        raise HTTPException(422, f"Enter all results before submitting. Missing: {', '.join(missing)}")
    if not report.conclusion:  # DiagnosticReport.conclusion is mandatory in the ABDM profile
        raise HTTPException(422, "Enter a conclusion before submitting the report")
    report.status, report.status_reason = "pending_approval", None
    audit(db, user, "lab_report.submit", "lab_order", order.id, request=request)
    db.commit()
    return order


@router.post("/orders/{order_id}/report/approve", response_model=LabOrderOut)
def approve_report(order_id: int, request: Request, user: User = Depends(require("lab:approve")),
                   db: Session = Depends(get_db)):
    order, report = report_for(db, user, order_id)
    require_status(report, "pending_approval", action="approve report")
    if report.created_by_id == user.id:
        raise HTTPException(403, "Results must be approved by someone other than the person who entered them")
    doctor = current_doctor(db, user)
    mark_approved(report, user)
    report.approved_by_doctor_id = doctor.id
    order.status = "completed"
    audit(db, user, "lab_report.approve", "lab_order", order.id, request=request)
    db.commit()
    return order


@router.post("/orders/{order_id}/report/reject", response_model=LabOrderOut)
def reject_report(order_id: int, body: ReasonRequest, request: Request, user: User = Depends(require("lab:approve")),
                  db: Session = Depends(get_db)):
    order, report = report_for(db, user, order_id)
    require_status(report, "pending_approval", action="reject report")
    current_doctor(db, user)
    report.status, report.status_reason = "rejected", body.reason
    audit(db, user, "lab_report.reject", "lab_order", order.id, {"reason": body.reason}, request)
    db.commit()
    return order
