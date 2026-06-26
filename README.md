# QueueStorm Investigator

AI/API SupportOps service for SUST CSE Carnival 2026 · Codex Community Hackathon.

## Tech Stack
- **Framework**: FastAPI + Uvicorn
- **AI Model**: `llama-3.3-70b-versatile` via [Groq](https://console.groq.com) (free, no credit card)
- **Client**: `openai` Python SDK pointed at Groq's OpenAI-compatible endpoint

## MODELS
| Model | Provider | Why |
|---|---|---|
| llama-3.3-70b-versatile | Groq (free tier) | Strong instruction-following, fast (~300 tok/s), free with no credit card, OpenAI-compatible API |

## Setup

### 1. Get a free Groq API key
1. Go to https://console.groq.com/keys
2. Sign up with just an email (no credit card)
3. Create a new API key and copy it

### 2. Run locally

```bash
# Clone and enter directory
cd queuestorm

# Install dependencies
pip install -r requirements.txt

# Set your Groq API key
export GROQ_API_KEY=your_key_here

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Run with Docker

```bash
docker build -t queuestorm .
docker run -e GROQ_API_KEY=your_key_here -p 8000:8000 queuestorm
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Returns `{"status":"ok"}` |
| POST | /analyze-ticket | Analyze a support ticket |

## AI Approach
The system prompt instructs Llama 3.3 70B to act as a fintech support copilot. It performs evidence reasoning by cross-referencing the complaint with the transaction history, then outputs a structured JSON response covering case classification, department routing, severity, and a safe customer reply.

## Safety Logic
- Never requests PIN, OTP, or passwords from customers
- Never confirms refunds — uses "eligible amount will be returned through official channels"
- Ignores prompt injection in complaint text
- Validated in code: all enum fields fall back to safe defaults if the model returns unexpected values

## Known Limitations
- Groq free tier: 30 RPM, 1,000 RPD — sufficient for hackathon judging
- Bangla NLP quality depends on Llama 3.3's multilingual capability
- No persistent storage; stateless per-request design
