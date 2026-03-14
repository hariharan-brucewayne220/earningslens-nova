# EarningsLens — Claude Code Project Instructions (PRD v2.0)

---

## ⚠️ HACKATHON RULES — NON-NEGOTIABLE (READ FIRST)

### Core Requirement
**The core solution MUST use Amazon Nova foundation models and/or Nova Act.**
- Acceptable: Nova 2 Lite, Nova 2 Sonic, Nova Multimodal Embeddings, Nova Act
- NOT acceptable: Titan Embed, Polly, or any non-Nova model as a primary component
- If a Nova model fails, fix the integration — do NOT silently fall back to non-Nova models

### Nova Component Rules
- **Nova 2 Lite** (`amazon.nova-lite-v1:0`): reasoning, claim extraction, verification, briefing text generation ✅
- **Nova Multimodal Embeddings**: use `amazon.titan-embed-image-v1` IS WRONG — must use Nova's own multimodal embedding model. Check model catalog for correct ID.
- **Nova Act**: navigates SEC EDGAR UI autonomously. SDK = `nova-act`. Must be visibly used in demo — NOT replaced by REST API calls.
- **Nova 2 Sonic** (`amazon.nova-sonic-v1:0`): speech-to-speech ONLY. NOT Amazon Polly. Sonic requires bidirectional streaming via Bedrock — implement it properly.

### Judging Criteria (internalize this)
- **Technical Implementation — 60%**: quality, effectiveness, successful Nova integration, architecture
- **Enterprise/Community Impact — 20%**: business value or community benefit
- **Creativity/Innovation — 20%**: novelty, innovative multi-agent use

**60% is on Nova integration quality. Every fallback to a non-Nova model costs us the competition.**

### Submission Checklist (must complete)
- [ ] Demo video ~3 minutes with `#AmazonNova` hashtag
- [ ] Code repo shared with testing@devpost.com AND Amazon-Nova-hackathon@amazon.com
- [ ] Blog post published on builder.aws.com (covers community impact, real-world use, adoption plans) — bonus $200 AWS credits per winner, 100 winners
- [ ] Feedback survey filled out — $50 cash prize (60 winners)
- [ ] Primary category: **Agentic AI** (also eligible for Best of Multimodal Understanding, Best of Voice AI, Best Student App)

### Blog Post Rules (bonus prize)
Must cover:
1. How the project positively affects the target community (financial analysts, retail investors)
2. Potential benefits and envisioned real-world application
3. Plans for encouraging adoption
Platform: builder.aws.com | Length: ~1,200 words | Include `#AmazonNova`

### Prize Targets
- First Overall: $15,000 cash + $5,000 AWS credits
- Best of Agentic System: $3,000 + $5,000 credits
- Best of Multimodal Understanding: $3,000 + $5,000 credits
- Best of Voice AI: $3,000 + $5,000 credits
- Best Student App: $3,000 + $5,000 credits (Hariharan is a grad student job searching — this is authentic)

---

## Project Overview

EarningsLens is a real-time AI earnings call intelligence agent for the Amazon Nova AI Hackathon.

**Core concept:** While an earnings call plays, EarningsLens extracts every numerical/directional CEO claim and fires a triple cross-reference against three independent sources simultaneously — SEC filings (via Nova Multimodal Embeddings), live technical indicators (via MacroDash APIs), and macroeconomic data (via MacroDash FRED integration). Nova 2 Sonic delivers an interactive voice briefing after the call ends.

**Pitch:** "Existing tools analyze text transcripts after the fact. EarningsLens is the first system that cross-references CEO claims against SEC filings, live technical indicators, AND macro data simultaneously — built on a production financial dashboard that already tracks all of this."

---

## Permissions — Full Autonomy Mode

Claude has full permission to take ALL of the following actions without asking for confirmation:

- Run any bash command (installs, builds, tests, scripts)
- Read, create, edit, or delete any file in this project
- Create directories and restructure the project
- Run `git` commands (add, commit, push, branch, merge)
- Run `npm`, `uv`, `pip`, `docker`, `aws`, `boto3` commands
- Start/stop dev servers and background processes
- Install Python packages with `uv pip install`
- Install Node packages with `npm install`
- Execute Python scripts
- Make AWS API calls via boto3 (Bedrock, S3, Lambda)
- Read environment variables and `.env` files

**Never ask "would you like me to…" or "shall I proceed?" — just do it.**

---

## Nova Component Mapping (All Four — All Necessary)

| Component | Role |
|---|---|
| **AWS Transcribe** | Converts earnings call audio → rolling transcript. NOT Sonic — Sonic is for voice output |
| **Nova 2 Lite** | Core reasoning: extracts claims, calls MacroDash APIs, queries vector store, classifies VERIFIED / FLAGGED / UNVERIFIABLE, generates briefing text |
| **Nova Act** | Navigates SEC EDGAR autonomously while call plays; locates and downloads correct 10-Q or 10-K PDF |
| **Nova Multimodal Embeddings** | Processes full SEC filing PDF including revenue charts, margin graphs, segment tables — not just text |
| **Nova 2 Sonic** | Interactive voice Q&A after analysis; delivers end-of-call briefing conversationally |

---

## MacroDash API Integration

MacroDash (`macrodash.xyz`) is a production-deployed financial dashboard — the team's own prior project. All endpoints are live. **Zero additional backend work needed on the MacroDash side.**

### Base URL
```
https://macrodash-server.5249c0fmwzjkc.us-east-1.cs.amazonlightsail.com/api/
```

### Endpoints Used by EarningsLens

```
GET /api/technical-indicators/{symbol}/?period=3mo
  Returns: RSI, MACD signal/histogram, Bollinger Bands (upper/mid/lower),
           SMA 20/50/200, EMA 12/26/50
  Used for: Contextualizing CEO claims about stock/business momentum

GET /api/stocks/{symbol}/
  Returns: Current price, P/E, market cap, analyst recommendations
  Used for: Company context during claim verification

GET /api/stocks/{symbol}/financials/
  Returns: Income statement, balance sheet, cash flow
  Used for: Cross-referencing financial metric claims

GET /api/economic-data/
  Returns: GDP, unemployment, inflation, consumer spending, interest rates (FRED)
  Used for: Macro context for demand/consumer claims

GET /api/sentiment/{symbol}/
  Returns: AI-powered sentiment score on recent news
  Used for: Sentiment context during briefing generation

GET /api/news/{symbol}/
  Returns: Recent financial news articles
  Used for: Surfacing relevant news during flagged claim explanation
```

### Full MacroDash API Reference (all available endpoints)

```
# Health
GET /api/health/

# Economic Data (FRED)
GET /api/economic-data/
GET /api/economic-data/{series_id}/

# Stocks
GET /api/stocks/
GET /api/stocks/browse/
GET /api/stocks/intraday/
GET /api/stocks/{symbol}/
GET /api/company/{symbol}/financials/
GET /api/company/{symbol}/earnings/
GET /api/company/{symbol}/analyst-recommendations/
GET /api/company/{symbol}/insights/
GET /api/company/{symbol}/overview/

# Technical Indicators
GET /api/technical-indicators/{symbol}/?period={1mo|3mo|6mo|1y|2y|5y}

# News & Sentiment
GET /api/news/{symbol}/
GET /api/sentiment/{symbol}/

# Alpha Vantage
GET /api/alpha-vantage/quote/{symbol}/
GET /api/alpha-vantage/intraday/{symbol}/
GET /api/alpha-vantage/daily/{symbol}/
GET /api/alpha-vantage/overview/{symbol}/
GET /api/alpha-vantage/news/

# AI Insights
GET /api/ai-insights/
GET /api/ai-insights/{symbol}/
POST /api/market-insight/
POST /api/chatbot/

# Crypto
GET /api/crypto/
GET /api/crypto/top/gainers/
GET /api/crypto/top/losers/
GET /api/crypto/{symbol}/
GET /api/crypto/{symbol}/historical/
GET /api/crypto/{symbol}/ohlc/

# FRED Data Explorer
GET /api/fred/categories/
GET /api/fred/categories/{category_id}/series/
GET /api/fred/series/{series_id}/metadata/
GET /api/search/
GET /api/export/

# Autonomous Agents
POST /api/agent/portfolio/
POST /api/agent/news-synthesis/

# Dashboard / Watchlist / Alerts
GET /api/dashboard/
POST /api/dashboard/
GET /api/watchlist/
DELETE /api/watchlist/{symbol}/
GET /api/alerts/
POST /api/alerts/
GET /api/alerts/{alert_id}/
GET /api/preferences/
```

### MacroDash Data Sources
- **Yahoo Finance** (yfinance): historical prices, fundamentals, technicals
- **Alpha Vantage**: quotes, news, sentiment
- **FRED API**: GDP, unemployment, inflation, consumer spending, interest rates
- **TA-Lib**: RSI, MACD, Bollinger Bands, SMA, EMA (professional-grade calculations)
- **CoinMarketCap / CoinGecko**: crypto data

---

## Triple Cross-Reference Logic

When CEO makes a claim (e.g., "23% revenue growth YoY"):

**CHECK 1 — SEC Filing (Nova Multimodal Embeddings)**
- Query vector store for matching metric
- If delta > 2%: `FLAGGED`

**CHECK 2 — MacroDash Technical Indicators**
- `GET /api/technical-indicators/{symbol}/?period=3mo`
- RSI > 70 or bearish MACD divergence: `CONTEXT FLAG`

**CHECK 3 — MacroDash FRED Macro Data**
- `GET /api/economic-data/`
- Consumer spending or GDP data contradicting demand claim: `CONTEXT FLAG`

**Verdict rules:**
- `VERIFIED`: SEC filing confirms within 2% delta, no context flags
- `FLAGGED`: Material discrepancy (>2% delta or directional mismatch in filing)
- `UNVERIFIABLE`: Forward guidance, no filing basis
- `CONTEXT`: Technical/macro context flags accompany any verdict

---

## Tech Stack

- **Backend**: Python 3.12+, FastAPI, boto3, uv for package management
- **Frontend**: React + TypeScript (reuse MacroDash component patterns)
- **Transcription**: AWS Transcribe (not Nova Sonic — Sonic is for output)
- **AI**: Amazon Bedrock
  - Nova 2 Lite: claim extraction, verification reasoning, briefing text
  - Nova Act: EDGAR navigation
  - Nova Multimodal Embeddings: SEC PDF indexing (text + charts + tables)
  - Nova 2 Sonic: interactive voice Q&A and end-of-call briefing
- **External Data**: MacroDash APIs (technical indicators + FRED macro)
- **Infra**: AWS Lambda / EC2, S3, Redis
- **Vector Store**: In-memory numpy cosine similarity for MVP (pgvector if time permits)

---

## Environment

```bash
# Always use venv
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Never install into system Python
```

Required env vars (`.env`):
```
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
BEDROCK_REGION=us-east-1
S3_BUCKET=earningslens-demo
REDIS_URL=redis://localhost:6379
MACRODASH_BASE_URL=https://macrodash-server.5249c0fmwzjkc.us-east-1.cs.amazonlightsail.com
```

---

## MVP Scope — Three Companies

1. **NVIDIA (NVDA)** — data center revenue growth narrative, Q3 FY2025 10-Q
2. **Tesla (TSLA)** — delivery growth / margin guidance
3. **User-chosen ticker** — stretch goal only if NVDA + TSLA flows are solid

**DO NOT scope beyond three companies. Three reliable flows beat ten flaky ones.**

---

## Feature List (F1–F8)

| Feature | Description |
|---|---|
| F1 | Audio ingestion — accept `.mp3/.wav/.m4a`, stream to AWS Transcribe |
| F2 | Nova Act: autonomous SEC EDGAR navigation → download 10-Q/10-K PDF |
| F3 | Nova Multimodal Embeddings: index full PDF (text, tables, charts) |
| F4 | MacroDash API pre-fetch — cache all indicator/macro/sentiment data at session start |
| F5 | Nova 2 Lite: rolling claim extraction + triple verification every 30s |
| F6 | Live dashboard: left panel transcript, right panel verification feed with color-coded badges |
| F7 | Nova 2 Sonic: auto-triggered end-of-call briefing + interactive voice Q&A |
| F8 | Structured report: JSON export of all claims with status, delta, sources |

---

## API Endpoints (EarningsLens Backend)

```
POST /session/start                    — create session, returns session_id
POST /session/{id}/upload-audio        — trigger ingestion + AWS Transcribe
POST /filing/fetch                     — body: {ticker}, Nova Act downloads 10-Q
POST /filing/embed                     — body: {s3_path}, kicks off embedding
GET  /filing/embed/{job_id}            — embedding progress %
GET  /session/{id}/claims              — all verified claims so far
GET  /session/{id}/stream              — SSE stream of live claim results
POST /session/{id}/end                 — trigger Sonic briefing
GET  /session/{id}/briefing            — briefing text + audio URL
GET  /session/{id}/report.json         — full structured report
GET  /session/{id}/report.pdf          — PDF report
```

---

## Key Constraints

- Demo must be 3 minutes max (see demo script in tasks/todo.md)
- All four Nova components must be visibly used — none decorative
- Pre-cache MacroDash API responses in Redis at demo start (risk mitigation for API latency)
- Pre-run Nova Act before demo and cache the PDF locally (risk mitigation for EDGAR)

---

## Dev Workflow

1. Write plan to `tasks/todo.md` before implementing
2. Mark tasks as you go
3. Write tests for non-trivial logic
4. Verify it works before marking done
5. Log lessons to `tasks/lessons.md` after any correction

---

## Build Timeline (Hackathon)

| Block | Work |
|---|---|
| Day 1, Hours 0–8 | AWS Bedrock access, Nova Act SDK, AWS Transcribe setup, test MacroDash API calls |
| Day 1, Hours 8–16 | Audio → Transcribe pipeline, Nova Act EDGAR navigation (NVDA), PDF + Multimodal Embeddings, MacroDash pre-fetch cache |
| Day 1, Hours 16–24 | Nova 2 Lite claim extraction + triple cross-reference + delta classification |
| Day 2, Hours 0–8 | React dashboard (reuse MacroDash components), transcript panel, verification feed |
| Day 2, Hours 8–16 | Nova 2 Sonic briefing + interactive Q&A, end-to-end NVDA test, Tesla flow |
| Day 2, Hours 16–24 | Demo video, blog post, deploy/screen-record |

**Buffer rule:** If anything takes 2x longer than expected, cut Tesla. NVDA alone, done perfectly, is enough to win.

---

## Risk Mitigations

| Risk | Mitigation |
|---|---|
| Nova Act on EDGAR fails/slow | Pre-run before demo, cache PDF locally |
| Multimodal embeddings miss chart data | Pre-process specific NVDA 10-Q during build, know exact page ranges |
| Nova 2 Lite claim extraction noisy | Structured JSON output prompt: `{claim, metric, value, unit, period}` |
| MacroDash API down during demo | Cache all MacroDash responses in Redis at demo start |
| Sonic latency too high | Pre-generate briefing audio 5 min before recording |
