# LabLink — Making College Laboratories More Accessible

**LabLink** is a GenAI-powered campus laboratory resource-sharing platform that breaks down departmental silos so college students can discover, book, request, and fairly share specialized equipment, development kits, and laboratory workstations across departments.

Built for **Google Cloud Run** using **Python (FastAPI)**, **SQLite**, **Google Gemini (`google-genai`)**, and an accessible zero-build **HTML5 / CSS3 / Modular ES6** single-page application.

---

## Core Capabilities

1. **Cross-Department Equipment Discovery & GenAI Finder**
   - Browse 14 shared resources across **5 departments** (`CSE`, `ECE`, `ROBO`, `MECH`, `BIO`) covering all three campus categories: *Laboratory Workstations*, *Development Kits*, and *Specialised Equipment*.
   - Describe a project in plain English (e.g., *"Autonomous pothole-detection drone due Friday needing edge GPU and chassis printer"*). LabLink combines hybrid semantic retrieval with **Gemini** to rank matching cross-department resources with availability and safety tips.
2. **Intelligent Slot Booking & Utilization Optimization**
   - Interactive hourly timeline (`08:00–20:00`) per resource.
   - **Smart Slot Optimizer** recommends conflict-free windows that pack sessions adjacent to existing bookings (reducing idle gaps) and load-balance across compatible equipment in less busy departments.
   - Any overlapping booking automatically flags a structured **Scheduling Conflict** instead of silently double-booking or blocking progress.
3. **Project-Based Access Requests & AI Readiness Briefs**
   - Structured request workflow capturing project track, deadline, team size, flexibility, justification, and safety acknowledgment.
   - **GenAI Feasibility Assistant** generates a concise **Faculty Readiness Brief** (readiness score, urgency tier, strengths, safety checklist, recommended action) before submission.
4. **Faculty & Lab Coordinator Approval Queue**
   - Review pending cross-department requests with full project context, AI readiness briefs, and live overlap warnings.
   - One-click **Approve**, **Request Revision**, or **Reject** with audit logging; approving an overlapping slot routes seamlessly to the Conflict Resolver.
5. **Hybrid Deterministic + GenAI Scheduling Conflict Resolution**
   - Scores competing bookings transparently on a **0–100 Fairness Scale**:
     - **Deadline Urgency (35%)** — prioritizes imminent capstone/competition/course deadlines.
     - **Usage Fairness / Anti-Starvation (25%)** — favors students with fewer recent lab hours and rewards `+1 Priority Credit` earned from yielding earlier.
     - **Equipment Specificity (20%)** — accounts for strict hardware needs vs. flexible alternatives.
     - **Project & Team Impact (20%)** — reflects track criticality and team size unblocked.
   - Generates **mathematically verified, zero-overlap feasible plans** (*Cross-Department Equivalent Reallocation*, *Same-Resource Priority Shift + Priority Credit*, and *Balanced Split-Shift*), paired with **Gemini** executive explanations and student communication drafts.
   - Applies any chosen allocation atomically inside a single database transaction.
6. **Utilization & Accessibility Insights Dashboard**
   - Live KPIs for campus utilization, cross-department access share, conflict resolution throughput, peak-hour demand, underutilized equipment opportunities, and full governance audit logs.

---

## Architecture Overview

```text
Browser SPA (Accessible HTML5 / CSS3 / Modular ES6 — Zero Login Wall)
        │  HTTPS JSON API
        ▼
Google Cloud Run — FastAPI Service (app/main.py)
  ├── Security Middleware (CSP, X-Frame-Options, nosniff, Rate Limiter)
  ├── SQLite (WAL mode, indexed schema, atomic transactions, seeded campus data)
  ├── Deterministic Scheduler & Fairness Conflict Engine (zero-hallucination math)
  └── Google GenAI Service (gemini-2.5-flash + TTL cache + deterministic fallback)
```

---

## Security & Accessibility Highlights

- **Security**: Strict Pydantic v2 validation (`extra="forbid"`), parameterized SQL queries throughout, HTML tag/control-char sanitization, XSS-safe DOM creation (`textContent` only—zero `innerHTML`), strict CSP with **zero `unsafe-inline`**, HSTS in production, 64 KB payload-size guard (`413`), bounded-memory sliding-window rate limiting (`429`), non-root Docker container, and secrets loaded strictly from environment variables.
- **Efficiency**: Bulk SQL queries eliminating N+1 patterns, SQLite WAL + memory temp store + targeted indexes, GZip response compression, static asset caching, TTL-cached GenAI queries, and a resilient multi-model Gemini fallback chain.
- **Accessibility (WCAG 2.1 AA)**: Semantic landmarks (`header`, `nav`, `main`, `footer`), skip-to-content link, ARIA `tablist` with keyboard arrow navigation and screen-reader badge counts, native HTML5 `<progress>` meters, `aria-invalid` form error focus, high-contrast Light/Dark + `prefers-contrast: more` modes, `aria-live="polite"` announcements, and `prefers-reduced-motion` support.

---

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# Optional: add your GEMINI_API_KEY in .env (app also runs cleanly in deterministic fallback mode)

uvicorn app.main:app --reload --port 8080
```

Open `http://127.0.0.1:8080` in your browser.

---

## Run Automated Tests

```bash
pytest
```

The test suite verifies catalog filtering, GenAI discovery & caching, Gemini mock integration, smart slot optimization, overlap conflict detection, project requests & faculty approval state transitions, fairness scoring math, atomic zero-overlap conflict resolution, security headers, XSS/SQLi defenses, rate limiting, utilization analytics, and frontend accessibility invariants.

---

## Deploy to Google Cloud Run

### Option A: From Google Cloud Shell (Fastest — No Local Docker/CLI Needed)

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

gcloud run deploy lablink \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>,GEMINI_MODEL=gemini-3.5-flash-lite,LABLINK_ENV=production"
```

### Option B: Directly in Google Cloud Console

1. Open **Cloud Run** in Google Cloud Console → **Deploy container** → **Service**.
2. Choose **Continuously deploy from a repository** and connect your public GitHub repository (`main` branch, Dockerfile auto-detected).
3. Under **Authentication**, choose **Allow public access**.
4. Under **Variables & Secrets**, add `GEMINI_API_KEY` and `GEMINI_MODEL=gemini-3.5-flash-lite`.
5. Click **Create** and verify `/health` and `/` on the generated `.run.app` URL.
