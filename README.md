# app-matching-minimal

Single-file, minimal-dependency implementation of the app-matching API spec:
admin/root-protected control panel + session-based submissions, jsonl/JSON
file storage, brute-force protection. No Pydantic models, no settings
library, no rate-limiting library — just FastAPI and the standard library.

See [../app_matching](../app_matching) for the same spec built as a proper
multi-module package (typed config, Pydantic validation, `slowapi`, tests,
Docker) — this repo exists to compare the two.

## Run

```bash
uv sync
export ADMIN_PASSWORD=change-me-admin ROOT_PASSWORD=change-me-root
uv run uvicorn minimal_app:app --reload
```

Both passwords are required: the app refuses to start without them.

Frontend (separate static page, talks to the API at `localhost:8000`):

```bash
python3 -m http.server 5173 -d frontend
# open http://localhost:5173
```

It checks `/server/status`, shows nothing while the panel is inactive, otherwise
calls `/session/start` and shows one slider per label. The values sent are
the slider weights scaled so they add up to 1. Submitting asks for confirmation first.

## Try it with curl

```bash
curl -X POST localhost:8000/server/activate -H "X-Root-Password: change-me-root"

curl localhost:8000/server/status
# => {"active": true}

curl -X POST localhost:8000/session/start
# => {"session_id": "...", "values": [5 random numbers summing to 1], "labels": ["AI", ...]}

curl -X POST localhost:8000/session/submit \
  -H "X-Session-Id: <session_id>" -H "Content-Type: application/json" \
  -d '{"values":[0.1,0.2,0.3,0.15,0.25],"labels":["AI","GameTheory","Maths","Stats","History"]}'

curl localhost:8000/server/statistics -H "X-Admin-Password: change-me-admin"
curl localhost:8000/server/export -H "X-Admin-Password: change-me-admin"
curl -X POST localhost:8000/server/reset -H "X-Admin-Password: change-me-admin"
```

## Routes

| Route | Method | Auth |
|---|---|---|
| `/server/status` | GET | none |
| `/server/activate` | POST | `X-Root-Password` |
| `/server/deactivate` | POST | `X-Root-Password` |
| `/server/reset` | POST | `X-Admin-Password` |
| `/server/statistics` | GET | `X-Admin-Password` |
| `/server/export` | GET | `X-Admin-Password` |
| `/session/start` | POST | none (403 if the panel is inactive) |
| `/session/submit` | POST | valid, unused `X-Session-Id` |

Submission rules: `values` is 5 numbers in [0, 1] that sum to 1; `labels` is
each of AI, GameTheory, Maths, Stats, History exactly once.

Data lives under `data/` (gitignored): `data.jsonl` (submissions),
`sessions.json`, `state.json`, `archive/<timestamp>/` (written by reset).
