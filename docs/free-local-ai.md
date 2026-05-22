# Free Local AI Mode

The deployed demo is intentionally free. It uses the backend fallback review engine when `OPENAI_API_KEY` is empty.

If you want real AI review without paying for an API, run a local open-source model with Ollama on your own computer. Ollama provides an OpenAI-compatible API endpoint, so this project can use it through the existing `OPENAI_BASE_URL` setting.

Important:

- Local AI mode is free to run, but it uses your computer CPU/GPU.
- The Vercel frontend and Render backend cannot use your laptop's local Ollama server.
- For the hosted live demo, keep `OPENAI_API_KEY` empty and use fallback mode.
- For a local portfolio demo, run the backend and frontend on your computer with Ollama enabled.

## 1. Install Ollama

Install Ollama from:

```text
https://ollama.com
```

## 2. Download a Free Coding Model

Recommended model:

```powershell
ollama pull qwen2.5-coder:7b
```

If your computer is slow or has limited memory, use the smaller model:

```powershell
ollama pull qwen2.5-coder:1.5b
```

## 3. Start the Backend With Local AI

Create or update `backend/.env`:

```text
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen2.5-coder:7b
OPENAI_BASE_URL=http://localhost:11434/v1
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

For the smaller model, use:

```text
OPENAI_MODEL=qwen2.5-coder:1.5b
```

Then run:

```powershell
cd backend
venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

## 4. Start the Frontend

Create or update `frontend/.env`:

```text
VITE_API_BASE_URL=http://localhost:8000
```

Then run:

```powershell
cd frontend
npm run dev
```

Open:

```text
http://localhost:5173
```

## 5. Confirm AI Mode Is Working

Paste code into the app and click **Review Code**.

When Ollama is working, the backend response includes:

```text
used_ai: true
```

If Ollama is not running or the model output cannot be parsed as JSON, the backend will safely fall back to rule-based review.

## 6. Portfolio Wording

Use this wording for the project:

```text
The live demo runs on free fallback review logic. The backend also supports free local AI review through Ollama or OpenAI-compatible APIs when configured locally.
```

Do not claim that the hosted live demo uses OpenAI unless a real API key is configured.
