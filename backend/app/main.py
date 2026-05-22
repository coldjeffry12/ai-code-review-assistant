from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

from app.routers.review import router as review_router

load_dotenv()


def _allowed_origins() -> list[str]:
    configured = os.getenv("FRONTEND_ORIGINS") or os.getenv("FRONTEND_ORIGIN", "")
    defaults = ["http://localhost:5173", "http://127.0.0.1:5173"]
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return list(dict.fromkeys(origins + defaults))


app = FastAPI(
    title="AI Code Review Assistant API",
    description="Backend API for reviewing code, finding bugs, and suggesting improvements.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review_router, prefix="/api", tags=["review"])


@app.get("/")
def root():
    return {
        "message": "AI Code Review Assistant API is running",
        "docs": "/docs",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}
