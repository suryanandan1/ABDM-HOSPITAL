"""AI assistance for the HIU: suggest a request and summarise retrieved records.

AI never creates, approves or submits a consent request, never decides what may be decrypted, and only
summarises facts copied from received records; every statement must cite those records (checked below).
"""

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app import ai

PURPOSES = {"CAREMGT": "Care Management", "BTG": "Break the Glass", "PUBHLTH": "Public Health",
            "HPAYMT": "Healthcare Payment", "DSRCH": "Disease Specific Healthcare Research", "PATRQT": "Self Requested"}
HI_TYPES = ["OPConsultation", "Prescription", "DiagnosticReport", "DischargeSummary", "ImmunizationRecord",
            "HealthDocumentRecord", "WellnessRecord"]
Purpose = Literal["CAREMGT", "BTG", "PUBHLTH", "HPAYMT", "DSRCH", "PATRQT"]
HiType = Literal["OPConsultation", "Prescription", "DiagnosticReport", "DischargeSummary", "ImmunizationRecord",
                 "HealthDocumentRecord", "WellnessRecord"]


class RequestSuggestion(BaseModel):
    purpose_code: Purpose
    hi_types: list[HiType]
    date_from: str | None = Field(description="YYYY-MM-DD, or null when no range is needed")
    date_to: str | None = Field(description="YYYY-MM-DD, or null for today")
    missing_information: list[str] = Field(description="Information staff should add or confirm before submitting")
    explanation: str = Field(description="Two or three sentences on why these records may help, based only on the context")


class SummaryItem(BaseModel):
    text: str
    source_record_ids: list[int] = Field(description="Ids of the records this statement is taken from")


class RecordsSummary(BaseModel):
    overview: list[SummaryItem]
    consultations: list[SummaryItem]
    prescriptions: list[SummaryItem]
    laboratory: list[SummaryItem]


SUGGEST_PROMPT = """You help hospital staff prepare a request for a patient's records from other providers under India's
ABDM consent framework. Suggest the purpose of use, the record categories (hiTypes) and a date range that are
proportionate to the stated clinical reason; ask for less rather than more. Base the explanation only on the context
given. List missing information (e.g. an unclear reason). You do not create, submit or approve anything: staff review
every suggestion and the patient decides on consent."""

SUMMARY_PROMPT = """You summarise health records a hospital received from other providers, for the treating doctor.
Use only the facts in the records provided. Never add diagnoses, interpretations, causes or recommendations that are not
written in the records. Every item must list the ids of the records it comes from in source_record_ids. Keep items short.
Write "overview" (key documented conditions and events), "consultations", "prescriptions" (medicines over time) and
"laboratory" (repeated tests and documented values, quoting units). Leave a list empty when there is nothing to say."""


def call_model(system: str, payload: dict, output_format):
    """The model call; replaced by a fake in tests."""
    return ai.structured(system, [{"type": "text", "text": json.dumps(payload, default=str)}], output_format, max_tokens=8000)


def suggest_request(context: dict) -> dict:
    suggestion = call_model(SUGGEST_PROMPT, context, RequestSuggestion)
    result = suggestion.model_dump()
    for key in ("date_from", "date_to"):  # drop unusable dates rather than guessing
        try:
            if result[key] and date.fromisoformat(result[key]) > date.today():
                result[key] = None
        except ValueError:
            result[key] = None
    result["hi_types"] = sorted(set(result["hi_types"]))
    return result


def summarize(records: list[dict]) -> dict:
    """records: [{"record_id", ...facts}]. Items citing no or unknown records are removed."""
    known = {r["record_id"] for r in records}
    summary = call_model(SUMMARY_PROMPT, {"records": records}, RecordsSummary).model_dump()
    removed = 0
    for section, items in summary.items():
        kept = [i for i in items if i["source_record_ids"] and set(i["source_record_ids"]) <= known]
        removed += len(items) - len(kept)
        summary[section] = kept
    summary["removed_unsupported_items"] = removed
    return summary
