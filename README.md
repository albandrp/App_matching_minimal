# app-matching-minimal

Single-file, minimal-dependency implementation of the app-matching API spec:
admin/root-protected control panel + session-based submissions, jsonl/JSON
file storage, brute-force protection. The submission payload is validated
with a Pydantic model (bundled with FastAPI); no settings library, no
rate-limiting library — just FastAPI and the standard library.

See [App_matching](https://github.com/albandrp/App_matching) for the same spec built as a proper
multi-module package (typed config, Pydantic validation, `slowapi`, tests,
Docker).

## Run

```bash
uv sync
export ADMIN_PASSWORD=change-me-admin ROOT_PASSWORD=change-me-root
uv run uvicorn minimal_app:app --reload
```

Both passwords are required: the app refuses to start without them.

Frontend (separate static pages, talk to the API at `localhost:8000`):

```bash
python3 -m http.server 5173 -d frontend
# participants: http://localhost:5173
# admin:        http://localhost:5173/admin.html
```

**Participant page** (built for phones: large buttons). It calls
`/session/start` directly - a 403 means the panel is inactive (there's no
dedicated status route, since the spec names exactly 7 routes and that isn't
one of them). Otherwise it shows the session's (label, score) items, sorted
by score. The user can only reorder them with ↑/↓; the scores never change.
Submitting asks for confirmation first. The footer shows the session id and
the app version.

**Admin page.** Type the admin password and press Refresh: it calls
`/server/statistics` and shows the panel state, the counts, per-label stats
and the raw payload. The password stays in the input field, never in the URL.
Admin routes allow 5 requests a minute, so refreshing faster gets a 429.

The app version comes from `pyproject.toml`. The API sends it in an
`X-App-Version` header on every response (errors included), and both pages
show it in their footer.

## Try it with curl

```bash
# no status route: session/start itself is how you find out (403 while inactive)
curl -X POST localhost:8000/session/start

curl -X POST localhost:8000/server/activate -H "X-Root-Password: change-me-root"

curl -X POST localhost:8000/session/start
# => {"session_id": "...", "items": [{"label": "Maths", "score": 0.31}, ...]}
#    one random score per label, summing to 1, sorted by score

# send the same items back, in your order (most preferred first)
curl -X POST localhost:8000/session/submit -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "items": [<the items above, reordered>]}'

curl localhost:8000/server/statistics -H "X-Admin-Password: change-me-admin"
curl localhost:8000/server/export -H "X-Admin-Password: change-me-admin"
curl -X POST localhost:8000/server/reset -H "X-Admin-Password: change-me-admin"
```

## Routes

| Route | Method | Auth |
|---|---|---|
| `/server/activate` | POST | `X-Root-Password` |
| `/server/deactivate` | POST | `X-Root-Password` |
| `/server/reset` | POST | `X-Admin-Password` |
| `/server/statistics` | GET | `X-Admin-Password` |
| `/server/export` | GET | `X-Admin-Password` |
| `/session/start` | POST | none (403 if the panel is inactive) |
| `/session/submit` | POST | valid, unused `session_id` in the body |

Submission rules (Pydantic models `Item` and `Submission`): the body is
`{"session_id": ..., "items": [{"label": ..., "score": ...}, ...]}`. The items
must be exactly the ones the session was given (same labels, same scores),
only reordered. The position in the list is the preference order, first =
most preferred.

`/server/statistics` reports, per label, the average rank (1 = most
preferred) and how often it was ranked first, plus how many users submitted
the initial order (sorted by score) unchanged.

Data lives under `data/` (gitignored): `data.jsonl` (submissions),
`sessions.json`, `state.json`, `archive/<timestamp>/` (written by reset).
