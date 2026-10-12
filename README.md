# app-matching

Participants rank courses by preference; an admin collects the answers,
uploads the course assignments (DA and IA algorithms) and participants look
up their result. A FastAPI backend in one file, static HTML pages, and data
stored as JSON/JSONL files.

## Files

| File | Role |
|---|---|
| `minimal_app.py` | the whole API (FastAPI) |
| `config.json` | the courses and their % of available seats |
| `frontend/index.html` | participant page (rank the courses) |
| `frontend/result.html` | result page (submitted ranking + DA/IA matches) |
| `frontend/admin.html` | admin page |
| `frontend/config.js` | the API address the pages call |
| `data/` | created at runtime: answers, sessions, assignments, archives (gitignored) |

There are no automated tests.

## Run

Requires Python 3.12 (see `.python-version`) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
export ADMIN_PASSWORD=change-me-admin
uv run uvicorn minimal_app:app --reload
```

The admin password is required: the app refuses to start without it.
Optional environment variables: `DATA_DIR` (where the data is stored, default
`data/`) and `CONFIG_FILE` (default `config.json`).

uvicorn logs every request with the client's IP address, and some request
URLs contain a session id, so the logs can link an IP to a participant's
answers. Add `--no-access-log` to the uvicorn command to keep IPs out of the
logs.

Restarting the API keeps the data and whether the participant page is open
or closed; it resets the rate-limit counters and re-reads `config.json`.

Frontend (separate static pages; they find the API through
[frontend/config.js](frontend/config.js), set to `localhost:8000`):

```bash
python3 -m http.server 5173 -d frontend
# participants: http://localhost:5173
# result:       http://localhost:5173/result.html
# admin:        http://localhost:5173/admin.html
```

## Config

[config.json](config.json) lists the courses as `[course, % of available seats]`
pairs; the seats must add up to 100:

```json
[
  ["Artificial intelligence", 8],
  ["Cryptography", 23],
  ...
]
```

The API reads it only at startup. To change the courses: edit the file,
restart the API, then reset the answers (answers recorded with the old
courses break the statistics and the export).

## Typical session

1. If needed, edit `config.json` and restart the API; reset any leftover test
   answers.
2. Admin page: **Open the participant page**.
3. Participants answer; each one keeps their session id.
4. Admin page: **Close the participant page**.
5. **Download user entries** and run the matching algorithms on the CSV.
6. **Upload assignments** (CSV `session_id,da,ia`).
7. Participants look up their matches on the result page (it works with the
   participant page closed).
8. Once everything is saved, **Reset all answers**. Never reset before step 6:
   the upload would then refuse the archived session ids.

## Pages

**Participant page** (built for phones). It calls `/session/start` directly -
a 403 means the panel is inactive (there's no dedicated status route).
Otherwise it shows each course with "23% available seats, you have N points":
the points are a random number between 1 and 1000, drawn per user and per
course. Courses are sorted by ascending seats. The user can only reorder them
with ↑/↓. Submitting asks for confirmation first, then shows the session id
with a Copy button and a link to the result page: participants need that id
to see their matches. After submitting, the address becomes
`?submitted=yes&session=<id>`, so refreshing shows the confirmation again
instead of a new questionnaire (unless the answers were reset). The footer
shows the session id and the app version.

Session ids are 8 characters, capital letters and digits without look-alikes
(no 0/O, 1/I/L), e.g. `K7QXM3PA`; lowercase is accepted when looking up a
result. Every page load creates a session, so "Sessions started" in the
statistics is always higher than "Submissions".

**Result page.** The participant types their session id (pre-filled when
coming from the link) and gets the course they are matched to under each of
the two algorithms ("Match under DA", "Match under IA"), once the admin has
uploaded the assignments. It also shows the ranking they submitted, with each
course's seats and points. Works even after the panel is closed.

**Admin page.** Type the admin password, then:

- **Open / Close the participant page** (one button at the top, its text
  follows the current state). Closing it doesn't affect the result page;
- **Refresh** shows the panel state, the counts, per-course stats and the raw
  statistics payload;
- **Download user entries** downloads `submissions.csv`: one row per
  submission, `session_id, submitted_at, rank_1 … rank_5` (`rank_1` = most
  preferred course), then one `<course> points` column per course;
- **Upload assignments** sends a CSV with three columns: session id, course
  matched under DA, course matched under IA (header optional):

  ```
  session_id,da,ia
  K7QXM3PA,Cryptography,Robotics
  MZC8B5QV,Game theory,Game theory
  ```

  The whole file is refused, with the list of problems, if a session id is
  unknown or repeated or a course isn't in `config.json`. A new upload replaces the
  previous one. Upload it **before** any reset: a reset archives the
  sessions, so their ids would then be refused as unknown.
- **Reset all answers** (after a confirmation) empties the answers, sessions
  and assignments. Nothing is deleted: they are moved to
  `data/archive/<timestamp>/` on the server. To delete them for good, remove
  that folder by hand.

The password stays in the input field, never in the URL.

The app version comes from `pyproject.toml` (edit it there by hand). The API
sends it in an `X-App-Version` header on every response (errors included),
and the pages show it in their footer.

## Try it with curl

```bash
# no status route: session/start itself is how you find out (403 while inactive)
curl -X POST localhost:8000/session/start

curl -X POST localhost:8000/server/activate -H "X-Admin-Password: change-me-admin"

curl -X POST localhost:8000/session/start
# => {"session_id": "...",
#     "items": [{"course": "Artificial intelligence", "seats": 8, "points": 547},
#               {"course": "Cryptography", "seats": 23, "points": 112}, ...]}

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

The interactive API documentation is at `/docs` (e.g.
`http://localhost:8000/docs`).

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
`{"session_id": ..., "items": [{"course": ..., "seats": ..., "points": ...}, ...]}`.
The items must be exactly the ones the session was given (seats from
`config.json`, points drawn for that session), only reordered. The position
in the list is the preference order, first = most preferred.

`/server/statistics` reports, per course, the average rank (1 = most
preferred) and how often it was ranked first, how many users submitted the
starting order unchanged, and whether assignments were uploaded.

Error codes:

| Code | Meaning |
|---|---|
| 401 | wrong admin password, or unknown session on submit |
| 403 | participant page closed (`/session/start`, `/session/submit`) |
| 404 | no submission or assignment for this session id |
| 409 | session already submitted |
| 422 | invalid body or CSV (the response lists the problems), or missing password header |
| 429 | too many requests |

Rate limits, per IP address and per minute: 10 for the admin routes, 100 for
the participant routes. Participants behind the same network (e.g. a
classroom wifi) share one IP, so they share the 100.

The API accepts requests from any website (CORS `allow_origins=["*"]`), so
the pages can be hosted anywhere; restrict it in `minimal_app.py` to your
frontend's address if needed.

Data lives under `data/` (gitignored): `data.jsonl` (submissions),
`sessions.json`, `state.json`, `assignments.json`, and
`archive/<timestamp>/` (written by reset, which also archives the
assignments).
