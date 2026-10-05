# RouteOptimizer

RouteOptimizer is a route planner (not a navigator). This is vertical slice 1: paste address list → geocode → show results with clear errors for unresolved addresses. Address recognition, city scoping and result editing are covered below; see *Address normalization* and *Fallback chain*.

## Planned

Not implemented yet:

* **PWA support** (service worker, web app manifest, offline shell). The frontend is currently a plain Vite/React SPA.
* Routing provider and distance/duration matrix.
* Route optimization.
* Map view.
* Navigator hand-off.
* Client-side session persistence.

## Address normalization

Every address is normalized deterministically in `backend/app/services/address_normalizer.py` before any network call. No request is made from the raw user text. The user's original string is always kept and shown back next to the result.

- **Postcode** — a trailing 5-digit postcode is recognized and removed from the query.
- **City** — known city spellings are canonicalized (`Киев` → `Київ`). The parse endpoint returns a `suggested_city` so the City field can be pre-filled; the user's value always wins over the suggestion.
- **Unit** — `кв`, `оф`, `под`, `эт` are recognized, and the unit kind is reported to the UI (`apartment`, `office`, `entrance`, `floor`). `15-9` is split into house `15` and apartment `9` and the result is flagged `unit_inferred` so the UI can ask the user to check it.
- **House** — parsed separately from the unit, accepting values like `12А`, `12/2`, `12 корп. 3` as they appear in `address.house_number`.
- **Street terms** — generic Russian terms are mapped to Ukrainian (`улица` → `вулиця`, `проспект` → `проспект`, `спуск` → `узвіз`, …). Proper-name adjectives are left alone in the free-text query, because Nominatim matches those tolerantly.
- **Structured street** — for the structured lookup the street is rebuilt as `вулиця, <house>` with the *real* Ukrainian street name. A Russian adjectival ending is rewritten here (`Покровская` → `Покровська`), because the structured `street` field does not tolerate it. This is verified against Nominatim: `street=вулиця Покровська` returns Kyiv, `street=вулиця Покровская` returns other towns.

## Fallback chain

At most **3** provider calls per address, each deduplicated against the previous ones:

1. the full normalized query;
2. the query without the unit, when it differs;
3. the structured lookup `street` + `city`, when both are known.

The chain stops as soon as an attempt reaches house level (`resolved` or `ambiguous`). A `partial` is only accepted immediately when the input contained no house number; when the user did give a house number, the chain keeps going, because a later spelling may resolve it. If no attempt resolves, the most informative answer is returned — `partial` beats `not_found`, and ties keep the earlier attempt so a drop notice is not lost.

A rate-limit or setup error stops the chain immediately: retrying another spelling would only burn requests against an already-limited provider.

City scoping and the house/partial split live in the service layer, not the provider, so every provider behaves the same way.

## City scoping

When the user picks a City, a candidate matches if any of `city`, `town`, `village`, `municipality` or `hamlet` equals it. A candidate with **no** locality is kept — dropping it would lose valid matches. `state` is deliberately not used: `Київська область` also covers the wrong towns, which is exactly the failure that produced `вулиця Покровская` matches from Боярка, Переяслав and Гостомель.

Candidates outside the chosen city are dropped and counted. The UI shows `5 matches were ignored (outside Київ).` so the drop is visible instead of silent.

Two house-level candidates on the *same street with the same house number* are collapsed into one, keeping the more important row. Nominatim returns an address and any place sitting at that address as separate rows (`8, Покровська вулиця` next to `Ліцей №100 «Поділ», 8, Покровська вулиця`); that is one location, not a choice. Different house numbers on one street stay `ambiguous`.

## Heuristic classification (geocoding results)

The Nominatim provider is configured with `addressdetails=1` and requests up to 5 candidates. Classification uses `house_number` and result `type` (not text guessing). House numbers like `12А`, `12/2`, `12 корп. 3` are accepted when present in `address.house_number`.

- **resolved**: exactly one candidate that is precise at building/house level. Considered precise if `address.house_number` exists (non-empty) OR the result `type` is building-like (`building`, `house`, `apartments`, `office`, `commercial`, `retail`, `industrial`, `warehouse`, `hotel`, `school`, `hospital`, `place_of_worship`, `government`, `public_building`, `civic`, `entrance`, `room`, `yes`). With a single precise candidate we return `resolved`.
- **partial**: a match found at street/locality level but not at house/building level (missing `house_number` and not building-like). Several hits on one street collapse into a single representative (highest importance), with the message "Street found, house not found. The pin is approximate." This is **never** presented as `resolved`.
- **ambiguous**: multiple distinct in-scope house-level candidates. Rows sharing a house number and street are collapsed first, so a POI at the searched address does not create a false choice. User must choose one candidate; selecting a candidate updates the row to `resolved` locally.
- **not_found**: no results or no valid results from provider.
- **error**: provider failure, timeout, rate limit (HTTP 429) or network error. Errors are **not** cached. On HTTP 429 the provider returns an `error` with Retry-After info if present (no automatic retry loop).

### Error kinds

Errors carry an `error_kind` so the UI can say something useful and offer the right action. `error_message` stays technical and is for logs and debugging only; the UI must render its own user-facing text based on `error_kind`.

- **`retryable`** — temporary condition (timeout, HTTP 429, network error, 5xx). User-facing text: "Could not check this address right now. Try again." The row offers a per-row **Retry** action that re-checks only that address.
- **`setup`** — the address search service rejected the request itself (HTTP 401/403). The address is not at fault and retrying will not help, so no Retry action is offered. User-facing text: "The address search service rejected the request. This is a setup problem, not your address."

On HTTP 401/403/5xx the backend logs the status code and the first 500 characters of the response body at WARNING. Address lists are never logged.

## API

- `POST /api/parse` — split text into lines, trim, ignore blanks, detect duplicates (case-insensitive). Returns items in input order with `is_duplicate` flag, plus a `suggested_city` used to pre-fill the City field. Input limits: max 100 lines, max line length 500. Instant, no network.
- `POST /api/geocode` — geocode a small batch of addresses (max 5). Optional `city` scopes matches to that city. Results returned in input order, each carrying `searched_as`, `house`, `unit`, `unit_kind`, `unit_inferred`, `dropped_candidates` and `scope_message` so the UI can explain what happened. Cache: `resolved`, `partial`, `ambiguous`, `not_found` are cached (by normalized query + requested city). `error` is never cached. Rate limit: 1 req/s delay applied **only before real network calls** (cache hits do not wait). The limiter uses a **shared async lock** held across the delay and the request. If provider returns HTTP 429, we return `error` (no auto-retry).
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

## Measuring quality

`backend/scripts/measure_quality.py` runs the real pipeline over a text file of
addresses (one per line, `#` comments allowed) and prints a status per address
plus a summary. It hits the real geocoder, so keep the file small — Nominatim
allows 1 request per second and its usage policy forbids bulk downloads.

```bash
cd backend
.venv/bin/python scripts/measure_quality.py addresses.txt --city Київ
.venv/bin/python scripts/measure_quality.py addresses.txt --city Київ --json out.json
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
