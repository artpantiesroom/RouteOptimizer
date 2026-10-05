# RouteOptimizer

RouteOptimizer is a route planner (not a navigator). This is vertical slice 1: paste address list → geocode → show results with clear errors for unresolved addresses.

## Planned

Not implemented yet:

* **PWA support** (service worker, web app manifest, offline shell). The frontend is currently a plain Vite/React SPA.
* Routing provider and distance/duration matrix.
* Route optimization.
* Map view.
* Navigator hand-off.
* Client-side session persistence.

## Heuristic classification (geocoding results)

The Nominatim provider is configured with `addressdetails=1` and requests up to 5 candidates. Classification uses `house_number` and result `type` (not text guessing). House numbers like `12А`, `12/2`, `12 корп. 3` are accepted when present in `address.house_number`.

- **resolved**: exactly one candidate that is precise at building/house level. Considered precise if `address.house_number` exists (non-empty) OR the result `type` is building-like (`building`, `house`, `apartments`, `office`, `commercial`, `retail`, `industrial`, `warehouse`, `hotel`, `school`, `hospital`, `place_of_worship`, `government`, `public_building`, `civic`, `entrance`, `room`, `yes`). With a single precise candidate we return `resolved`.
- **partial**: a match found at street/locality level but not at house/building level (missing `house_number` and not building-like). Returned with a clear message so the user decides. This is **never** presented as `resolved`.
- **ambiguous**: multiple comparable candidates returned (up to 5). User must choose one candidate; selecting a candidate updates the row to `resolved` locally.
- **not_found**: no results or no valid results from provider.
- **error**: provider failure, timeout, rate limit (HTTP 429) or network error. Errors are **not** cached. On HTTP 429 the provider returns an `error` with Retry-After info if present (no automatic retry loop).

### Error kinds

Errors carry an `error_kind` so the UI can say something useful and offer the right action. `error_message` stays technical and is for logs and debugging only; the UI must render its own user-facing text based on `error_kind`.

- **`retryable`** — temporary condition (timeout, HTTP 429, network error, 5xx). User-facing text: "Could not check this address right now. Try again." The row offers a per-row **Retry** action that re-checks only that address.
- **`setup`** — the address search service rejected the request itself (HTTP 401/403). The address is not at fault and retrying will not help, so no Retry action is offered. User-facing text: "The address search service rejected the request. This is a setup problem, not your address."

On HTTP 401/403/5xx the backend logs the status code and the first 500 characters of the response body at WARNING. Address lists are never logged.

## API

- `POST /api/parse` — split text into lines, trim, ignore blanks, detect duplicates (case-insensitive). Returns items in input order with `is_duplicate` flag. Input limits: max 100 lines, max line length 500. Instant, no network.
- `POST /api/geocode` — geocode a small batch of addresses (max 5). Results returned in input order. Cache: `resolved`, `partial`, `ambiguous`, `not_found` are cached (by canonical key). `error` is never cached. Rate limit: 1 req/s delay applied **only before real network calls** (cache hits do not wait). The limiter uses a **shared async lock** held across the delay and the request. If provider returns HTTP 429, we return `error` (no auto-retry).
- `GET /api/health` — health check.

## Environment variables

See `backend/.env.example`. `NOMINATIM_USER_AGENT` **must** be configured (no usable generic default). The app fails fast at startup if missing.

## Running backend

```bash
cd backend
export NOMINATIM_USER_AGENT="RouteOptimizer-dev/0.1.0 (your-email@example.com)"
uvicorn app.main:app --reload --port 8000
```

## Running frontend

```bash
cd frontend
npm install
npm run dev
```

## Tests

```bash
# backend
cd backend
export NOMINATIM_USER_AGENT="RouteOptimizer-test/0.1.0 (test@test.com)"
python3 -m pytest -q

# frontend
cd frontend
npm test
```
