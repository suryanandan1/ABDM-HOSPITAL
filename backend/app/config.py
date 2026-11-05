from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = "sqlite:///./hospital.db"
    jwt_secret: str
    jwt_expire_minutes: int = 480  # longest a sign-in lasts (one shift), however active it is
    session_idle_minutes: int = 30  # a session unused this long ends; the browser also signs out after this
    # The browser keeps its session in an HttpOnly cookie (scripts cannot read it). Secure = https only; browsers
    # also accept Secure cookies on http://localhost, so keep it on unless testing from another machine over http.
    cookie_secure: bool = True
    password_scrypt_n: int = 2**15  # scrypt cost for new password hashes; older, cheaper hashes upgrade at sign-in
    # Roles that must use two-step sign-in (TOTP authenticator app), e.g. "hospital_admin,doctor"; anyone can opt in.
    mfa_required_roles: str = ""
    mfa_issuer: str = "ABDM Hospital"  # name shown in the authenticator app
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Only used by init_db.py when seeding the first hospital.
    hospital_name: str = "City General Hospital"
    hospital_code: str = "CGH"
    admin_username: str = "admin"
    admin_password: str | None = None

    # Phase 3: AI (Mistral AI) for extraction, suggestions and summaries; OCR model reads PDFs and images.
    mistral_api_key: str | None = None
    ai_model: str = "mistral-large-latest"
    ai_ocr_model: str = "mistral-ocr-latest"
    upload_dir: Path = BACKEND_DIR / "uploads"
    max_upload_mb: int = 20
    # Existing LOINC mapping project (map.py + data/Loinc.csv), imported as a library.
    loinc_mapping_dir: Path = BACKEND_DIR.parent.parent / "mapping"
    # Official HL7 FHIR validator; scripts/setup_validator.ps1 downloads both into backend/tools.
    fhir_validator_jar: Path = BACKEND_DIR / "tools" / "validator_cli.jar"
    java_path: str | None = None  # default: tools/jdk-*/bin/java.exe, else `java` on PATH
    fhir_tx_server: str = "https://tx.fhir.org"  # terminology server; "n/a" validates offline (no code checks)

    # Phase 4: ABDM HIP. "simulation" uses an in-process stand-in for ABDM; "sandbox" calls the ABDM sandbox.
    abdm_mode: Literal["simulation", "sandbox"] = "simulation"
    abdm_gateway_url: str = "https://dev.abdm.gov.in/api/hiecm"
    abdm_cm_id: str = "sbx"  # X-CM-ID: "sbx" for sandbox, "abdm" for production
    abdm_client_id: str | None = None  # bridge client id issued by NHA (e.g. SBX_00XXXX)
    abdm_client_secret: str | None = None  # never logged or returned by the API
    abdm_hip_id: str | None = None  # facility ID registered as HIP service (e.g. IN02100000XX)
    abdm_bridge_url: str | None = None  # public https base URL where ABDM delivers callbacks
    abdm_verify_callbacks: bool = True  # verify the gateway JWT on every callback (keep on)
    abdm_otp_test_display: bool = False  # sandbox only: show linking OTPs to staff when no SMS gateway exists
    abdm_timeout_seconds: float = 20

    # Phase 5: ABDM HIU (uses the same gateway credentials and bridge URL as the HIP).
    abdm_hiu_id: str | None = None  # facility ID registered as this bridge's HIU service
    hiu_auto_validate: bool = True  # run the HL7 validator on received bundles in the background

    # Encryption at rest for records received from other providers (and HIU key material). Kept apart from
    # JWT_SECRET so either can be rotated on its own. Without it, the legacy key derived from JWT_SECRET is used.
    record_sealing_key: str | None = None  # active key: >= 32 random characters
    record_sealing_old_keys: str = ""  # comma-separated earlier keys, still accepted for reading after a rotation

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if value.startswith("change-me"):
            raise ValueError("JWT_SECRET still has the example value from .env.example; generate a new one")
        return value

    @field_validator("record_sealing_key")
    @classmethod
    def strong_sealing_key(cls, value: str | None) -> str | None:
        if value is not None and value.strip() == "":
            return None
        if value is not None and len(value) < 32:
            raise ValueError("RECORD_SEALING_KEY must be at least 32 characters")
        return value

    @field_validator("password_scrypt_n")
    @classmethod
    def scrypt_cost(cls, value: int) -> int:
        if value < 2**14 or value & (value - 1):
            raise ValueError("PASSWORD_SCRYPT_N must be a power of two, at least 16384")
        return value

    @model_validator(mode="after")
    def sandbox_needs_sealing_key(self):
        if self.abdm_mode == "sandbox" and not self.record_sealing_key:
            raise ValueError("ABDM_MODE=sandbox needs RECORD_SEALING_KEY (records from other providers must not depend "
                             "on JWT_SECRET); set it, then run python scripts/reseal_records.py")
        return self

    @property
    def mfa_required_role_list(self) -> set[str]:
        return {r.strip() for r in self.mfa_required_roles.split(",") if r.strip()}

    @property
    def record_sealing_old_key_list(self) -> list[str]:
        return [k.strip() for k in self.record_sealing_old_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
