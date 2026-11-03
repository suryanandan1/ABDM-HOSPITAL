import logging
import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.hip import routes as hip_routes
from app.hiu import routes as hiu_routes
from app.routers import (assistant, auth, consultations, dashboard, doctors, documents, fhir, hospital, lab, patients,
                         prescriptions, staff)

app = FastAPI(title="ABDM Hospital Platform API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, hospital, staff, doctors, patients, dashboard, consultations, prescriptions, lab, fhir, documents,
               assistant):
    app.include_router(module.router, prefix="/api")
# HIP: staff API and the ABDM callback paths (served under the bridge URL, e.g. https://host/api/v3/hip/...)
app.include_router(hip_routes.staff, prefix="/api")
app.include_router(hip_routes.callbacks, prefix="/api")
# HIU: staff API, the data push URL handed to HIPs, and the ABDM HIU callbacks (/api/v3/hiu/...)
for router in (hiu_routes.staff, hiu_routes.push, hiu_routes.callbacks):
    app.include_router(router, prefix="/api")


class RedactAccessLog(logging.Filter):
    """Access logs never contain query strings (search terms can be patient names) or HIU push tokens."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            path = re.sub(r"\?.*$", "", str(record.args[2]))
            record.args = (*record.args[:2], re.sub(r"(/data-push/)[^/]+", r"\1***", path), *record.args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(RedactAccessLog())


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")  # patient data must not be cached
    return response


@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok"}
