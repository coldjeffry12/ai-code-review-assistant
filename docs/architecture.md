# Architecture

## Overview

The AI Code Review Assistant is split into a React frontend and a FastAPI backend.

The frontend owns the user experience: code input, language selection, loading state, error display, and rendering the review result. The backend owns validation, AI provider calls, fallback rules, and response formatting.

## Request Flow

```text
User
  |
  v
React + TypeScript frontend
  |
  | POST /api/review
  v
FastAPI backend
  |
  |-- OPENAI_API_KEY exists --> OpenAI-compatible review
  |
  `-- no key or AI error ----> local fallback review
  |
  v
Structured JSON response
  |
  v
Frontend result display
```

## Backend Responsibilities

- Validate request data with Pydantic.
- Configure CORS for local and deployed frontend URLs.
- Build a clear code-review prompt.
- Call an OpenAI-compatible API when a key is configured.
- Parse AI output safely enough for a demo app.
- Fall back to local review rules when no key exists or the AI response fails.
- Return one consistent response shape to the frontend.

## Frontend Responsibilities

- Let the user choose a language and review focus.
- Send code to the backend API.
- Handle loading, validation errors, and backend errors.
- Display risk score, summary, bug findings, improvements, test ideas, and fixed code.

## Why the Project Is Separated

Keeping frontend and backend separate makes the project easier to deploy:

- Vercel or Netlify can host the frontend.
- Render, Railway, Fly.io, or Docker can host the backend.
- API keys stay on the backend and are never exposed to browser code.

## Reliability Choices

- The fallback engine keeps the demo usable without a paid API key.
- AI output is parsed as JSON and normalized before returning to the frontend.
- CORS origins are configurable with `FRONTEND_ORIGINS`.
- The frontend reads the backend URL from `VITE_API_BASE_URL`.
