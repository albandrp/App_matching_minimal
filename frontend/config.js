// Where the pages find the API. Locally (python -m http.server) it's the
// uvicorn on port 8000; deployed (GitHub Pages) it's the Onyxia service URL.
// After (re)launching the Onyxia service, paste its URL below and push.
const API_URL = location.hostname === "localhost"
  ? "http://localhost:8000"
  : "https://user-albandrp-319731-user.user.lab.sspcloud.fr";
