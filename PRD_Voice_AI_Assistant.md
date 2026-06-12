# PRD — Voice AI Finance Assistant
**Project:** McAllen Trade Analyst — Voice Layer  
**Author:** Rani  
**Date:** 2026-06-05  
**Status:** Draft v1.0

---

## 1. Overview

Add a voice interface on top of the existing **McAllen Trade Analyst** platform. The user speaks; the assistant listens, reasons using all existing data sources, and speaks back. This is a voice layer — not a new app. The backend, Supabase database, Stripe integration, and Claude AI are already in place.

---

## 2. What Already Exists (Do Not Rebuild)

| Component | Status | Details |
|---|---|---|
| FastAPI backend | ✅ Live | `backend/main.py`, port 8000 |
| Claude API integration | ✅ Live | `claude-opus-4-8`, prompt caching, web search tool |
| Supabase / PostgreSQL | ✅ Live | `analyses` table: id, ticker, analysis_text, price, verdict, searched_at |
| Yahoo Finance data | ✅ Live | 1yr OHLCV history, price, MA20/50/200, volume via yfinance |
| Stripe | ✅ Partial | Donation/support checkout link only — no subscription backend yet |
| McAllen analysis engine | ✅ Live | `/analyze/{ticker}` — streaming SSE, full technical + fundamental analysis |
| Price endpoint | ✅ Live | `/price/{ticker}` — price, 52W high/low, MAs, 30 candles |
| History endpoints | ✅ Live | GET/DELETE `/history`, `/history/{id}` |
| Explain endpoint | ✅ Live | POST `/explain` — plain-language explanation of finance terms |
| Web frontend | ✅ Live | Vanilla JS, TradingView chart, PWA, mobile canvas chart |
| Rate limiting | ✅ Live | Per-IP daily limit via `DAILY_LIMIT` env var |
| Anthropic finance agents | ✅ Cloned | `financial-services/` — 10 agents incl. Earnings Reviewer & Model Builder |

**The voice assistant calls these existing endpoints and agents — it does not reimplement them.**

---

## 3. Problem Statement

The app already does deep McAllen-style analysis, but requires typing. The goal is to make everything accessible through natural speech: ask about a stock, hear the analysis, dig into the history, ask follow-up questions — all hands-free.

---

## 4. Core User Story

> "I say 'Run a McAllen analysis on NVDA', she streams it back as audio. I say 'What was my last analysis on AAPL?', she pulls it from the database and reads the verdict and key takeaways. I say 'Explain what a death cross means', she tells me. We keep going."

---

## 5. Functional Requirements

### 5.1 Voice Interface
- **Voice input:** Push-to-talk (Phase 1) or wake word (Phase 2)
- **Voice output:** TTS reads Claude's responses aloud
- **Conversation memory:** Multi-turn context within a session
- **Language:** English (primary)

### 5.2 Stock Analysis (uses existing `/analyze/{ticker}`)
- "Run analysis on [TICKER]" → calls `/analyze/{ticker}`, streams audio back
- Reads out verdict + key takeaways (entry trigger, stop loss, risk/reward)
- Handles follow-up questions about the just-completed analysis

### 5.3 Price Data (uses existing `/price/{ticker}`)
- "What's [TICKER] trading at?" → calls `/price/{ticker}`, speaks price + MAs
- "Is [TICKER] above its 200-day?" → parses price data, answers directly

### 5.4 History Access (uses existing `/history`)
- "What was my last analysis on TSLA?" → queries history, reads verdict + summary
- "Show me all my bearish calls this week" → filters history by verdict + date

### 5.5 Finance Term Explanation (uses existing `/explain`)
- "What is a [term]?" → calls `/explain`, reads the plain-language answer

### 5.6 Earnings Research & Financial Modeling (via Anthropic Finance Agents)
The `financial-services/` repo is already cloned in the workspace. The voice assistant triggers these agents by voice:

| Voice trigger | Agent | Output |
|---|---|---|
| "Run earnings review on [TICKER]" | **Earnings Reviewer** | Transcript + filings → model update → `out/note-<ticker>.docx` |
| "Build a model for [TICKER]" | **Model Builder** | DCF / LBO / 3-statement / comps → `out/model-<ticker>.xlsx` |
| "Research [SECTOR or TICKER]" | **Market Researcher** | Sector overview, competitive landscape, peer comps |
| "Prep me for [TICKER]" | **Meeting Prep Agent** | Briefing pack with key facts and recent news |

Each agent is deployed via the Claude Managed Agents API (`POST /v1/agents`) using the cookbooks in `financial-services/managed-agent-cookbooks/`. The voice orchestrator sends a steering event and streams the result back as audio summary.

**Data sources used (no paid connectors):**
- Web search (Claude's built-in `web_search` tool) — same tool already powering `/analyze/{ticker}`
- `/history` endpoint — feeds past analyses for the ticker as additional context before triggering the agent
- Yahoo Finance via `/price/{ticker}` — live price, MAs, and 30-candle history

FactSet and Daloopa are expensive enterprise subscriptions — not used.

### 5.7 Document Q&A (new — reads Trade Analyst workspace)
- Read PDFs and files from the workspace (e.g. the McAllen charting PDF)
- Answer questions grounded in document content
- "What does McAllen say about gaps?" → searches and reads from the PDF

### 5.7 Supabase Write-back (extends existing DB)
- Save voice session summaries and notes back to Supabase
- New table: `voice_sessions` (session_id, transcript, summary, created_at)

### 5.8 Stripe Read (new)
- "How much did I make this month?" → reads Stripe revenue/charges via Stripe API
- Read-only; no write or refund actions

---

## 6. Architecture

```
[Microphone]
     │
     ▼
[STT — Whisper / Deepgram]
     │
     ▼
[Voice Orchestrator — New Python module]
     │  (calls existing backend via HTTP, or imports directly)
     │
     ├──► GET /analyze/{ticker}                    ← existing
     ├──► GET /price/{ticker}                      ← existing
     ├──► GET /history                             ← existing
     ├──► POST /explain                            ← existing
     ├──► POST /v1/agents (earnings-reviewer)      ← financial-services repo
     ├──► POST /v1/agents (model-builder)          ← financial-services repo
     ├──► POST /v1/agents (market-researcher)      ← financial-services repo
     ├──► [Tool: read_document]                    ← new (PDF/file access)
     ├──► [Tool: stripe_read]                      ← new (Stripe API)
     └──► [Tool: write_voice_session]              ← new (Supabase)
     │
     ▼
[Claude API — claude-opus-4-8]
(same model already in use; add voice system prompt)
     │
     ▼
[TTS — ElevenLabs or OpenAI TTS]
     │
     ▼
[Speaker]
```

### New Module
- `backend/voice_assistant.py` — voice loop, STT, orchestration, TTS
- Reuses existing `backend/main.py` DB helpers and analysis logic
- New env vars: `DEEPGRAM_API_KEY` (or use Whisper locally), `ELEVENLABS_API_KEY`, `STRIPE_SECRET_KEY`

---

## 7. New API Routes (add to existing `main.py`)

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/voice/session` | Save a voice session transcript + summary |
| GET | `/voice/sessions` | List voice session history |
| GET | `/voice/sessions/{id}` | Fetch a specific session |

---

## 8. New Database Table

```sql
CREATE TABLE voice_sessions (
    id SERIAL PRIMARY KEY,
    session_id TEXT,
    transcript TEXT,
    summary TEXT,
    tickers_discussed TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 9. Voice Persona

- **Name:** TBD (suggest asking user to name her)
- **Tone:** Confident, concise, McAllen-literate — sounds like a sharp analyst partner
- **Style:** Verdict first, details on request. Uses finance vocabulary naturally.
- **Boundaries:** Frames all outputs as analysis, not regulated investment advice

---

## 10. Phased Rollout

### Phase 1 — Voice Loop (Weeks 1–2)
- [ ] STT pipeline (Whisper local or Deepgram)
- [ ] TTS pipeline (ElevenLabs or OpenAI TTS)
- [ ] `voice_assistant.py` CLI script: speak → Claude → speak back
- [ ] Calls `/price/{ticker}` and `/explain` via voice
- [ ] Basic session memory (conversation list passed to Claude)

### Phase 2 — Full Analysis by Voice (Weeks 3–4)
- [ ] Call `/analyze/{ticker}` and read verdict + takeaways aloud
- [ ] Call `/history` to recall past analyses
- [ ] Handle multi-turn follow-up questions about analysis results

### Phase 3 — Finance Agents + Document Q&A + Stripe (Weeks 5–7)
- [ ] Deploy Earnings Reviewer managed agent (`financial-services/managed-agent-cookbooks/earnings-reviewer`)
- [ ] Deploy Model Builder managed agent (`financial-services/managed-agent-cookbooks/model-builder`)
- [ ] Deploy Market Researcher managed agent
- [ ] Wire voice triggers → steering events → managed agents → audio summary
- [ ] `read_document` tool (parse McAllen PDF and other workspace files)
- [ ] `stripe_read` tool (read revenue, charges, subscriptions)
- [ ] Save voice sessions to Supabase (`voice_sessions` table)
- [ ] Configure agents to use web search + `/history` + `/price` instead of paid connectors

### Phase 4 — Web UI Integration (Weeks 7–8)
- [ ] Microphone button in existing frontend
- [ ] Waveform visualization while listening / speaking
- [ ] Wake word detection (optional)
- [ ] Voice session history view in the existing history drawer

---

## 11. Open Questions

1. **STT provider:** Whisper (free, local, slightly slower) vs Deepgram (cloud, ~300ms latency)?
2. **TTS provider:** ElevenLabs (most natural) vs OpenAI TTS `tts-1-hd` (cheaper, still good)?
3. **Voice name/persona:** What do you want to call her?
4. **Stripe scope:** Read-only forever, or write/refund capability later?
5. **Wake word:** "Hey [name]" always-on, or push-to-talk only?
6. **Frontend integration:** New standalone voice page, or mic button added to the existing UI?

---

## 12. Success Metrics

- Full McAllen analysis completable hands-free in < 30 seconds
- History recall and follow-up questions work in one conversation
- No new backend infrastructure required — voice layer sits on top of what exists
- User replaces daily manual analysis workflow within 2 weeks of Phase 1

---

*This PRD is a living document — update as scope evolves.*
