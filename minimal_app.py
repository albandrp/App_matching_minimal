from __future__ import annotations

import json
import math
import os
import random
import secrets
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
ROOT_PASSWORD = os.environ.get("ROOT_PASSWORD")
if not ADMIN_PASSWORD or not ROOT_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD and ROOT_PASSWORD must be set")
LABELS = ["AI", "GameTheory", "Maths", "Stats", "History"]

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_FILE = DATA_DIR / "data.jsonl"
SESSIONS_FILE = DATA_DIR / "sessions.json"
STATE_FILE = DATA_DIR / "state.json"
ARCHIVE_DIR = DATA_DIR / "archive"

DATA_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE.touch(exist_ok=True)
if not SESSIONS_FILE.exists():
    SESSIONS_FILE.write_text("{}")
if not STATE_FILE.exists():
    STATE_FILE.write_text(json.dumps({"active": False}))

app = FastAPI(title="app-matching (minimal)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, payload) -> None:
    # write to a temp file then swap, so a crash never leaves a half-written file
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


# sync endpoints run in a thread pool: serialize every read-modify-write on the data files
_lock = threading.Lock()


def is_active() -> bool:
    return read_json(STATE_FILE)["active"]


# brute-force protection: N requests per IP per rolling window 

RATE_LIMITS = {"admin": (5, 60), "session": (10, 60)}  # (max hits, window seconds)
_hits: dict[tuple[str, str], list[float]] = defaultdict(list)


def rate_limit(bucket: str):
    def check(request: Request) -> None:
        limit, window = RATE_LIMITS[bucket]
        key = (bucket, request.client.host if request.client else "unknown")
        now = time.monotonic()
        hits = [t for t in _hits[key] if now - t < window]
        if len(hits) >= limit:
            raise HTTPException(429, "Too many requests")
        hits.append(now)
        _hits[key] = hits

    return check


# auth

def require_admin(x_admin_password: str = Header(...)) -> None:
    if not secrets.compare_digest(x_admin_password, ADMIN_PASSWORD):
        raise HTTPException(401, "Invalid admin password")


def require_root(x_root_password: str = Header(...)) -> None:
    if not secrets.compare_digest(x_root_password, ROOT_PASSWORD):
        raise HTTPException(401, "Invalid root password")


# server

@app.get("/server/status")
def status() -> dict:
    return {"active": is_active()}


@app.post("/server/activate", dependencies=[Depends(rate_limit("admin")), Depends(require_root)])
def activate() -> dict:
    write_json(STATE_FILE, {"active": True})
    return {"active": True}


@app.post("/server/deactivate", dependencies=[Depends(rate_limit("admin")), Depends(require_root)])
def deactivate() -> dict:
    write_json(STATE_FILE, {"active": False})
    return {"active": False}


@app.post("/server/reset", dependencies=[Depends(rate_limit("admin")), Depends(require_admin)])
def reset() -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_dir = ARCHIVE_DIR / stamp
    batch_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        DATA_FILE.rename(batch_dir / "data.jsonl")
        SESSIONS_FILE.rename(batch_dir / "sessions.json")
        DATA_FILE.touch()
        write_json(SESSIONS_FILE, {})
    return {"archived_to": str(batch_dir)}


@app.get("/server/statistics", dependencies=[Depends(rate_limit("admin")), Depends(require_admin)])
def statistics() -> dict:
    submissions = [json.loads(line) for line in DATA_FILE.read_text().splitlines() if line.strip()]
    sessions = read_json(SESSIONS_FILE)
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for record in submissions:
        for label, value in zip(record["labels"], record["values"]):
            sums[label] = sums.get(label, 0.0) + value
            counts[label] = counts.get(label, 0) + 1
    return {
        "active": is_active(),
        "total_submissions": len(submissions),
        "total_sessions": len(sessions),
        "used_sessions": sum(1 for s in sessions.values() if s["used"]),
        "average_value_by_label": {label: sums[label] / counts[label] for label in sums},
    }


@app.get("/server/export", dependencies=[Depends(rate_limit("admin")), Depends(require_admin)])
def export() -> FileResponse:
    return FileResponse(DATA_FILE, media_type="application/x-ndjson", filename="submissions.jsonl")


# session

@app.post("/session/start", dependencies=[Depends(rate_limit("session"))])
def start() -> dict:
    if not is_active():
        raise HTTPException(403, "Server is not active")
    session_id = uuid.uuid4().hex
    # session content: 5 random values summing to 1, one per label
    draws = [random.random() for _ in LABELS]
    values = [d / sum(draws) for d in draws]
    with _lock:
        sessions = read_json(SESSIONS_FILE)
        sessions[session_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used": False,
            "values": values,
            "labels": LABELS,
        }
        write_json(SESSIONS_FILE, sessions)
    return {"session_id": session_id, "values": values, "labels": LABELS}


@app.post("/session/submit", dependencies=[Depends(rate_limit("session"))])
def submit(payload: dict, x_session_id: str = Header(...)) -> dict:
    if not is_active():
        raise HTTPException(403, "Server is not active")

    values = payload.get("values")
    labels = payload.get("labels")
    if not (
        isinstance(values, list)
        and len(values) == 5
        and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in values)
    ):
        raise HTTPException(422, "values must be 5 numbers between 0 and 1")
    if not math.isclose(sum(values), 1.0, abs_tol=1e-6):
        raise HTTPException(422, f"values must sum to 1 (got {sum(values)})")
    if not (isinstance(labels, list) and sorted(labels) == sorted(LABELS)):
        raise HTTPException(422, f"labels must be each of {LABELS} exactly once")

    record = {
        "id": uuid.uuid4().hex,
        "session_id": x_session_id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "values": values,
        "labels": labels,
    }
    with _lock:
        sessions = read_json(SESSIONS_FILE)
        session = sessions.get(x_session_id)
        if session is None:
            raise HTTPException(401, "Unknown session")
        if session["used"]:
            raise HTTPException(409, "Session already submitted")
        with DATA_FILE.open("a") as f:
            f.write(json.dumps(record) + "\n")
        session["used"] = True
        write_json(SESSIONS_FILE, sessions)
    return record

# travailler avec pydantic pour valider les payloads
