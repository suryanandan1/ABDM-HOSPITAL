"""ABDM gateway client: session token, request headers, outbound calls and callback authentication.

Paths, headers and bodies follow NHA's ABDM sandbox documentation (Milestone 3 v2.5) and NHA's ABDM
wrapper (application-v3.properties). In simulation mode the identical calls go to app.hip.simulator.
"""

import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
import jwt

from app.config import settings

SESSIONS_PATH = "/gateway/v3/sessions"
CERTS_PATH = "/gateway/v3/certs"
BRIDGE_URL_PATH = "/gateway/v3/bridge/url"
GENERATE_LINK_TOKEN_PATH = "/v3/token/generate-token"
ADD_CARE_CONTEXTS_PATH = "/hip/v3/link/carecontext"
ON_DISCOVER_PATH = "/user-initiated-linking/v3/patient/care-context/on-discover"
ON_INIT_LINK_PATH = "/user-initiated-linking/v3/link/care-context/on-init"
ON_CONFIRM_LINK_PATH = "/user-initiated-linking/v3/link/care-context/on-confirm"
CONSENT_ON_NOTIFY_PATH = "/consent/v3/request/hip/on-notify"
HI_ON_REQUEST_PATH = "/data-flow/v3/health-information/hip/on-request"
HI_NOTIFY_PATH = "/data-flow/v3/health-information/notify"
# HIU (NHA sandbox documentation, Milestone 3 v2.5)
CONSENT_INIT_PATH = "/consent/v3/request/init"
CONSENT_STATUS_PATH = "/consent/v3/request/status"
CONSENT_HIU_ON_NOTIFY_PATH = "/consent/v3/request/hiu/on-notify"
CONSENT_FETCH_PATH = "/consent/v3/fetch"
HI_REQUEST_PATH = "/data-flow/v3/health-information/request"

_lock = threading.Lock()
_session = {"token": None, "expires": 0.0}
_jwks = {"keys": None, "fetched": 0.0}


class GatewayError(Exception):
    """An ABDM call failed; `message` is safe to show to staff."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message, self.status = message, status


class CallbackAuthError(Exception):
    pass


def simulation() -> bool:
    return settings.abdm_mode == "simulation"


def hip_id() -> str | None:
    return settings.abdm_hip_id or ("SIM_HIP_001" if simulation() else None)


def hiu_id() -> str | None:
    return settings.abdm_hiu_id or ("SIM_HIU_LOCAL" if simulation() else None)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def http_client() -> httpx.Client:
    """Separate so tests can substitute a mock transport."""
    return httpx.Client(timeout=settings.abdm_timeout_seconds)


def base_headers(request_id: str | None = None) -> dict:
    return {"REQUEST-ID": request_id or str(uuid.uuid4()), "TIMESTAMP": timestamp(), "X-CM-ID": settings.abdm_cm_id,
            "Content-Type": "application/json"}


def reset() -> None:
    _session.update(token=None, expires=0.0)
    _jwks.update(keys=None, fetched=0.0)


def session_token() -> str:
    if simulation():
        from app.hip import simulator
        return simulator.issue_token()
    with _lock:
        if _session["token"] and _session["expires"] > time.time():
            return _session["token"]
        if not (settings.abdm_client_id and settings.abdm_client_secret):
            raise GatewayError("ABDM_CLIENT_ID and ABDM_CLIENT_SECRET are not configured")
        body = {"clientId": settings.abdm_client_id, "clientSecret": settings.abdm_client_secret,
                "grantType": "client_credentials"}
        try:
            with http_client() as client:
                r = client.post(settings.abdm_gateway_url + SESSIONS_PATH, json=body, headers=base_headers())
        except httpx.HTTPError as exc:
            raise GatewayError(f"ABDM gateway is unreachable ({type(exc).__name__})") from None
        if r.status_code not in (200, 202) or "accessToken" not in (data := r.json() or {}):
            raise GatewayError(f"ABDM rejected the client credentials (HTTP {r.status_code})", r.status_code)
        _session.update(token=data["accessToken"], expires=time.time() + max(int(data.get("expiresIn", 1200)) - 60, 30))
        return _session["token"]


def call(path: str, body: dict | None, headers: dict | None = None, method: str = "POST") -> tuple[int, dict | None, str]:
    """Call a gateway API. Returns (HTTP status, JSON body or None, REQUEST-ID used)."""
    request_headers = {**base_headers(), **(headers or {})}
    for attempt in (1, 2):
        request_headers["Authorization"] = f"Bearer {session_token()}"
        if simulation():
            from app.hip import simulator
            status, data = simulator.receive(method, path, body, request_headers)
            return status, data, request_headers["REQUEST-ID"]
        try:
            with http_client() as client:
                r = client.request(method, settings.abdm_gateway_url + path, json=body, headers=request_headers)
        except httpx.HTTPError as exc:
            raise GatewayError(f"ABDM gateway is unreachable ({type(exc).__name__})") from None
        if r.status_code == 401 and attempt == 1:
            _session.update(token=None)  # token expired early: renew once
            continue
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = None
        return r.status_code, data, request_headers["REQUEST-ID"]
    return r.status_code, None, request_headers["REQUEST-ID"]


def push(url: str, body: dict) -> int:
    """Data push to the HIU's dataPushUrl (sent directly to the HIU, as in NHA's wrapper)."""
    if simulation():
        from app.hip import simulator
        return simulator.receive_push(url, body)
    try:
        with http_client() as client:
            return client.post(url, json=body, headers={"Content-Type": "application/json"}).status_code
    except httpx.HTTPError:
        return 0


def jwks(refresh: bool = False) -> dict:
    if simulation():
        from app.hip import simulator
        return simulator.jwks()
    with _lock:
        if _jwks["keys"] and not refresh and time.time() - _jwks["fetched"] < 3600:
            return _jwks["keys"]
        try:
            with http_client() as client:
                r = client.get(settings.abdm_gateway_url + CERTS_PATH, headers=base_headers())
        except httpx.HTTPError as exc:
            raise CallbackAuthError(f"Cannot fetch ABDM signing keys ({type(exc).__name__})") from None
        if r.status_code not in (200, 202):
            raise CallbackAuthError(f"Cannot fetch ABDM signing keys (HTTP {r.status_code})")
        _jwks.update(keys=r.json(), fetched=time.time())
        return _jwks["keys"]


def verify_callback(authorization: str | None) -> dict:
    """Check the gateway JWT on a callback against ABDM's published keys (/gateway/v3/certs)."""
    if not settings.abdm_verify_callbacks:
        return {}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise CallbackAuthError("Missing gateway token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        keys = jwks()
        match = next((k for k in keys.get("keys", []) if k.get("kid") == kid), None)
        if match is None:  # keys may have rotated
            match = next((k for k in jwks(refresh=True).get("keys", []) if k.get("kid") == kid), None)
        if match is None:
            raise CallbackAuthError("Gateway token signed with an unknown key")
        return jwt.decode(token, jwt.PyJWK(match).key, algorithms=["RS256", "RS512"], options={"verify_aud": False})
    except jwt.PyJWTError as exc:
        raise CallbackAuthError(f"Invalid gateway token: {exc}") from None
