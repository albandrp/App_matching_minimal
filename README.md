# app-matching-minimal

Single-file, minimal-dependency implementation of the app-matching API spec:
admin-password-protected control panel + session-based submissions, jsonl/JSON
file storage, brute-force protection. The submission payload is validated
with a Pydantic model (bundled with FastAPI); no settings library, no
rate-limiting library — just FastAPI and the standard library.

See [App_matching](https://github.com/albandrp/App_matching) for the same spec built as a proper
multi-module package (typed config, Pydantic validation, `slowapi`, tests,
Docker).

## Run

```bash
uv sync
export ADMIN_PASSWORD=change-me-admin
uv run uvicorn minimal_app:app --reload
```

The admin password is required: the app refuses to start without it.

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
dedicated status route). Otherwise it shows each label with "23% available
seats, you have N points": the points are a random number between 1 and
1000, drawn per user and per label. Labels are sorted by ascending seats.
The user can only reorder them with ↑/↓.
Submitting asks for confirmation first, then shows the session id with a Copy
button and a link to the result page: participants need that id to see their
matches. The footer shows the session id and the app version.
Session ids are 8 characters, capital letters and digits without look-alikes
(no 0/O, 1/I/L), e.g. `K7QXM3PA`; lowercase is accepted when looking up a result.

**Result page.** The participant types their session id (pre-filled when
coming from the link) and gets the course they are matched to under each of
the two algorithms ("Match under DA", "Match under IA"), once the admin has
uploaded the assignments. It also shows the ranking they submitted, with each
label's seats and points. Works even after the panel is closed.

**Admin page.** Type the admin password, then:

- **Open / Close the participant page** (one button at the top, its label
  follows the current state). Closing it doesn't affect the result page;
- **Refresh** shows the panel state, the counts, per-label stats and the raw
  statistics payload;
- **Download user entries** downloads `submissions.csv`: one row per
  submission, `session_id, submitted_at, rank_1 … rank_5` (`rank_1` = most
  preferred label), then one `<label> points` column per label;
- **Upload assignments** sends a CSV with three columns: session id, course
  matched under DA, course matched under IA (header `session_id,da,ia`
  optional). The whole file is refused, with the list of problems, if a
  session id is unknown or repeated or a course isn't a label. A new upload
  replaces the previous one.
  Upload it **before** any reset: a reset archives the sessions, so their
  ids would then be refused as unknown.
- **Reset all answers** (after a confirmation) empties the answers, sessions
  and assignments. Nothing is deleted: they are moved to
  `data/archive/<timestamp>/` on the server. To delete them for good, remove
  that folder by hand.

The password stays in the input field, never in the URL. Admin routes allow
10 requests a minute, so going faster gets a 429.

The app version comes from `pyproject.toml`. The API sends it in an
`X-App-Version` header on every response (errors included), and the pages
show it in their footer.

## Try it with curl

```bash
# no status route: session/start itself is how you find out (403 while inactive)
curl -X POST localhost:8000/session/start

curl -X POST localhost:8000/server/activate -H "X-Admin-Password: change-me-admin"

curl -X POST localhost:8000/session/start
# => {"session_id": "...",
#     "items": [{"label": "Artificial intelligence", "seats": 8, "points": 547},
#               {"label": "Cryptography", "seats": 23, "points": 112}, ...]}

# send the same items back, in your order (most preferred first)
curl -X POST localhost:8000/session/submit -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "items": [<the items above, reordered>]}'

curl localhost:8000/server/statistics -H "X-Admin-Password: change-me-admin"
curl localhost:8000/server/export -H "X-Admin-Password: change-me-admin"   # CSV

# assignments: CSV "session_id,da,ia", then each participant looks theirs up
curl -X POST localhost:8000/server/assignments -H "X-Admin-Password: change-me-admin" \
  -H "Content-Type: text/csv" --data-binary @assignments.csv
curl localhost:8000/session/assignment/<session_id>
curl -X POST localhost:8000/server/reset -H "X-Admin-Password: change-me-admin"
```

## Routes

| Route | Method | Auth |
|---|---|---|
| `/server/activate` | POST | `X-Admin-Password` |
| `/server/deactivate` | POST | `X-Admin-Password` |
| `/server/reset` | POST | `X-Admin-Password` |
| `/server/statistics` | GET | `X-Admin-Password` |
| `/server/export` | GET | `X-Admin-Password` |
| `/server/assignments` | POST | `X-Admin-Password` (body: the CSV file) |
| `/session/start` | POST | none (403 if the panel is inactive) |
| `/session/submit` | POST | valid, unused `session_id` in the body |
| `/session/assignment/{session_id}` | GET | none (the session id itself): the submitted ranking and the DA/IA matches |

Submission rules (Pydantic models `Item` and `Submission`): the body is
`{"session_id": ..., "items": [{"label": ..., "seats": ..., "points": ...}, ...]}`.
The items must be exactly the ones the session was given (seats from
`labels.json`, points drawn for that session), only reordered. The position
in the list is the preference order, first = most preferred.

`/server/statistics` reports, per label, the average rank (1 = most
preferred) and how often it was ranked first, how many users submitted the
starting order unchanged, and whether assignments were uploaded.

Data lives under `data/` (gitignored): `data.jsonl` (submissions),
`sessions.json`, `state.json`, `assignments.json`, and
`archive/<timestamp>/` (written by reset, which also archives the
assignments).
