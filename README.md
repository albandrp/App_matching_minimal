# app-matching

Participants rank courses by preference; an admin collects the answers,
uploads the course assignments (DA and IA algorithms) and participants look
up their result. A FastAPI backend in one file, static HTML pages, and data
stored as JSON/JSONL files.

## Files

| File | Role |
|---|---|
| `app.py` | the whole API (FastAPI) |
| `config.json` | the courses and their % of available seats |
| `frontend/index.html` | participant page |
| `frontend/result.html` | result page |
| `frontend/admin.html` | admin page |
| `frontend/config.js` | the API address the pages call |
| `.github/workflows/pages.yml` | publishes `frontend/` to GitHub Pages, writing the `API_URL` repository variable into `config.js` |
| `data/` | runtime data, gitignored: `data.jsonl` (answers), `sessions.json`, `state.json`, `assignments.json`, `archive/` |

There are no automated tests.

## Run locally

**1. Install.** Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
git clone <repository URL> && cd <repository>
uv sync
```

**2. Start the API** (http://localhost:8000):

```bash
ADMIN_PASSWORD=change-me-admin uv run uvicorn app:app --reload
```

**3. Serve the pages**, in a second terminal (http://localhost:5173):

```bash
python3 -m http.server 5173 -d frontend
```

- Pages: `/` (participants), `/result.html`, `/admin.html`. API documentation: `http://localhost:8000/docs`.
- `ADMIN_PASSWORD` is required. Optional: `DATA_DIR` (default `data/`), `CONFIG_FILE` (default `config.json`).
- uvicorn logs each request with the client's IP, which can be linked to a session id; add `--no-access-log` to avoid it.
- A restart keeps the data and the open/closed state, resets the rate-limit counters and re-reads `config.json`.

## Deploy (SSP Cloud / Onyxia + GitHub Pages)

The API runs in an Onyxia service with an exposed port; the pages are published on GitHub Pages and call the API through its Onyxia URL (both are https).

**1. Onyxia service.** Launch a VS Code or Jupyter service and, **before launching**, enable the custom service port in its *Networking* tab (port `5000`). It cannot be added to a running service.

**2. Install, in the service's terminal** (`~/work` is the persistent folder):

```bash
git clone <repository URL> ~/work/app && cd ~/work/app
pip install uv && uv sync --frozen
cat > ~/work/app.env <<EOF
ADMIN_PASSWORD=$(openssl rand -hex 16)
DATA_DIR=$HOME/work/data
EOF
chmod 600 ~/work/app.env && cat ~/work/app.env   # note the admin password
```

**3. Start (or restart) the API:**

```bash
cd ~/work/app && pkill -f "uvicorn app:app"; set -a && . ~/work/app.env && set +a && \
nohup uv run uvicorn app:app --host 0.0.0.0 --port 5000 \
  --proxy-headers --forwarded-allow-ips '*' --no-access-log >> ~/work/api.log 2>&1 &
```

`--host 0.0.0.0` lets Onyxia reach the API; `--proxy-headers` makes the rate limit see each participant's IP instead of Onyxia's proxy. `nohup … &` keeps it running when the terminal is closed. Run a single process (no `--workers`).

**4. Public URL of the API:**

```bash
kubectl get ingress -o custom-columns=NAME:.metadata.name,HOST:.spec.rules[*].host
```

Take the host that does **not** end in `-ui` (that one is the editor) and prefix it with `https://`. Check: `https://<host>/docs` shows the API documentation.

**5. GitHub Pages**, in the repository (or a fork) on GitHub:

- *Actions*: on a fork, enable workflows first.
- *Settings → Pages → Source*: **GitHub Actions**.
- *Settings → Secrets and variables → Actions → Variables*: `API_URL` = the URL from step 4, without a trailing `/`.
- *Actions → Deploy frontend to GitHub Pages → Run workflow*. It also runs on every push that changes `frontend/`.

The pages are then at `https://<owner>.github.io/<repository>/` (participants), `…/result.html` and `…/admin.html`. Open the admin page, type the admin password and open the participant page.

**Operations**, in the service's terminal:

| Task | Command |
|---|---|
| Is it running? | `pgrep -af "uvicorn app:app"` |
| Logs | `tail -f ~/work/api.log` |
| Update | `cd ~/work/app && git pull && uv sync --frozen`, then step 3 |
| Admin password | `cat ~/work/app.env` (to change it, edit the file, then step 3) |
| Reset the answers | **Reset all answers** on the admin page |

- If Onyxia restarts the service, the API stops: run step 3 again.
- Deleting the service deletes `~/work` (code, data, password) and the next service gets a new URL: download the answers first, then redo steps 1–5.
- Update the API and the pages together (sync the fork if the pages come from one). If an update changes the stored data format, reset the answers afterwards.
- "The server cannot be reached" on the pages: check that `https://<owner>.github.io/<repository>/config.js` contains the API URL (otherwise rerun the workflow) and that `https://<host>/docs` answers (otherwise run step 3).

## Config

`config.json` lists `[course, % of available seats]` pairs; the seats must add up to 100:

```json
[["Artificial intelligence", 8], ["Cryptography", 23], ...]
```

It is read only at startup: after editing it, restart the API and reset the answers (old answers break the statistics and the export).

## Typical session

1. Edit `config.json` if needed, restart the API, reset leftover test answers.
2. Admin page: **Open the participant page**; participants answer and keep their session id.
3. Admin page: **Close the participant page**, then **Download user entries** and run the matching algorithms.
4. **Upload assignments**; participants look up their matches on the result page.
5. Once everything is saved, **Reset all answers**. Never reset before step 4: the upload would refuse the archived session ids.

## Pages

**Participant** (phone-friendly). Courses are sorted by ascending seats, each with "X% available seats, you have N points" (points: random 1–1000, per participant and per course). The participant reorders them with ↑/↓ and submits after a confirmation. They then get their session id (with a Copy button and a link to the result page), and the address becomes `?submitted=yes&session=<id>` so a refresh doesn't start a new questionnaire. While the participant page is closed, it says so.

Session ids are 8 characters without look-alikes (no 0/O, 1/I/L), e.g. `K7QXM3PA`, accepted in any case. Every page load creates a session, so "Sessions started" is always higher than "Submissions".

**Result.** The participant types their session id and sees their submitted ranking and, once uploaded, their match under DA and under IA. Works with the participant page closed.

**Admin**, after typing the admin password (kept in the page, never in the URL):

- **Open / Close the participant page** (doesn't affect the result page).
- **Statistics**: per course, average rank and times ranked first; starting order kept unchanged; assignments uploaded or not.
- **Download user entries**: `submissions.csv`, one row per submission: `session_id, submitted_at, rank_1 … rank_5` (`rank_1` = favourite), then one `<course> points` column per course.
- **Upload assignments**: CSV `session_id,da,ia` (header optional). The whole file is refused, with the problems listed, if an id is unknown or repeated or a course isn't in `config.json`. A new upload replaces the previous one.

  ```
  session_id,da,ia
  K7QXM3PA,Cryptography,Robotics
  ```

- **Reset all answers**: moves answers, sessions and assignments to `data/archive/<timestamp>/` (delete that folder to remove them for good).

## API

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/session/start` | POST | none | new session (403 if the participant page is closed) |
| `/session/submit` | POST | `session_id` in the body | submit the ranking |
| `/session/assignment/{session_id}` | GET | none | submitted ranking + DA/IA matches |
| `/server/activate`, `/server/deactivate` | POST | admin | open / close the participant page |
| `/server/statistics` | GET | admin | statistics |
| `/server/export` | GET | admin | answers as CSV |
| `/server/assignments` | POST | admin | upload the assignments CSV (as the body) |
| `/server/reset` | POST | admin | archive everything |

"admin" = header `X-Admin-Password`. Example: `curl -X POST localhost:8000/server/activate -H "X-Admin-Password: change-me-admin"`.

Submission body: `{"session_id": ..., "items": [{"course": ..., "seats": ..., "points": ...}, ...]}`: the session's items, only reordered, favourite first (validated with the Pydantic models `Item` and `Submission`).

- Errors: `401` wrong password or unknown session · `403` participant page closed · `404` unknown session id · `409` already submitted · `422` invalid body or CSV, or missing header · `429` too many requests.
- Rate limits per IP and per minute: 10 for admin routes, 100 for participant routes (a classroom on one wifi shares the 100).
- CORS accepts any origin (`allow_origins=["*"]`), so the pages can be hosted anywhere; restrict it in `app.py` if needed.
- Every response carries the app version (from `pyproject.toml`, edited by hand) in an `X-App-Version` header; the pages show it in their footer.

## License

MIT, see [LICENSE](LICENSE).
