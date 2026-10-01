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

Frontend (separate static pages; they find the API through
[frontend/config.js](frontend/config.js), set to `localhost:8000`):

```bash
python3 -m http.server 5173 -d frontend
# participants: http://localhost:5173
# result:       http://localhost:5173/result.html
# admin:        http://localhost:5173/admin.html
```

**Labels.** [labels.json](labels.json) lists `[label, % of available seats]`
pairs; the seats must add up to 100. The API reads it at startup (another
file can be given with `LABELS_FILE`).

**Participant page** (built for phones: large buttons). It calls
`/session/start` directly - a 403 means the panel is inactive (there's no
dedicated status route). Otherwise it shows "You have N points" (one random
number between 1 and 1000 per user) and the labels with their "% available
seats", sorted by ascending seats. The user can only reorder them with ↑/↓.
Submitting asks for confirmation first, then shows the session id with a Copy
button and a link to the result page: participants need that id to see their
course. The footer shows the session id and the app version.

**Result page.** The participant types their session id (pre-filled when
coming from the link) and gets the course they are assigned, once the admin
has uploaded the assignments. Works even after the panel is closed.

**Admin page.** Type the admin password, then:

- **Refresh** shows the panel state, the counts, per-label stats and the raw
  statistics payload;
- **Download user entries** downloads `submissions.jsonl`;
- **Upload assignments** sends a CSV with two columns, session id and
  assigned course (header `session_id,course` optional). The whole file is
  refused, with the list of problems, if a session id is unknown or repeated
  or a course isn't a label. A new upload replaces the previous one.
  Upload it **before** any reset: a reset archives the sessions, so their
  ids would then be refused as unknown.

The password stays in the input field, never in the URL. Admin routes allow
10 requests a minute, so going faster gets a 429.

The app version comes from `pyproject.toml`. The API sends it in an
`X-App-Version` header on every response (errors included), and the pages
show it in their footer.

## Try it with curl

```bash
# no status route: session/start itself is how you find out (403 while inactive)
curl -X POST localhost:8000/session/start

curl -X POST localhost:8000/server/activate -H "X-Root-Password: change-me-root"

curl -X POST localhost:8000/session/start
# => {"session_id": "...", "points": 547,
#     "items": [{"label": "AI", "seats": 8}, {"label": "GameTheory", "seats": 23}, ...]}

# send the same items back, in your order (most preferred first)
curl -X POST localhost:8000/session/submit -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "items": [<the items above, reordered>]}'

curl localhost:8000/server/statistics -H "X-Admin-Password: change-me-admin"
curl localhost:8000/server/export -H "X-Admin-Password: change-me-admin"

# assignments: CSV "session_id,course", then each participant looks theirs up
curl -X POST localhost:8000/server/assignments -H "X-Admin-Password: change-me-admin" \
  -H "Content-Type: text/csv" --data-binary @assignments.csv
curl localhost:8000/session/assignment/<session_id>
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
| `/server/assignments` | POST | `X-Admin-Password` (body: the CSV file) |
| `/session/start` | POST | none (403 if the panel is inactive) |
| `/session/submit` | POST | valid, unused `session_id` in the body |
| `/session/assignment/{session_id}` | GET | none (the session id itself) |

Submission rules (Pydantic models `Item` and `Submission`): the body is
`{"session_id": ..., "items": [{"label": ..., "seats": ...}, ...]}`. The items
must be exactly the labels and seats from `labels.json`, only reordered. The
position in the list is the preference order, first = most preferred. The
stored record adds the session's `points`, taken from the server.

`/server/statistics` reports, per label, the average rank (1 = most
preferred) and how often it was ranked first, how many users submitted the
starting order unchanged, and whether assignments were uploaded.

Data lives under `data/` (gitignored): `data.jsonl` (submissions),
`sessions.json`, `state.json`, `assignments.json`, and
`archive/<timestamp>/` (written by reset, which also archives the
assignments).
