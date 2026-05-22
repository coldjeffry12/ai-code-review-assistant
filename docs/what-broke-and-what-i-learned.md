# What Broke and What I Learned

This document is included for portfolio reviewers. It explains the engineering problems behind the finished demo.

## 1. AI Responses Were Not Always Clean JSON

Problem:

AI models sometimes return markdown code fences, extra explanation text, missing fields, or values in the wrong type.

Fix:

The backend now extracts JSON more defensively, normalizes risk scores, converts list-like fields safely, and falls back to local review rules if the AI response is unusable.

Lesson:

AI output should be treated as untrusted data. It needs validation before the frontend depends on it.

## 2. The Demo Could Not Depend Only on an API Key

Problem:

A portfolio reviewer may run the app without an OpenAI API key, or the deployed service may have no quota.

Fix:

The fallback review engine remains part of the backend. It detects simple risks such as hardcoded secrets, weak error handling, unsafe dynamic execution, possible SQL injection, unsafe HTML injection, and division-by-zero patterns.

Lesson:

A reliable demo should still show value when third-party services are unavailable.

## 3. Frontend and Backend Needed Clear CORS Settings

Problem:

The frontend runs on port `5173`, while the backend runs on port `8000`. Browsers block cross-origin requests unless the backend allows the frontend origin.

Fix:

FastAPI CORS is configured for local development and can be extended with `FRONTEND_ORIGINS` for deployed frontend URLs.

Lesson:

Deployment needs configuration, not hardcoded localhost-only assumptions.

## 4. The Frontend Build Needed TypeScript Configuration

Problem:

The build script used `tsc`, but the project did not have a `tsconfig.json`, so the build did not compile the app.

Fix:

A TypeScript config and Vite environment type file were added.

Lesson:

It is not enough for a Vite app to run in dev mode. A portfolio project should also pass a production build.

## 5. Windows Setup Needed Extra Care

Problem:

On some Windows machines, PowerShell blocks `npm.ps1` because script execution is disabled.

Fix:

The README includes `npm.cmd install` and `npm.cmd run dev` as a Windows fallback.

Lesson:

Beginner-friendly documentation should include common platform-specific issues.
