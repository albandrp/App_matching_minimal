from __future__ import annotations

import csv
import io
import json
import os
import random
import secrets
import threading
import time
import tomllib
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
ROOT_PASSWORD = os.environ.get("ROOT_PASSWORD")
if not ADMIN_PASSWORD or not ROOT_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD and ROOT_PASSWORD must be set")
# single source of truth for the version: pyproject.toml
APP_VERSION = tomllib.loads(Path(__file__).with_name("pyproject.toml").read_text())["project"]["version"]


class Item(BaseModel):
    label: str
    seats: Annotated[int, Field(ge=0, le=100)]  # % of available seats


# labels config: a list of [label, % of available seats] pairs
LABELS_FILE = Path(os.environ.get("LABELS_FILE", Path(__file__).with_name("labels.json")))
ITEMS = [Item(label=label, seats=seats) for label, seats in json.loads(LABELS_FILE.read_text())]
LABELS = [item.label for item in ITEMS]
SEATS = {item.label: item.seats for item in ITEMS}
if len(set(LABELS)) != len(LABELS) or sum(SEATS.values()) != 100:
    raise RuntimeError(f"{LABELS_FILE}: labels must be unique and seats must sum to 100")
# every session starts with the labels by ascending seats (ties keep the config order)
INITIAL_ORDER = [item.label for item in sorted(ITEMS, key=lambda item: item.seats)]
POINTS_RANGE = (1, 1000)  # one random number of points per user


class Submission(BaseModel):
    session_id: str
    # the labels with their seats, in the user's order (position 0 = most preferred);
    # the user only reorders them, so the seats must match the config
    items: list[Item]

    @field_validator("items")
    @classmethod
    def items_are_each_label_once(cls, items: list[Item]) -> list[Item]:
        if sorted(item.label for item in items) != sorted(LABELS):
            raise ValueError(f"items must contain each of {LABELS} exactly once")
        return items


DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_FILE = DATA_DIR / "data.jsonl"
SESSIONS_FILE = DATA_DIR / "sessions.json"
STATE_FILE = DATA_DIR / "state.json"
ASSIGNMENTS_FILE = DATA_DIR / "assignments.json"  # {session_id: course}, uploaded by the admin
ARCHIVE_DIR = DATA_DIR / "archive"

DATA_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE.touch(exist_ok=True)
if not SESSIONS_FILE.exists():
    SESSIONS_FILE.write_text("{}")
if not STATE_FILE.exists():
    STATE_FILE.write_text(json.dumps({"active": False}))

app = FastAPI(title="app-matching (minimal)", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-App-Version"],
)


# every response carries the version, even errors (e.g. the 403 while the panel is inactive)
@app.middleware("http")
async def add_version_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-App-Version"] = APP_VERSION
    return response


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

# session limit is per IP, and many participants can share one (classroom, NAT)
RATE_LIMITS = {"admin": (10, 60), "session": (100, 60)}  # (max hits, window seconds)
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


def require_active() -> None:
    # a dependency, so it runs before body validation: inactive always means 403
    if not is_active():
        raise HTTPException(403, "Server is not active")


# server
#
# No /server/status route: it isn't in the spec. The frontend finds out
# whether the panel is active by calling /session/start directly and
# treating a 403 as "inactive".

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
        if ASSIGNMENTS_FILE.exists():
            ASSIGNMENTS_FILE.rename(batch_dir / "assignments.json")
        DATA_FILE.touch()
        write_json(SESSIONS_FILE, {})
    return {"archived_to": str(batch_dir)}


@app.get("/server/statistics", dependencies=[Depends(rate_limit("admin")), Depends(require_admin)])
def statistics() -> dict:
    submissions = [json.loads(line) for line in DATA_FILE.read_text().splitlines() if line.strip()]
    sessions = read_json(SESSIONS_FILE)
    orders = [[item["label"] for item in r["items"]] for r in submissions]
    n = len(submissions) or 1  # avoid dividing by zero before the first submission
    return {
        "version": APP_VERSION,
        "active": is_active(),
        "total_submissions": len(submissions),
        "total_sessions": len(sessions),
        "used_sessions": sum(1 for s in sessions.values() if s["used"]),
        # rank 1 = most preferred
        "average_rank_by_label": {
            label: sum(order.index(label) + 1 for order in orders) / n for label in LABELS
        },
        "ranked_first_count": {
            label: sum(1 for order in orders if order[0] == label) for label in LABELS
        },
        # how many users submitted the starting order (ascending seats) unchanged
        "kept_initial_order": sum(1 for order in orders if order == INITIAL_ORDER),
        "assignments_uploaded": ASSIGNMENTS_FILE.exists(),
    }


@app.get("/server/export", dependencies=[Depends(rate_limit("admin")), Depends(require_admin)])
def export() -> FileResponse:
    return FileResponse(DATA_FILE, media_type="application/x-ndjson", filename="submissions.jsonl")


@app.post("/server/assignments", dependencies=[Depends(rate_limit("admin")), Depends(require_admin)])
async def upload_assignments(request: Request) -> dict:
    # the body is the CSV file itself: "session_id,course" rows, header optional
    try:
        text = (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "the file must be UTF-8 text")
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if rows and [cell.strip().lower() for cell in rows[0]] == ["session_id", "course"]:
        rows = rows[1:]
    if not rows:
        raise HTTPException(422, "the file has no assignment rows")

    sessions = read_json(SESSIONS_FILE)
    assignments: dict[str, str] = {}
    errors = []
    for line, row in enumerate(rows, start=1):
        if len(row) != 2:
            errors.append(f"row {line}: expected 2 columns (session_id, course), got {len(row)}")
            continue
        session_id, course = row[0].strip(), row[1].strip()
        if session_id not in sessions:
            errors.append(f"row {line}: unknown session id {session_id!r}")
        elif session_id in assignments:
            errors.append(f"row {line}: session id {session_id!r} appears twice")
        if course not in LABELS:
            errors.append(f"row {line}: unknown course {course!r} (expected one of {LABELS})")
        assignments[session_id] = course
    if errors:
        raise HTTPException(422, errors[:20])  # the whole file is refused

    with _lock:
        write_json(ASSIGNMENTS_FILE, assignments)
    return {"assigned": len(assignments)}


# session

@app.post("/session/start", dependencies=[Depends(rate_limit("session")), Depends(require_active)])
def start() -> dict:
    session_id = uuid.uuid4().hex
    points = random.randint(*POINTS_RANGE)
    with _lock:
        sessions = read_json(SESSIONS_FILE)
        sessions[session_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used": False,
            "points": points,
        }
        write_json(SESSIONS_FILE, sessions)
    items = [{"label": label, "seats": SEATS[label]} for label in INITIAL_ORDER]
    return {"session_id": session_id, "points": points, "items": items}


@app.post("/session/submit", dependencies=[Depends(rate_limit("session")), Depends(require_active)])
def submit(payload: Submission) -> dict:
    if {item.label: item.seats for item in payload.items} != SEATS:
        raise HTTPException(422, "items must be the configured labels and seats, only reordered")
    with _lock:
        sessions = read_json(SESSIONS_FILE)
        session = sessions.get(payload.session_id)
        if session is None:
            raise HTTPException(401, "Unknown session")
        if session["used"]:
            raise HTTPException(409, "Session already submitted")
        record = {
            "id": uuid.uuid4().hex,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "session_id": payload.session_id,
            "points": session["points"],  # from the server, never from the client
            "items": [item.model_dump() for item in payload.items],
        }
        with DATA_FILE.open("a") as f:
            f.write(json.dumps(record) + "\n")
        session["used"] = True
        write_json(SESSIONS_FILE, sessions)
    return record


# public, so participants can look up their result once the admin has uploaded
# the assignments (even after the panel is closed); session ids are unguessable
@app.get("/session/assignment/{session_id}", dependencies=[Depends(rate_limit("session"))])
def assignment(session_id: str) -> dict:
    if not ASSIGNMENTS_FILE.exists():
        raise HTTPException(404, "Results are not available yet")
    course = read_json(ASSIGNMENTS_FILE).get(session_id)
    if course is None:
        raise HTTPException(404, "No course is assigned to this session id")
    return {"session_id": session_id, "course": course}
