# CertiFake frontend — what this actually is

A single-page React client for the backend in `../backend`. One component file
(`src/App.jsx`, ~360 lines), no router, no state library, no TypeScript. That is
a deliberate scope statement, not an unfinished scaffold: the backend is the
product being demonstrated, and this exists so its endpoints can be exercised
by a human instead of only by curl.

## What it does (verified against the code, not against a design doc)

- **Auth panel** with `login` / `register` modes calling `POST /auth/login` and
  `POST /auth/register`; the JWT and email are persisted to `localStorage` so a
  refresh keeps the session, and `handleLogout` clears both.
- **Upload** via a drag-and-drop zone (or file picker) that `POST`s the file to
  `/analyze` as multipart form-data with the bearer token.
- **Polling**: after a 202, `setInterval` polls `GET /status/{id}` until the
  status becomes `completed` (renders the result) or `failed` (shows the
  error), clearing the interval in both cases.
- **Artifacts**: `/heatmap/{id}` and `/report/{id}` are authenticated
  endpoints, so plain `<img src>` / `<a href>` tags cannot fetch them. They are
  fetched as blobs with the `Authorization` header and handed to the DOM as
  `URL.createObjectURL` object URLs, which the effect's cleanup revokes.
- **Result rendering**: authenticity score with a colour scale
  (`>=80` good, `>=55` warn, else bad), verdict, extracted fields and
  suspicious signals from the analysis payload.

## What it deliberately does not have

No routing (one screen), no test suite, no SSR, no bundler config beyond
Vite defaults, no component library. If you are looking for the dark sidebar
with Dashboard / History / Settings and per-user profiles, that UI does not
exist here and never did — see "Verification status" below for why that is
worth saying out loud.

## Running it

```bash
npm ci          # exact install from package-lock.json
npm run dev     # vite dev server with HMR
```

`VITE_API_URL` points it at the backend; it defaults to
`http://127.0.0.1:8000`. The compose stack in the repo root does **not** serve
this app — it is a dev-time client for the API, not a deployed artifact.

## Verification status

- `frontend-build` in `.github/workflows/ci.yml` runs `npm ci`, then
  `npm run lint -- --deny-warnings`, then `npm run build` on every push. Until
  that job was added on Day 7, nothing anywhere had ever compiled this
  directory — no CI job referenced it and there is no test suite for it.
- The lint gate is strict on purpose. The first build found exactly one
  warning (`react-hooks/exhaustive-deps`, the heatmap/report effect missing
  `authHeaders`), fixed by memoizing `authHeaders` on `token` in `App.jsx`
  rather than by loosening the rule. `--deny-warnings` therefore gates real
  regressions.
- Verified green locally on 2026-09-30 before the CI job was added: vite 8.1.4,
  16 modules, ~62 kB gzipped JS, 0 warnings / 0 errors from oxlint.

## Note on screenshots

`../screenshots/` used to contain `certifake_ui.jpg`, and it was removed on
Day 7 rather than kept or referenced. It was a concept mockup of a much richer
interface — sidebar navigation, history and settings pages, per-user profiles,
font controls — none of which this app implements, and it carried visible
AI-generation artifacts (misspelled "Custom Analyeis", garbled signature
text). Keeping a 572 kB image named `certifake_ui.jpg` in a folder called
`screenshots/` asserts "this is what the product looks like" without any
markdown ever saying so, which is exactly the class of unearned claim this
repository has been removing for seven days. There is currently **no** UI
screenshot in this repo; if one is wanted, it should be captured from a
running build and labelled with the commit it came from.
