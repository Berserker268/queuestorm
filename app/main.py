import os
import json
import re
import uuid
import time
import logging
import asyncio
from decimal import Decimal
from typing import Optional, List, Any
from datetime import datetime

from openai import OpenAI
from fastapi import FastAPI, Request, HTTPException, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, condecimal
from enum import Enum

# Basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("queuestorm")

app = FastAPI(title="QueueStorm Investigator")

# Config via env
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
QUEUESTORM_API_KEY = os.environ.get("QUEUESTORM_API_KEY", None)
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "12.0"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))

client = OpenAI(
    base_url=GROQ_BASE_URL,
    api_key=GROQ_API_KEY,
)


# --- ENUMS (Strict Case-Sensitive Matching) ---
class CaseType(str, Enum):
    wrong_transfer = "wrong_transfer"
    payment_failed = "payment_failed"
    refund_request = "refund_request"
    duplicate_payment = "duplicate_payment"
    merchant_settlement_delay = "merchant_settlement_delay"
    agent_cash_in_issue = "agent_cash_in_issue"
    phishing_or_social_engineering = "phishing_or_social_engineering"
    other = "other"


class Department(str, Enum):
    customer_support = "customer_support"
    dispute_resolution = "dispute_resolution"
    payments_ops = "payments_ops"
    merchant_operations = "merchant_operations"
    agent_operations = "agent_operations"
    fraud_risk = "fraud_risk"


class EvidenceVerdict(str, Enum):
    consistent = "consistent"
    inconsistent = "inconsistent"
    insufficient_data = "insufficient_data"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# --- INPUT SCHEMAS ---
class TransactionEntry(BaseModel):
    transaction_id: str
    timestamp: datetime
    type: str  # transfer, payment, cash_in, cash_out, settlement, refund
    amount: condecimal(gt=-1)  # non-negative decimal
    counterparty: str
    status: str  # completed, failed, pending, reversed

    @field_validator("transaction_id")
    @classmethod
    def txn_id_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("transaction_id must not be empty")
        return v


class TicketRequest(BaseModel):
    ticket_id: str
    complaint: str
    language: Optional[str] = "en"
    channel: Optional[str] = "in_app_chat"
    user_type: Optional[str] = "customer"
    campaign_context: Optional[str] = None
    transaction_history: List[TransactionEntry] = Field(default_factory=list)
    metadata: Optional[Any] = None

    @field_validator("complaint")
    @classmethod
    def complaint_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("complaint must not be empty")
        return v

    @field_validator("language")
    @classmethod
    def language_default(cls, v):
        return (v or "en").lower()


# --- OUTPUT SCHEMA ---
class AnalysisOutput(BaseModel):
    ticket_id: str
    relevant_transaction_id: Optional[str] = None
    evidence_verdict: EvidenceVerdict
    case_type: CaseType
    severity: Severity
    department: Department
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    reason_codes: Optional[List[str]] = Field(default_factory=list)


# Strict system prompt: short, few-shot examples will be inserted here if needed.
SYSTEM_PROMPT = """You are QueueStorm Investigator, an internal AI copilot for support agents at a digital finance platform.
CRITICAL: NEVER ask for PIN, OTP, password, CVV, or full card numbers. NEVER promise refunds, reversals, or unblocks. NEVER direct customers to third-party channels.
Return ONLY a single JSON object that exactly matches the required schema and types. No explanation, no markdown, no extra text.
"""


# ---------------------------
# Helper functions
# ---------------------------

def build_user_message(ticket: TicketRequest) -> str:
    txn_text = "No transaction history provided."
    if ticket.transaction_history:
        txn_lines = []
        for t in ticket.transaction_history:
            # Format amount with two decimals
            amt = f"{Decimal(t.amount):.2f}"
            ts = t.timestamp.isoformat()
            txn_lines.append(
                f"  - {t.transaction_id}: {t.type} of {amt} BDT "
                f"to/from {t.counterparty} at {ts} [{t.status}]"
            )
        txn_text = "Transaction history:\n" + "\n".join(txn_lines)

    return f"""Ticket ID: {ticket.ticket_id}
Language: {ticket.language or 'unknown'}
Channel: {ticket.channel or 'unknown'}
User type: {ticket.user_type or 'unknown'}
Campaign context: {ticket.campaign_context or 'none'}

Customer complaint:
{ticket.complaint}

{txn_text}

Analyze this ticket and return ONLY the JSON response matching the required schema. No other text."""


def strip_fences(text: str) -> str:
    # Remove leading/trailing ```json or ``` fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text, flags=re.I)
    return text.strip()


def extract_first_json(text: str) -> str:
    """
    Attempt to find the first balanced JSON object in text.
    Fallback: try simple regex of {...}
    """
    text = strip_fences(text)
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM output")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                return candidate
    # Last resort: regex greedy (may fail on nested braces)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        return m.group(0)
    raise ValueError("Could not extract JSON object from LLM output")


def safe_reply(language: str = "en") -> str:
    if language and language.lower().startswith("bn"):
        return "আপনার অভিযোগ গ্রহণ করা হয়েছে। তদন্তের পর যে কোন যোগ্য পরিমাণ অফিসিয়াল চ্যানেলের মাধ্যমে প্রক্রিয়াকৃত হবে। এখানে কোন সংবেদনশীল তথ্য শেয়ার করবেন না।"
    return "We have noted your concern. Any eligible amount will be returned through official channels after investigation. Please do not share sensitive credentials here."


FORBIDDEN_REPLY_RE = re.compile(
    r"\b(otp|pin|password|cvv|full card|card number|will refund|we have reversed|we have refunded|http[s]?://|\+?\d{6,})\b",
    flags=re.I,
)


def scan_and_sanitize_customer_reply(reply: str, language: str) -> (str, bool):
    """
    Return (safe_reply_text, was_replaced_bool)
    """
    if not reply:
        return safe_reply(language), True

    if FORBIDDEN_REPLY_RE.search(reply):
        return safe_reply(language), True

    # Also check absolute refund language
    if re.search(r"\b(will refund|guarantee|we have reversed|we will reverse)\b", reply, flags=re.I):
        return safe_reply(language), True

    return reply, False


async def call_llm(messages: List[dict], model: str = GROQ_MODEL) -> str:
    """
    Wrapper for LLM call. Uses asyncio.to_thread to avoid blocking the event loop.
    Retries on transient errors. Returns raw text output.
    """
    last_exc = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            # run the blocking client call in a thread
            coro = asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                max_tokens=1000,
                temperature=0.1,
                messages=messages,
            )
            response = await asyncio.wait_for(coro, timeout=LLM_TIMEOUT_SECONDS)
            # Support multiple SDK shapes
            text = None
            try:
                text = response.choices[0].message.content
            except Exception:
                try:
                    text = response.choices[0].text
                except Exception:
                    # fallback: convert to string
                    text = str(response)
            return text
        except asyncio.TimeoutError as e:
            last_exc = e
            logger.warning("LLM call timed out on attempt %d", attempt + 1)
        except Exception as e:
            last_exc = e
            logger.warning("LLM call transient error on attempt %d: %s", attempt + 1, str(e))
        # backoff
        await asyncio.sleep(1 + attempt * 1.5)
    # exhausted
    raise RuntimeError(f"LLM failed after retries: {last_exc}")


def coerce_and_validate_llm_output(raw_text: str) -> AnalysisOutput:
    """
    Extract JSON, parse, and validate against AnalysisOutput.
    On failure, raise ValidationError or ValueError.
    """
    json_text = extract_first_json(raw_text)
    parsed = json.loads(json_text)
    # Enforce keys exist; Pydantic will enforce types/enums
    try:
        # pydantic v2: model_validate; fall back to v1 parse_obj if missing
        if hasattr(AnalysisOutput, "model_validate"):
            validated = AnalysisOutput.model_validate(parsed)
        else:
            validated = AnalysisOutput.parse_obj(parsed)
    except ValidationError as e:
        raise
    return validated


# ---------------------------
# Endpoints
# ---------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze-ticket", response_model=AnalysisOutput)
async def analyze_ticket_endpoint(ticket: TicketRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    # Simple API key auth
    if QUEUESTORM_API_KEY:
        if not x_api_key or x_api_key != QUEUESTORM_API_KEY:
            logger.warning("Unauthorized request (missing/invalid x-api-key) from %s", request.client.host if request.client else "unknown")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    # Input limits
    if len(ticket.complaint) > 5000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Complaint too long")
    if len(ticket.transaction_history) > 200:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Too many transaction history items")

    request_id = str(uuid.uuid4())
    logger.info("analyze-ticket start request_id=%s ticket_id=%s", request_id, ticket.ticket_id)

    # Build LLM messages
    user_message = build_user_message(ticket)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        raw = await call_llm(messages=messages, model=GROQ_MODEL)
    except Exception as e:
        logger.error("LLM call failed request_id=%s error=%s", request_id, str(e))
        # Fallback deterministic response for human review
        fallback = AnalysisOutput(
            ticket_id=ticket.ticket_id,
            relevant_transaction_id=None,
            evidence_verdict=EvidenceVerdict.insufficient_data,
            case_type=CaseType.other,
            severity=Severity.medium,
            department=Department.customer_support,
            agent_summary="Model unavailable — please review.",
            recommended_next_action="Escalate to human reviewer; verify transaction history and contact customer via official channels.",
            customer_reply=safe_reply(ticket.language),
            human_review_required=True,
            confidence=0.0,
            reason_codes=["llm_unavailable"],
        )
        return JSONResponse(status_code=200, content=fallback.model_dump())

    # Post-process LLM output
    try:
        validated = coerce_and_validate_llm_output(raw)
    except Exception as e:
        logger.warning("LLM response validation failed request_id=%s error=%s raw=%s", request_id, str(e), raw[:1000])
        # return safe fallback and require human review
        fallback = AnalysisOutput(
            ticket_id=ticket.ticket_id,
            relevant_transaction_id=None,
            evidence_verdict=EvidenceVerdict.insufficient_data,
            case_type=CaseType.other,
            severity=Severity.medium,
            department=Department.customer_support,
            agent_summary="LLM output malformed or failed schema validation; human review required.",
            recommended_next_action="Do not act automatically. Assign to human reviewer.",
            customer_reply=safe_reply(ticket.language),
            human_review_required=True,
            confidence=0.0,
            reason_codes=["parse_error"],
        )
        return JSONResponse(status_code=200, content=fallback.model_dump())

    # Sanitize the customer_reply for forbidden content
    cust_reply, replaced = scan_and_sanitize_customer_reply(validated.customer_reply, ticket.language)
    if replaced:
        logger.info("Customer reply sanitized request_id=%s ticket_id=%s", request_id, ticket.ticket_id)
        validated.customer_reply = cust_reply
        validated.human_review_required = True
        validated.confidence = 0.0
        if validated.reason_codes is None:
            validated.reason_codes = []
        validated.reason_codes.append("sanitized_customer_reply")

    # Final deterministic enforcement for enums and ranges (defensive)
    # confidence => ensure in [0.0, 1.0], coerce if needed
    try:
        if validated.confidence is None:
            validated.confidence = 0.0
        # pydantic returns Decimal for condecimal; ensure float where appropriate
        validated.confidence = float(validated.confidence)
        if validated.confidence < 0.0 or validated.confidence > 1.0:
            validated.confidence = max(0.0, min(1.0, validated.confidence))
    except Exception:
        validated.confidence = 0.0

    # Ensure no extra fields are returned (model_dump will respect schema)
    out = validated.model_dump()
    logger.info("analyze-ticket complete request_id=%s ticket_id=%s verdict=%s confidence=%s",
                request_id, ticket.ticket_id, out.get("evidence_verdict"), out.get("confidence"))

    return JSONResponse(status_code=200, content=out)
