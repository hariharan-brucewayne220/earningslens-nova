from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

load_dotenv()

from backend.api.sessions import router as sessions_router
from backend.api.filing import router as filing_router
from backend.api.embedding import router as embedding_router
from backend.api.briefing import router as briefing_router
from backend.api.stream import router as stream_router
from backend.api.reports import router as reports_router

app = FastAPI(title="EarningsLens API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_router, prefix="/api")
app.include_router(filing_router, prefix="/api")
app.include_router(embedding_router, prefix="/api")
app.include_router(briefing_router, prefix="/api")
app.include_router(stream_router, prefix="/api")
app.include_router(reports_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "EarningsLens"}
