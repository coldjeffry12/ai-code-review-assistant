# Deployment Guide

This project is designed for:

- Backend API on Render
- Frontend app on Vercel

Do not hardcode API keys or live deployment URLs in source code. Configure deployment URLs with environment variables after each service is deployed.

Official references:

- Render FastAPI docs: https://render.com/docs/deploy-fastapi
- Render Blueprint YAML reference: https://render.com/docs/blueprint-spec
- Vercel Vite docs: https://vercel.com/docs/frameworks/frontend/vite
- Vercel environment variables docs: https://vercel.com/docs/projects/environment-variables

## 1. Deploy the Backend on Render

Repository:

```text
https://github.com/coldjeffry12/ai-code-review-assistant
```

Render service type:

```text
Web Service
```

Root directory:

```text
backend
```

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health check path:

```text
/health
```

Environment variables:

```text
PYTHON_VERSION=3.11.11
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
FRONTEND_ORIGINS=
```

Notes:

- `PYTHON_VERSION` pins Render to Python 3.11.11. This avoids Python 3.14 dependency build issues with packages such as `pydantic-core`.
- `OPENAI_API_KEY` is optional. Leave it empty if you want the backend to use fallback review mode.
- `OPENAI_BASE_URL` is optional and only needed for a compatible non-default AI provider.
- `FRONTEND_ORIGINS` should be updated after the Vercel frontend URL is available.
- Render provides `$PORT`; the backend start command must use it.

After deployment, test these URLs:

```text
https://your-render-backend-url.onrender.com/health
https://your-render-backend-url.onrender.com/docs
```

Expected health response:

```json
{"status":"ok"}
```

## 2. Deploy the Frontend on Vercel

Repository:

```text
https://github.com/coldjeffry12/ai-code-review-assistant
```

Vercel project root directory:

```text
frontend
```

Install command:

```text
npm install
```

Build command:

```text
npm run build
```

Output directory:

```text
dist
```

Environment variable:

```text
VITE_API_BASE_URL=https://your-render-backend-url.onrender.com
```

Important:

- Vite frontend environment variables must start with `VITE_`.
- Do not include a trailing slash in `VITE_API_BASE_URL`.
- Redeploy the frontend after setting or changing `VITE_API_BASE_URL`.

## 3. Update Backend CORS After Frontend Deployment

After Vercel gives you the frontend URL, update the Render backend environment variable:

```text
FRONTEND_ORIGINS=https://your-vercel-frontend-url.vercel.app
```

If you need to allow multiple frontend origins, use a comma-separated list:

```text
FRONTEND_ORIGINS=https://your-vercel-frontend-url.vercel.app,https://your-custom-domain.com
```

Then redeploy or restart the Render backend service.

## 4. Final Smoke Test

Open the Vercel frontend URL and run a review with this sample:

```python
def divide_numbers(a, b):
    return a / b

print(divide_numbers(10, 0))
```

Expected result:

- The frontend loads.
- Clicking **Review Code** calls the Render backend.
- The result shows risk score, summary, possible bugs, improvements, and test case ideas.
- If `OPENAI_API_KEY` is empty, the backend still returns a fallback review.

## 5. Update README and CV

After both services are live, update the root `README.md` with:

```text
Live frontend: https://your-vercel-frontend-url.vercel.app
Backend docs: https://your-render-backend-url.onrender.com/docs
```

Use the frontend URL as the live demo link in your CV and portfolio.
