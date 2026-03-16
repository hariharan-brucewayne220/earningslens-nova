from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

from backend.api.sessions import router as sessions_router
from backend.api.filing import router as filing_router
from backend.api.embedding import router as embedding_router
from backend.api.briefing import router as briefing_router
from backend.api.stream import router as stream_router
from backend.api.reports import router as reports_router
from backend.api.sonic_demo import router as sonic_demo_router

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
app.include_router(sonic_demo_router, prefix="/api")


DATA_DIR = Path(__file__).resolve().parent.parent / "data"

@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audio file not found")
    suffix = path.suffix.lower()
    media_type = "audio/wav" if suffix == ".wav" else "audio/mpeg"
    return FileResponse(str(path), media_type=media_type, headers={"Accept-Ranges": "bytes"})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "EarningsLens"}
