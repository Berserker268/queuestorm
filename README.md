# QueueStorm Investigator

An AI-powered SupportOps API service for intelligent ticketing and complaint analysis in digital finance platforms. Built for SUST CSE Carnival 2026 · Codex Community Hackathon.

**QueueStorm** leverages large language models (LLMs) to automatically analyze support tickets, cross-reference transaction histories, and provide structured recommendations for support agents—all while enforcing strict security guardrails.



## Features

✅ **Intelligent Ticket Analysis**
- Automatically categorizes support complaints into predefined case types
- Extracts relevant transaction IDs and correlates with complaint context
- Assigns severity levels (low, medium, high, critical)
- Recommends appropriate departments for escalation

✅ **Transaction-Aware Reasoning**
- Cross-references complaint text with transaction history
- Validates evidence consistency (consistent, inconsistent, insufficient)
- Supports up to 200 transaction entries per ticket
- Handles multiple transaction types (transfers, payments, cash-in/out, settlements, refunds)

✅ **Multilingual Support**
- English and Bengali language support for customer replies
- Language-aware safe response fallbacks

✅ **Security-First Design**
- Blocks forbidden content (PIN, OTP, passwords, CVV, full card numbers)
- Never commits to refunds or reversals in automated replies
- Prevents prompt injection in complaint text
- Automatic flagging for human review when safety concerns detected

✅ **Structured Output**
- Returns valid JSON conforming to strict schema
- Includes confidence scores and reason codes for audit trails
- Pydantic v2 validation ensures type safety

---

## Tech Stack

| Component | Technology |
|---|---|
| **Framework** | FastAPI 0.115.0 + Uvicorn |
| **AI Model** | Llama 3.3 70B Versatile via Groq |
| **API Client** | OpenAI Python SDK (OpenAI-compatible) |
| **Data Validation** | Pydantic v2.9.2 |
| **Async Runtime** | asyncio |
| **Containerization** | Docker |

### Why Groq?

- **Fast inference**: ~300 tokens/second
- **Free tier**: 30 RPM, 1,000 RPD (sufficient for hackathon)
- **No credit card required**: Simple email signup
- **OpenAI-compatible API**: Drop-in replacement for OpenAI SDK
- **Strong multilingual capabilities**: Handles English and Bengali

---

## Quick Start

### Prerequisites

- Python 3.9+
- A free Groq API key (30 seconds to obtain)
- Docker (optional)

### 1. Get a Groq API Key

1. Visit [https://console.groq.com/keys](https://console.groq.com/keys)
2. Sign up with your email
3. Generate a new API key
4. Copy the key to a safe location

### 2. Run Locally

```bash
# Clone the repository
git clone https://github.com/Berserker268/queuestorm.git
cd queuestorm

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set Groq API key
export GROQ_API_KEY=gsk_your_key_here

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000
