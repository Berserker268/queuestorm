# QueueStorm Investigator

**AI/API SupportOps Copilot for Digital Finance**
SUST CSE Carnival 2026 — Codex Community Hackathon (bKash presents)

---

## Overview

QueueStorm Investigator is an internal AI copilot for support agents at a digital finance platform. It ingests a customer complaint alongside the customer's recent transaction history, cross-references both, and returns a single structured JSON response that classifies, routes, and drafts a safe agent reply — all within a 30-second window.

The key distinction from a plain classifier: the service acts as an **investigator**. The complaint says one thing; the transaction data may say another. The service decides what the evidence actually shows.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.11 |
| Framework | FastAPI (async) |
| LLM Backend | Groq Cloud (`llama-3.3-70b-versatile`) |
| LLM Client | `openai` SDK (Groq-compatible endpoint) |
| Validation | Pydantic v2 |
| Server | Uvicorn |
| Containerisation | Docker |

---

## Models Used

| Model | Provider | Where it runs | Why |
|---|---|---|---|
| `llama-3.3-70b-versatile` | Groq Cloud | Remote API (Groq inference) | Fast inference (<5 s typical), free tier available, strong instruction following for structured JSON output, sufficient context window for complaint + transaction history |

No GPU is required. All inference is delegated to the Groq API over HTTPS.

---

## AI Approach

The LLM receives a strict system prompt instructing it to return **only** a single JSON object matching the `AnalysisOutput` schema. The user message is a structured prompt that includes:

- Ticket metadata (ID, language, channel, user type, campaign context)
- The verbatim customer complaint
- A formatted list of all provided transactions (ID, type, amount, counterparty, timestamp, status)

The model is asked to cross-reference the complaint against the transaction history and populate every required field, including `relevant_transaction_id` and `evidence_verdict`. Temperature is set to `0.1` for deterministic, schema-consistent outputs.

Post-LLM, the service performs deterministic validation and sanitisation in Python before returning any response — the LLM output is never trusted unconditionally.

---

## Safety Logic

Safety is enforced at two layers:

**1. System-prompt rules (LLM layer)**
The system prompt contains an explicit `CRITICAL` block that prohibits the model from asking for PIN, OTP, password, or CVV; confirming refunds or reversals; or directing customers to third-party channels.

**2. Regex sanitisation (application layer)**
`scan_and_sanitize_customer_reply()` applies a compiled regex to every `customer_reply` field before it leaves the service. If any forbidden pattern is detected (OTP, PIN, password, CVV, phone numbers, explicit refund confirmations, external URLs), the reply is **replaced wholesale** with a safe fallback string, `human_review_required` is forced to `true`, `confidence` is zeroed, and `sanitized_customer_reply` is appended to `reason_codes`.

This dual-layer approach means a prompt-injection attack inside the complaint text that tricks the LLM into producing a dangerous reply will still be caught and neutralised before the response reaches the agent.

**Prompt injection resistance:** The system prompt is kept separate from user-controlled input. The complaint and transaction history are clearly delimited in the user message, reducing the surface for injection. Any LLM output that violates safety rules is overwritten by the application layer regardless of how it was produced.

---

## Setup & Run

### Prerequisites

- Python 3.11+
- A [Groq](https://console.groq.com) API key (free tier is sufficient)

### Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes | — | Your Groq Cloud API key |
| `GROQ_BASE_URL` | No | `https://api.groq.com/openai/v1` | Groq-compatible base URL |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Model identifier |
| `QUEUESTORM_API_KEY` | No | — | If set, all requests must include `X-Api-Key` header |
| `LLM_TIMEOUT_SECONDS` | No | `12.0` | Per-attempt timeout for LLM calls |
| `LLM_MAX_RETRIES` | No | `2` | Number of retry attempts on transient LLM errors |

### Local (bare Python)

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t queuestorm-investigator .
docker run -p 8000:8000 --env-file .env queuestorm-investigator
```

Or pull and run (if published):

```bash
docker pull <your-dockerhub-username>/queuestorm-investigator:latest
docker run -p 8000:8000 \
  -e GROQ_API_KEY=your_key_here \
  queuestorm-investigator:latest
```

### Verify the service is up

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

---

## API Reference

### `GET /health`

Returns `{"status": "ok"}` within 60 seconds of service start.

### `POST /analyze-ticket`

Accepts a JSON body and returns a structured analysis.

**Example request:**

```json
{
  "ticket_id": "TKT-001",
  "complaint": "I sent 5000 taka to the wrong number around 2pm today.",
  "language": "en",
  "channel": "in_app_chat",
  "user_type": "customer",
  "campaign_context": "boishakh_bonanza_day_1",
  "transaction_history": [
    {
      "transaction_id": "TXN-9101",
      "timestamp": "2026-04-14T14:08:22Z",
      "type": "transfer",
      "amount": 5000,
      "counterparty": "+8801719876543",
      "status": "completed"
    }
  ]
}
```

**Example response:**

```json
{
  "ticket_id": "TKT-001",
  "relevant_transaction_id": "TXN-9101",
  "evidence_verdict": "consistent",
  "case_type": "wrong_transfer",
  "severity": "high",
  "department": "dispute_resolution",
  "agent_summary": "Customer reports sending 5000 BDT to an unintended recipient via TXN-9101 at 14:08. Transaction is completed and matches the complaint.",
  "recommended_next_action": "Verify recipient details for TXN-9101 and initiate wrong-transfer review process via dispute resolution workflow.",
  "customer_reply": "We have noted your concern about transaction TXN-9101. Any eligible amount will be returned through official channels after investigation. Please do not share sensitive credentials here.",
  "human_review_required": true,
  "confidence": 0.91,
  "reason_codes": ["wrong_transfer", "transaction_match", "high_value"]
}
```

**HTTP status codes:**

| Code | Meaning |
|---|---|
| 200 | Successful analysis |
| 400 | Malformed input (invalid JSON, missing required fields) |
| 401 | Missing or invalid `X-Api-Key` (only if `QUEUESTORM_API_KEY` is set) |
| 413 | Complaint too long (>5000 chars) or too many transactions (>200) |
| 422 | Semantically invalid input (e.g. empty complaint) |
| 500 | Internal error — no secrets or stack traces exposed |

---

## Architecture

```
Client
  │
  ▼
FastAPI (async)
  ├── Input validation (Pydantic v2)
  ├── Input limits (complaint length, transaction count)
  │
  ├── build_user_message()   ← formats complaint + transactions
  │
  ├── call_llm()             ← async, threaded, retries + timeout
  │       │
  │       └── Groq API (llama-3.3-70b-versatile)
  │
  ├── coerce_and_validate_llm_output()  ← JSON extraction + Pydantic validation
  │
  ├── scan_and_sanitize_customer_reply()  ← regex safety layer
  │
  └── JSONResponse (AnalysisOutput schema)
```

**Fallback behaviour:** If the LLM call fails (timeout, API error, retry exhaustion) or the response fails schema validation, the service returns a deterministic fallback response with `human_review_required: true`, `evidence_verdict: insufficient_data`, and `reason_codes: ["llm_unavailable"]` or `["parse_error"]`. The service never crashes on bad input or LLM failure.

---

## Assumptions & Known Limitations

- **Language support:** Bangla and Banglish complaints are passed verbatim to the LLM. Quality of classification may be slightly lower for mixed-script inputs compared to English.
- **Transaction matching:** The LLM identifies the relevant transaction by reasoning over the complaint text and history. There is no deterministic string-match step; accuracy depends on model output quality.
- **Single-turn only:** The service has no memory across requests. Each ticket is analysed independently.
- **Groq rate limits:** On the free tier, sustained high throughput may trigger rate limiting. Consider upgrading the Groq plan or adding a queue for production-scale loads.
- **Evidence verdict nuance:** When transaction history is empty, the service always returns `insufficient_data`. When it is present but ambiguous, the LLM may occasionally return `inconsistent` where `insufficient_data` would be more precise.
- **No real financial data:** All complaint and transaction data processed during evaluation is synthetic. The service is not connected to any real payment system.
