# Project Journey: AI Code Review Assistant

This document explains the project from the original idea to the current deployed portfolio version. It is written for GitHub visitors, portfolio reviewers, and job applications.

## 1. Starting Point

The project started as a portfolio idea for an Applied AI Engineer role.

The goal was to build a full-stack AI Code Review Assistant where a user can paste code, select a programming language, and receive:

- A code summary
- Possible bugs
- Risk score
- Improvement suggestions
- Suggested test cases
- Optional fixed code

The project needed to prove more than a basic UI. It needed to show practical AI-native development, a GitHub-ready repository, testing evidence, deployment readiness, and a live demo.

The chosen stack was:

- Frontend: React, TypeScript, Vite
- Backend: Python, FastAPI, Pydantic
- AI integration: OpenAI-compatible chat API
- Fallback mode: local rule-based review logic when no API key is available
- Deployment: Render for the backend and Vercel for the frontend

## 2. Core Design Decision

The most important design decision was to keep the app useful even without a paid AI API key.

The backend supports two review paths:

1. AI review when an OpenAI-compatible API key and base URL are configured.
2. Fallback review when no key exists, when the AI provider is unavailable, or when the AI response cannot be parsed safely.

This made the project reliable for GitHub reviewers because they can still run and test the app without paying for an API.

## 3. First Backend Implementation

The backend was built with FastAPI.

Main backend responsibilities:

- Expose a health check route: `GET /health`
- Expose the review route: `POST /api/review`
- Validate request and response data with Pydantic schemas
- Read environment variables from `.env`
- Call an OpenAI-compatible API if configured
- Return fallback results if AI is unavailable
- Support CORS for local and deployed frontends

The review response shape includes:

```json
{
  "summary": "Review summary",
  "risk_score": 50,
  "bugs": [],
  "improvements": [],
  "test_cases": [],
  "fixed_code": null,
  "used_ai": false,
  "review_source": "fallback",
  "cache_hit": false,
  "similarity_used": null
}
```

## 4. Fallback Review Logic

The fallback engine was added so the app can run without any AI provider.

It detects simple but useful patterns, including:

- Hardcoded secrets
- Weak error handling
- Division by zero patterns
- Empty list or array access
- Unsafe dynamic execution such as `eval`
- Unsafe HTML injection in JavaScript, TypeScript, or React
- Missing null or undefined checks
- Null pointer risk in Java or C++
- Unsafe SQL string concatenation
- Non-numeric arithmetic input in common demo cases
- Discount range business rule issues

This fallback logic is intentionally simple. It is not meant to replace real AI review, but it keeps the live demo stable.

## 5. First Frontend Implementation

The frontend was built with React and TypeScript.

The main user workflow is:

1. Select a programming language.
2. Enter the review focus.
3. Paste code.
4. Click **Review Code**.
5. Read the risk score, summary, possible bugs, improvements, and test case ideas.

The frontend calls:

```text
POST /api/review
```

The API base URL is controlled by:

```text
VITE_API_BASE_URL
```

This keeps local development and deployment separate.

## 6. Local Setup Work

The project was prepared for beginner-friendly Windows setup.

Backend command:

```powershell
cd backend
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend command:

```powershell
cd frontend
npm install
npm run dev
```

If PowerShell blocks `npm.ps1`, the README documents this Windows-safe alternative:

```powershell
npm.cmd install
npm.cmd run dev
```

## 7. AI Response Hardening

The AI response could not be trusted blindly.

Problems that needed handling:

- AI might return markdown code fences.
- AI might return text before or after JSON.
- AI might return a risk score as a string.
- AI might omit fields.
- AI might return invalid JSON.

The backend was improved to:

- Extract JSON safely from AI text.
- Coerce risk scores into `0-100`.
- Normalize list-like fields.
- Normalize severity labels.
- Fall back cleanly if parsing fails.

This is important because AI output is untrusted data, even when the AI provider is working.

## 8. Risk Score Consistency

The review result was improved so the score matches the severity.

Current rules:

- Critical issue: risk score must be at least `70`
- High issue: risk score must be at least `50`
- Medium issue: risk score must be at least `30`
- Only Low issues: risk score stays around `10-25`

The frontend labels scores as:

- `0-25`: Low risk
- `26-50`: Medium risk
- `51-75`: High risk
- `76-100`: Critical risk

This fixed cases where the app could show a Critical issue but still display a low score.

## 9. Duplicate and Placeholder Cleanup

The backend also removes confusing fallback placeholders.

Example of a bad result:

```text
No critical issue detected by fallback engine
```

This should not appear as a bug card when the AI already found real bugs.

The cleanup layer now:

- Removes placeholder bug cards.
- Removes duplicate test case ideas.
- Removes duplicate improvement suggestions.
- Merges AI findings with fallback safety checks more cleanly.

## 10. Review Cache

Reviewing code can take time because the live app depends on a free backend and a free AI provider.

To improve the experience, an in-memory backend cache was added.

Cache behavior:

- If the same language, focus, and code are reviewed again, the backend returns the cached result immediately.
- If the code is similar but changed, the backend does not return the old result.
- For similar changed code, the backend sends the full current code to AI again and includes the previous similar review as context.
- The response includes metadata so the frontend can show whether a result came from cache.

Response metadata:

```json
{
  "review_source": "ai_with_cache_context",
  "cache_hit": false,
  "similarity_used": 0.94
}
```

Limitation:

The cache is in memory. On Render Free, it can reset when the service sleeps or restarts. This is acceptable because it keeps the project free.

## 11. UI Improvements

The UI was improved to look cleaner and more portfolio-ready.

Important UI features:

- Professional two-panel layout
- Language selector
- Review focus input
- Code editor textarea
- Clear review result sections
- Risk score card
- Severity badges
- Empty state
- Loading state
- Light and dark theme toggle

The light/dark theme:

- Saves the user preference in `localStorage`
- Uses the system theme on first load
- Covers panels, forms, risk cards, severity badges, code blocks, buttons, and empty states

## 12. Testing the Review Feature

Manual test cases were documented in:

```text
docs/test-cases.md
```

The documented cases cover:

- Python division by zero
- Python hardcoded password or API key
- Python list index error
- JavaScript `var` usage
- JavaScript empty array access
- TypeScript missing null or undefined check
- SQL unsafe string concatenation
- Java null pointer risk

Live language coverage was also tested for:

- Python
- JavaScript
- TypeScript
- React
- Java
- C++
- SQL

The app passed issue detection for all frontend language options during the documented test run.

## 13. Automated Tests

Pytest tests were added for the backend.

The tests cover:

- `/health` returns `{"status": "ok"}`
- `/api/review` works without `OPENAI_API_KEY`
- Hardcoded secrets are detected
- JavaScript `var` usage gets an improvement suggestion
- HTML strings do not accidentally trigger arithmetic suggestions
- Critical severity forces the correct risk score floor
- High severity forces the correct risk score floor
- Placeholder fallback messages are not shown as real bug cards
- Duplicate test cases are removed
- The discount example detects TypeError risk and business rule issues
- Exact repeated reviews use the cache
- Similar changed reviews use previous review context without returning stale results

Test command:

```powershell
cd backend
.\venv\Scripts\python.exe -m pytest
```

Latest local result:

```text
12 passed
```

## 14. Screenshots

Portfolio screenshots were saved in:

```text
screenshots/
```

Screenshots include:

- Homepage
- Python division by zero input
- Python division by zero review result
- Hardcoded secret review result
- JavaScript `var` review result
- Test cases document
- FastAPI API docs

These screenshots help prove the project works without requiring a reviewer to run everything locally.

## 15. GitHub Setup

The project was prepared for GitHub by checking:

- `README.md` exists and is beginner-friendly
- `backend/` exists
- `frontend/` exists
- `docs/` exists
- `screenshots/` exists
- `.gitignore` prevents `.env`, virtual environments, `node_modules`, and build output from being uploaded
- MIT License exists
- Repository topics and description are portfolio-friendly

The repository is public:

```text
https://github.com/coldjeffry12/ai-code-review-assistant
```

## 16. Backend Deployment on Render

The backend was deployed on Render.

Important Render settings:

- Service type: Web Service
- Root Directory: `backend`
- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`
- Plan: Free

Important environment variables:

```text
PYTHON_VERSION=3.11.11
OPENAI_MODEL=gemini-2.5-flash
OPENAI_API_KEY=<set in Render only>
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
FRONTEND_ORIGINS=https://ai-code-review-assistant-seven.vercel.app
```

The API key is not stored in GitHub.

Live backend:

```text
https://ai-code-review-backend-c17u.onrender.com
```

Health check:

```text
https://ai-code-review-backend-c17u.onrender.com/health
```

## 17. Render Issue and Fix

The first Render build failed because Render selected Python `3.14.3`.

That caused a dependency build failure around `pydantic-core`.

Fix:

- Added Python version configuration.
- Used Python `3.11.11`.
- Rebuilt the service.

After that, the backend build succeeded and `/health` returned:

```json
{"status":"ok"}
```

## 18. Frontend Deployment on Vercel

The frontend was deployed on Vercel.

Important Vercel settings:

- Root Directory: `frontend`
- Framework Preset: Vite
- Build Command: `npm run build`
- Output Directory: `dist`

Important environment variable:

```text
VITE_API_BASE_URL=https://ai-code-review-backend-c17u.onrender.com
```

Live frontend:

```text
https://ai-code-review-assistant-seven.vercel.app
```

## 19. Free AI Decision

The project was required to stay free.

The final approach:

- Use a free OpenAI-compatible provider when available.
- Keep `OPENAI_API_KEY` optional.
- Keep fallback mode always available.
- Do not require paid dependencies.
- Document that free AI can be slower, throttled, or temporarily unavailable.

This is why the app is described as AI-enabled with fallback review instead of claiming that every request is always AI-reviewed.

## 20. Live Behavior

Current live behavior:

1. User opens the Vercel frontend.
2. Frontend sends code to the Render backend.
3. Backend checks exact cache.
4. Backend checks similar cached reviews.
5. If AI is configured and available, backend calls the AI provider.
6. Backend merges AI output with local safety checks.
7. Backend normalizes severity, risk score, and duplicate fields.
8. If AI fails, backend returns fallback review.
9. Frontend displays the result clearly.

## 21. What Still Is Not Perfect

This project is portfolio-ready, but not perfect.

Known limitations:

- The app does not execute user code.
- Static review cannot guarantee every bug will be found.
- Free AI providers may be slow or inconsistent.
- Render Free can sleep after inactivity.
- The in-memory cache resets when the backend restarts.
- Fallback mode only detects simple patterns.
- Suggested fixed code must be tested manually.

These limitations are documented honestly because an AI code review tool should not pretend to be a complete replacement for human review and real tests.

## 22. What This Project Demonstrates

This project demonstrates:

- Full-stack application development
- React and TypeScript frontend work
- Python FastAPI backend development
- AI API integration
- Fallback design for reliability
- Defensive AI JSON parsing
- Risk score normalization
- CORS and deployment configuration
- Automated backend testing with pytest
- Manual test case documentation
- GitHub repository preparation
- Render and Vercel deployment
- Portfolio screenshots
- AI-assisted development workflow

## 23. Final Status

The project is ready for:

- GitHub portfolio review
- CV or resume linking
- Applied AI Engineer job applications
- Live demo sharing
- Further improvements, such as persistent database-backed caching or streaming AI responses

Live links:

- Frontend: https://ai-code-review-assistant-seven.vercel.app
- Backend docs: https://ai-code-review-backend-c17u.onrender.com/docs
- GitHub: https://github.com/coldjeffry12/ai-code-review-assistant

