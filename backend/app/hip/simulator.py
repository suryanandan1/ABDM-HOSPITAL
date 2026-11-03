"""In-process stand-in for the ABDM gateway, consent manager, the patient's ABHA app and an HIU.

Used only when ABDM_MODE=simulation. It receives the HIP's outbound calls exactly as the real gateway would
(same paths, headers and bodies), signs its callbacks with its own RSA key (verified by the same code path as
real gateway tokens) and decrypts data pushes with its own HIU keys. Its state is in memory and every
transaction it causes is labelled "simulation". It is not a model of ABDM's internal rules.
"""

import hashlib
import json
import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.hip import crypto, gateway

KID = "simulated-abdm-gateway"
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
registry: dict[str, dict] = {}  # simulated ABHA accounts: address -> {name, gender, yearOfBirth, abhaNumber}
outbox: list[dict] = []  # calls received from the HIP (newest last)
phone: dict[str, str] = {}  # last OTP "delivered" by SMS per mobile number
hiu: dict[str, dict] = {}  # transaction id -> {key material, decrypted summary}
_callbacks: list[tuple[str, dict, dict]] = []


def reset() -> None:
    from app.hiu import simulator as hiu_simulator
    registry.clear(), outbox.clear(), phone.clear(), hiu.clear(), _callbacks.clear()
    hiu_simulator.reset()


def issue_token(**claims) -> str:
    now = int(time.time())
    payload = {"iss": "simulated-abdm-gateway", "iat": now, "exp": now + 1200, **claims}
    return jwt.encode(payload, _key, algorithm="RS256", headers={"kid": KID})


def jwks() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": KID, "alg": "RS256", "use": "sig"}]}


def callback_headers() -> dict:
    return {"Authorization": f"Bearer {issue_token()}", "REQUEST-ID": str(uuid.uuid4()), "TIMESTAMP": gateway.timestamp(),
            "X-HIP-ID": gateway.hip_id(), "X-CM-ID": "sbx"}


def deliver_sms(mobile: str, otp: str) -> None:
    phone[mobile] = otp


def last_call(path: str) -> dict | None:
    return next((c for c in reversed(outbox) if c["path"] == path), None)


def receive(method: str, path: str, body: dict | None, headers: dict) -> tuple[int, dict | None]:
    """The gateway side of an outbound HIP call."""
    outbox.append({"path": path, "method": method, "body": body, "request_id": headers.get("REQUEST-ID")})
    del outbox[:-200]
    if path in (gateway.CONSENT_INIT_PATH, gateway.CONSENT_STATUS_PATH, gateway.CONSENT_FETCH_PATH, gateway.HI_REQUEST_PATH):
        from app.hiu import simulator as hiu_simulator  # the HIU side: consent manager + an external HIP
        return hiu_simulator.receive(path, body, headers)
    if path == gateway.GENERATE_LINK_TOKEN_PATH:
        account = registry.get((body.get("abhaAddress") or "").lower())
        reply = {"abhaAddress": body.get("abhaAddress"), "response": {"requestId": headers["REQUEST-ID"]}}
        if account is None:
            reply["error"] = {"code": "SIM-404", "message": "ABHA address not found (simulated ABHA registry)"}
        elif (account["gender"], account["yearOfBirth"]) != (body.get("gender"), body.get("yearOfBirth")) \
                or account["name"].lower() != (body.get("name") or "").lower():
            reply["error"] = {"code": "SIM-422", "message": "Demographic details do not match the ABHA account (simulated)"}
        else:
            reply["linkToken"] = issue_token(abhaAddress=body["abhaAddress"], abhaNumber=account.get("abhaNumber"))
        _callbacks.append(("on_generate_token", callback_headers(), reply))
    elif path == gateway.ADD_CARE_CONTEXTS_PATH:
        reply = {"abhaAddress": body.get("abhaAddress"), "response": {"requestId": headers["REQUEST-ID"]}}
        try:
            jwt.decode(headers.get("X-LINK-TOKEN", ""), _key.public_key(), algorithms=["RS256"])
            reply["status"] = "Successfully Linked care context (simulated)"
        except jwt.PyJWTError:
            reply["error"] = {"code": "SIM-401", "message": "Invalid or expired link token (simulated)"}
        _callbacks.append(("on_add_care_contexts", callback_headers(), reply))
    return 202, None


def receive_push(url: str, body: dict) -> int:
    """The simulated HIU receives a data push and decrypts it with its own key material."""
    session = hiu.get(body.get("transactionId"))
    if session is None:
        return 404
    material = body["keyMaterial"]
    for entry in body.get("entries", []):
        content = crypto.decrypt(entry["content"], session["private"], session["nonce"],
                                 crypto.unb64(material["dhPublicKey"]["keyValue"]), crypto.unb64(material["nonce"]))
        bundle = json.loads(content)
        session["received"].append({"careContextReference": entry["careContextReference"],
                                    "checksum_ok": hashlib.md5(content.encode()).hexdigest() == entry["checksum"],
                                    "resourceType": bundle["resourceType"],
                                    "profile": bundle["entry"][0]["resource"]["meta"]["profile"][0].rsplit("/", 1)[-1],
                                    "entries": len(bundle["entry"])})
    return 202


def flush() -> None:
    """Deliver queued gateway callbacks (after the HIP's own request has committed)."""
    from app.database import SessionLocal
    from app.hip import service
    from app.hiu import service as hiu_service, simulator as hiu_simulator
    while _callbacks:
        kind, headers, body = _callbacks.pop(0)
        gateway.verify_callback(headers["Authorization"])
        if kind == "hiu_push":  # the simulated external HIP pushes data to the HIU's push URL
            with SessionLocal() as db:
                hiu_simulator.push(db, body["transactionId"], body["hiRequest"])
        elif kind.startswith("hiu_"):
            hiu_service.handle_inbound(kind, headers, body)
        else:
            service.handle_inbound(kind, headers, body)


def send(kind: str, body: dict):
    """A signed ABDM callback into the HIP, through the same verification as real callbacks."""
    from app.hip import service
    headers = callback_headers()
    gateway.verify_callback(headers["Authorization"])
    tx = service.handle_inbound(kind, headers, body)
    flush()
    return tx, headers["REQUEST-ID"]


def reply_to(path: str, request_id: str) -> dict | None:
    """The HIP's answer (on-discover, on-init, ...) to a simulated request."""
    call = next((c for c in reversed(outbox) if c["path"] == path
                 and (c["body"] or {}).get("response", {}).get("requestId") == request_id), None)
    return call["body"] if call else None


# ---------- simulated patient / consent manager / HIU actions ----------

def discover(abha_address: str, name: str, gender: str, year_of_birth: int, mobile: str | None) -> dict:
    body = {"transactionId": str(uuid.uuid4()),
            "patient": {"id": abha_address, "name": name, "gender": gender, "yearOfBirth": year_of_birth,
                        "verifiedIdentifiers": [{"type": "MOBILE", "value": mobile}] if mobile else [],
                        "unverifiedIdentifiers": []}}
    _, request_id = send("discover", body)
    return {"request": body, "response": reply_to(gateway.ON_DISCOVER_PATH, request_id)}


def link_init(transaction_id: str, abha_address: str, discovered: list[dict]) -> dict:
    body = {"transactionId": transaction_id, "abhaAddress": abha_address, "patient": discovered}
    _, request_id = send("link_init", body)
    return reply_to(gateway.ON_INIT_LINK_PATH, request_id)


def link_confirm(link_reference: str, otp: str) -> dict:
    _, request_id = send("link_confirm", {"confirmation": {"linkRefNumber": link_reference, "token": otp}})
    return reply_to(gateway.ON_CONFIRM_LINK_PATH, request_id)


def grant_consent(abha_address: str, patient_reference: str, care_contexts: list[str], hi_types: list[str],
                  date_from: str, date_to: str, erase_at: str, status: str = "GRANTED", consent_id: str | None = None,
                  hip_id: str | None = None) -> dict:
    consent_id = consent_id or str(uuid.uuid4())
    detail = {"schemaVersion": "v3", "consentId": consent_id, "createdAt": gateway.timestamp(),
              "patient": {"id": abha_address}, "hip": {"id": hip_id or gateway.hip_id()}, "hiu": {"id": "SIM_HIU_001"},
              "careContexts": [{"patientReference": patient_reference, "careContextReference": r} for r in care_contexts],
              "purpose": {"text": "Care Management", "code": "CAREMGT"}, "hiTypes": hi_types,
              "consentManager": {"id": "sbx"},
              "permission": {"accessMode": "VIEW", "dateRange": {"from": date_from, "to": date_to},
                             "dataEraseAt": erase_at, "frequency": {"unit": "HOUR", "value": 1, "repeats": 0}}}
    _, request_id = send("consent_notify", {"notification": {"status": status, "consentId": consent_id,
                                                             "consentDetail": detail, "signature": "simulated"}})
    return {"consent_id": consent_id, "response": reply_to(gateway.CONSENT_ON_NOTIFY_PATH, request_id)}


def request_health_information(consent_id: str, date_from: str, date_to: str) -> dict:
    """The HIU asks for data: fresh key material, then the HIP pushes encrypted records to the HIU."""
    own = crypto.generate_key_material()
    transaction_id = str(uuid.uuid4())
    hiu[transaction_id] = {"private": own["private"], "nonce": own["nonce"], "received": []}
    body = {"transactionId": transaction_id, "hiRequest": {
        "consent": {"id": consent_id}, "dateRange": {"from": date_from, "to": date_to},
        "dataPushUrl": "sim://hiu/data-push",
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519",
                        "dhPublicKey": {"expiry": "2099-01-01T00:00:00.000Z", "parameters": crypto.PARAMETERS,
                                        "keyValue": crypto.b64(own["public"])},
                        "nonce": crypto.b64(own["nonce"])}}}
    tx, request_id = send("health_information", body)
    notify = last_call(gateway.HI_NOTIFY_PATH)
    return {"transaction_id": transaction_id, "on_request": reply_to(gateway.HI_ON_REQUEST_PATH, request_id),
            "received_by_hiu": hiu[transaction_id]["received"],
            "notification": notify["body"] if notify and notify["body"]["notification"]["transactionId"] == transaction_id else None}
