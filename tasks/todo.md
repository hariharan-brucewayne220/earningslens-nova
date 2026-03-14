# EarningsLens — Implementation Plan (PRD v2.0)

> Amazon Nova AI Hackathon | Author: Hariharan | Status: In Progress
> Architecture: AWS Transcribe → Nova 2 Lite → [Nova Act + MacroDash APIs + Multimodal Vector Store] → Nova 2 Sonic

---

## Phase 0 — Project Setup

- [ ] Initialize repo structure: `backend/`, `frontend/`, `scripts/`, `data/`
- [ ] `uv venv .venv && source .venv/bin/activate`
- [ ] `requirements.txt`: fastapi, boto3, uvicorn, pdfplumber, redis, python-dotenv, httpx, numpy
- [ ] Create React+TypeScript frontend with Vite (`frontend/`)
- [ ] `.env.example` with: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `BEDROCK_REGION`, `S3_BUCKET`, `REDIS_URL`, `MACRODASH_BASE_URL`
- [ ] Verify AWS Bedrock access: Nova 2 Lite, Nova Multimodal Embeddings, Nova 2 Sonic, Nova Act
- [ ] Verify AWS Transcribe access
- [ ] Set up S3 bucket for audio + PDF storage
- [ ] Test MacroDash API: `GET {MACRODASH_BASE_URL}/api/technical-indicators/NVDA/?period=3mo`

---

## Phase 1 — F1: Audio Ingestion (AWS Transcribe)

**Goal:** Accept uploaded audio file, transcribe via AWS Transcribe, build rolling transcript.

- [ ] `backend/audio/ingestor.py` — accept `.mp3/.wav/.m4a`, upload to S3
- [ ] `backend/audio/transcribe_client.py` — wrap AWS Transcribe (poll or streaming)
- [ ] Store rolling transcript in Redis: `transcript:{session_id}`
- [ ] `POST /session/start` — returns `session_id`
- [ ] `POST /session/{id}/upload-audio` — triggers S3 upload + Transcribe job

**Note:** AWS Transcribe = audio → text. Nova 2 Sonic = voice OUTPUT only.

---

## Phase 2 — F2: Autonomous SEC Filing Retrieval (Nova Act)

**Goal:** Nova Act navigates SEC EDGAR and downloads the correct 10-Q/10-K PDF.

- [ ] `backend/filing/edgar_navigator.py` — Nova Act agent navigates EDGAR, downloads PDF
- [ ] Fallback: scrape investor relations page if EDGAR navigation fails
- [ ] Store PDF to S3: `filings/{ticker}/{date}.pdf`
- [ ] `POST /filing/fetch` — body: `{ticker}`, returns S3 path + status
- [ ] Status indicator: "Retrieving NVDA 10-Q..." → "Filing ready"
- [ ] **Risk mitigation:** Pre-run Nova Act before demo, cache PDF locally

---

## Phase 3 — F3: Multimodal Filing Embedding (Nova Multimodal Embeddings)

**Goal:** Embed full SEC PDF — text, tables, AND charts — into queryable vector store.

- [ ] `backend/embedding/pdf_processor.py` — extract text, tables, chart images per page
- [ ] `backend/embedding/embedder.py` — Nova Multimodal Embeddings for all chunk types
- [ ] `backend/embedding/vector_store.py` — in-memory numpy cosine store
- [ ] `POST /filing/embed` — async embedding job
- [ ] `GET /filing/embed/{job_id}` — progress %
- [ ] **Risk mitigation:** Pre-process NVDA 10-Q, note exact pages with revenue charts

---

## Phase 4 — F4: MacroDash API Pre-Fetch + Cache

**Goal:** Pre-fetch all MacroDash data at session start — zero latency during live verification.

- [ ] `backend/macrodash/client.py` — parallel fetch + Redis cache (TTL 5 min)
- [ ] `POST /session/{id}/prefetch` — triggers pre-fetch for ticker
- [ ] **Risk mitigation:** Demo runs on Redis cache if MacroDash is unreachable

### Endpoints Used

| Endpoint | Data | Used For |
|---|---|---|
| `GET /api/technical-indicators/{symbol}/?period=3mo` | RSI, MACD, Bollinger Bands, SMA, EMA | Momentum claim context |
| `GET /api/stocks/{symbol}/` | Price, P/E, market cap, analyst recs | Company context |
| `GET /api/company/{symbol}/financials/` | Income stmt, balance sheet, cash flow | Financial metric cross-reference |
| `GET /api/economic-data/` | GDP, unemployment, inflation, consumer spending (FRED) | Macro context for demand claims |
| `GET /api/sentiment/{symbol}/` | AI sentiment score | Sentiment context in briefing |
| `GET /api/news/{symbol}/` | Recent financial news | Context for flagged claims |

---

## Phase 5 — F5: Real-Time Claim Extraction + Triple Verification (Nova 2 Lite)

**Goal:** Rolling transcript → extract claims → triple cross-reference → classify.

- [ ] `backend/verification/claim_extractor.py` — Nova 2 Lite structured JSON output: `[{claim_text, metric, value, unit, period}]`
- [ ] `backend/verification/verifier.py` — three checks per claim:
  - **CHECK 1 (SEC):** Query vector store → delta >2% = FLAGGED
  - **CHECK 2 (Technical):** Cached MacroDash RSI/MACD → overbought/divergence flags
  - **CHECK 3 (Macro):** Cached MacroDash FRED → consumer spending contradiction
  - Nova 2 Lite reasons over all three → verdict + evidence JSON
- [ ] `backend/verification/pipeline.py` — background loop every 30s
- [ ] `GET /session/{id}/claims` — all verified claims

**Verdicts:** `VERIFIED` / `FLAGGED` (>2% delta or directional mismatch) / `UNVERIFIABLE` (forward guidance) + optional `CONTEXT` flags

---

## Phase 6 — F6: Live Dashboard UI

**Goal:** Split-panel real-time dashboard. Reuse MacroDash React component patterns.

- [ ] `SessionSetup.tsx` — ticker input + audio upload
- [ ] `TranscriptPanel.tsx` — scrolling live transcript with claim highlights
- [ ] `VerificationFeed.tsx` — color-coded claim cards (green/red/yellow), hover for filing snippet + MacroDash data
- [ ] `ConfidenceMeter.tsx` — % claims verified
- [ ] `MacroDashContext.tsx` — live indicator data panel
- [ ] `GET /session/{id}/stream` — SSE endpoint
- [ ] `backend/api/stream.py` — SSE implementation
- [ ] Desktop-first, 50/50 split layout

---

## Phase 7 — F7: Nova 2 Sonic Interactive Briefing

**Goal:** Auto-triggered end-of-call voice briefing + conversational Q&A.

- [ ] `backend/briefing/generator.py` — Nova 2 Lite generates briefing text
- [ ] `backend/briefing/sonic_tts.py` — Nova 2 Sonic TTS for briefing audio
- [ ] `backend/briefing/sonic_qa.py` — speech-to-speech Q&A grounded in verified data
- [ ] `POST /session/{id}/end` — triggers briefing
- [ ] `GET /session/{id}/briefing` — briefing text + audio URL
- [ ] Frontend: audio player auto-plays, voice waveform visualization
- [ ] **Risk mitigation:** Pre-generate briefing audio 5 min before recording

---

## Phase 8 — F8: Structured Report Export

- [ ] `backend/report/json_exporter.py` — per-claim: status, delta, SEC source page, MacroDash RSI, FRED PCE
- [ ] `GET /session/{id}/report.json`
- [ ] `GET /session/{id}/report.pdf` — flagged in red, verified in green
- [ ] Frontend: download buttons after call ends

---

## Phase 9 — Integration & Demo Prep

- [ ] End-to-end test: NVIDIA earnings call (Q3/Q4 FY2025)
- [ ] End-to-end test: Tesla earnings call
- [ ] Tune delta threshold (default 2%, test 3% and 5%)
- [ ] Verify MacroDash pre-fetch caches before call hits 2-min mark
- [ ] Demo dry-run: 3-minute walkthrough per script below

---

## Phase 10 — Polish & Submission

- [ ] Blog post (1,200 words) for builder.aws.com — angle: financial charts in SEC PDFs invisible to text-only tools
- [ ] README: setup + architecture diagram
- [ ] Clean credentials / dev hacks
- [ ] Deploy (AWS Lambda + API Gateway or EC2)
- [ ] Final submission package

---

## Demo Script — 3 Minutes Exactly

| Time | Action |
|---|---|
| 0:00–0:15 | Hook: "Every quarter, CEOs make dozens of numerical claims. Analysts can't verify in real time. EarningsLens does." Upload NVDA audio. |
| 0:15–0:40 | Nova Act: speed-cut of EDGAR navigation. "Nova Act pulls the 10-Q. No human touch." Filing ready. |
| 0:40–1:10 | GREEN: CEO "Data center revenue grew 122% YoY." Badge green. Filing snippet shown. "Verified." |
| 1:10–1:45 | RED: CEO "Strong consumer demand momentum..." Badge RED. Split: 10-Q + MacroDash RSI 78.4 + FRED PCE -0.3%. "Three sources. All contradict." |
| 1:45–2:15 | Sonic briefing plays: "8 verified. 1 flagged. CEO stated 23%, 10-Q shows 18.4%. RSI overbought. Consumer spending declined." |
| 2:15–2:45 | Voice Q&A: "Explain the flagged claim simply." Sonic responds. Voice waveform shown. |
| 2:45–3:00 | Report export. "MacroDash. Nova Act. Multimodal Embeddings. Nova 2 Lite. Nova 2 Sonic. Every component necessary." |

---

## Architecture Reference

```
[ Earnings Call Audio File ]
         |
         v
[ AWS Transcribe ] ──────────────────> [ Rolling Transcript (Redis) ]
                                                    |
[ Nova Act: EDGAR Navigator ]                       v
         |                           [ Nova 2 Lite: Claim Extractor ]
         v                                          |
[ PDF Filing (S3) ]           ┌─────────────────── ┼ ──────────────────────┐
         |                    v                     v                       v
         v          [ MacroDash APIs ]   [ Multimodal Vector Store ]  [ FRED / MacroDash ]
[ Nova Multimodal    /api/technical-     (Nova Multimodal Embeddings  /api/economic-data/
  Embeddings ]       indicators/{sym}/   indexed from SEC PDF)
  Indexes PDF text,  (Redis cached)      (text + charts + tables)
  tables, charts
         |                    |                     |                       |
         └────────────────────┴─────────────────────┴───────────────────────┘
                                                    |
                                      [ Nova 2 Lite: Verifier ]
                                      VERIFIED / FLAGGED / UNVERIFIABLE
                                                    |
                          ┌─────────────────────────┴──────────────────────┐
                          v                                                 v
               [ Live Dashboard UI ]                           [ Nova 2 Sonic ]
               Transcript + verification feed                  Interactive Q&A
               Color-coded badges                              End-of-call briefing
               MacroDash data panels
                          |
               [ JSON + PDF Report ]
```

---

---

## Hackathon Submission Checklist

- [ ] Primary category selected: **Agentic AI**
- [ ] Demo video includes `#AmazonNova` hashtag
- [ ] Repo shared with testing@devpost.com
- [ ] Repo shared with Amazon-Nova-hackathon@amazon.com
- [ ] Blog post published on builder.aws.com
- [ ] Feedback survey filled out ($50)

---

## Review

> Fill in after completion.

## Lessons

> Fill in after corrections.
