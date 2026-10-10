import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import router
from .config import settings
from .seed import ensure_seeded

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_seeded()  # creates tables, inserts demo data if missing; tolerates an unreachable DB
    yield


app = FastAPI(title="Closure Call", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


# Serve the built frontend (production). In dev, Vite serves it and proxies /api here.
dist = settings.frontend_dist.resolve()


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "Not found")
    index = dist / "index.html"
    if not index.exists():
        return {"message": "Closure Call API. Frontend not built; run `npm run dev` in /frontend or build it."}
    file = (dist / path).resolve()
    if path and file.is_file() and file.is_relative_to(dist):
        return FileResponse(file)
    return FileResponse(index)
