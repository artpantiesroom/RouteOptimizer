# RouteOptimizer

RouteOptimizer is a route planner (not a navigator). Slice 1: paste address list → geocode → show results with clear errors for unresolved addresses. Slice 2: a start point plus the recognized addresses produce a driving **distance/duration matrix** from a routing provider. Address recognition, city scoping, result editing and the precision of `resolved` are covered below; see *Address normalization*, *Fallback chain*, *House number equality*, *List-level checks* and *Route planning*.

## Planned

Not implemented yet:

* **Route optimization** (ordering the stops).
* **PWA support** (service worker, web app manifest, offline shell). The frontend is currently a plain Vite/React SPA.
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

Every candidate is judged against the house number **that attempt actually sent**, not against the parsed house. This matters because the normalizer splits a range into a house and a unit: `51-53` is sent literally on the first attempt and as `51` on the second, so a provider answering `51/53` is the first attempt's answer and `51` is the second's. A candidate that only matches a later attempt never satisfies an earlier one.

The chain stops as soon as an attempt reaches house level (`resolved`, or `ambiguous` between several matches for the number *it* asked for). A `partial` is only accepted immediately when the input contained no house number; when the user did give a house number, the chain keeps going, because a later spelling may resolve it. If no attempt resolves, the most informative answer is returned — `partial` beats `not_found`, and ties keep the earlier attempt so a drop notice is not lost.

A **house mismatch** is not an ambiguity. When the candidate's house number does not contain the requested one (`30` against `28-30`), the chain keeps going and only reports it if nothing better turns up. It then returns `ambiguous` with exactly one candidate and the message `Found 28-30, you asked for 30.`, so the row can be accepted in one tap.

If every candidate of every attempt was rejected as out of scope, the city scope may be the reason, so one final **street-only** lookup runs in the active city before giving up. A street that exists yields `partial` with an approximate pin (`Street found, house not found`); a street that does not exist yields `not_found` with `No such address found in <city> in the map data`. Either way the row no longer offers a retry in the same city, because that has just been shown not to work; the drop notice from the earlier candidates is preserved.

A rate-limit or setup error stops the chain immediately: retrying another spelling would only burn requests against an already-limited provider.

City scoping and the house/partial split live in the service layer, not the provider, so every provider behaves the same way.

## House number equality

`resolved` means the requested house was found, not merely *some* house. Before a candidate is accepted the requested and found numbers are compared by `house_numbers_equal()`:

- case-folded, with the Latin/Cyrillic look-alikes mapped to one script (`6А` = `6A` = `6а`);
- dashes and slashes treated alike (`51-53` = `51/53`), and a slash before a letter dropped (`6-А` = `6а`);
- an absent letter suffix is a different house (`6` ≠ `6а`), and so is an extra one.

`1` is not `130/1` and `40` is not `40/5`. A candidate whose house differs is never `resolved`; the row becomes `ambiguous` with `found_house`, and the UI shows `House 40 was asked for, the match is 40/5.` so the mismatch is visible.

A house mismatch is **not** terminal for the fallback chain: the next spelling of the address may still match exactly, so the chain keeps going and only settles on an ambiguity that actually matched the requested house.

Note that `normalize()` reads a dash as the house/unit separator (`15-9` → house `15`, unit `9`), so `51-53` cannot reach the classifier as a range — the pure function accepts it, but the pipeline only sees `51` and searches for that.

## City scoping

When the user picks a City, a candidate matches if any of `city`, `town`, `village`, `municipality` or `hamlet` equals it. A candidate with **no** locality is kept — dropping it would lose valid matches. `state` is deliberately not used: `Київська область` also covers the wrong towns, which is exactly the failure that produced `вулиця Покровская` matches from Боярка, Переяслав and Гостомель.

The scope is `city or normalized.city`: a city written *inside* the address is used as the scope even when the City field is blank. This is why `вулиця Дмитрівська, 86, Київ` no longer resolves to Яготин. An explicit City wins over the address text, so a line that names somewhere else can be deliberately re-scoped.

Candidates outside the scope are dropped from the accepted set and counted. The row keeps the fact that matches existed: it is returned as `not_found` with `found_city` set and `needs_check` on, and the drop is shown as `5 matches were ignored (outside Київ).`. The status stays non-terminal so the structured attempt still runs — an out-of-scope hit must not stop the chain, because the scoped request may well find the right street.

## List-level checks

Some rows are wrong in a way that only shows up when the list is read together. Both checks are pure functions in `list_checks.py` and need no network.

- **Dominant city** — when the City field was left blank, a city is suggested only if at least 5 confident `resolved` rows report it and it holds at least 60% of them. The threshold is about the city, not the list: 5 rows in Київ and 1 in Полтава suggests Київ; 4 and 1 does not. The suggestion is offered for the City field and never applied silently. With an explicit city there is no suggestion.
- **Distance outliers** — a `resolved` row more than 50 km (`DEFAULT_OUTLIER_KM`, configurable via `GeocodeService(max_outlier_km=...)`) from the median of the other resolved rows is marked `needs_check`. The median deliberately excludes the row being judged, so one outlier cannot drag the centre towards itself.

A row with `needs_check` is **not** counted as recognized in the UI, even when its status is `resolved`, and the results header shows how many need a check. Flagged rows show `Found in Полтава, outside Київ.` plus a one-tap `Search again in Київ` that re-runs only those rows with the city set.

Two house-level candidates on the *same street with the same house number* are collapsed into one, keeping the more important row. Nominatim returns an address and any place sitting at that address as separate rows (`8, Покровська вулиця` next to `Ліцей №100 «Поділ», 8, Покровська вулиця`); that is one location, not a choice. Different house numbers on one street stay `ambiguous`.

## Route planning

A **Router** abstraction (`backend/app/providers/routing/base.py`) turns an ordered list of coordinates into a driving distance/duration matrix. Slice 2 ships one provider, **OSRM**, calling `GET {base}/table/v1/driving/{lon},{lat};{lon},{lat}...?annotations=duration,distance`. The first matrix point is the **start point**; the rest are the stops in list order. The provider validates every response before it reaches the service layer (square numeric matrices, non-negative cells, zero diagonal, `code == "Ok"`) and maps failures to typed errors: `setup` (HTTP 401/403, non-retryable), `retryable` (timeout, HTTP 429, 5xx, network — never auto-retried), and `too many points` (OSRM `TooBig`).

The **matrix service** (`backend/app/services/matrix_service.py`) validates the request (2..`MATRIX_MAX_POINTS` points, coordinate ranges, duplicate ids) and reports two problem kinds instead of failing the whole request when only part of the matrix is unusable:

- **unreachable** — a point has no road route to/from the rest (an entire matrix row or column is `null`);
- **far_from_road** — a point is more than `max_snap_distance_m` (default 500 m) from the road network, so its times are approximate.

The provider instance carries the **rate limiter** (a shared async lock held across the delay and the request; `OSRM_RATE_LIMIT_DELAY_SECONDS`, default 1 s) and the **response cache** (keyed by coordinates rounded to 6 decimal places; successes only — errors are never cached). HTTP 429/401/403/5xx log the status code and the first 500 characters of the body at WARNING; coordinates are never logged.

The public OSRM demo server is for non-commercial, low-volume use, offers no uptime guarantee and must be credited (`Map data © OpenStreetMap contributors. Routes by OSRM.` in the frontend footer). For production, self-host OSRM (or use a paid provider) and point `OSRM_BASE_URL` at it — the abstraction keeps the rest of the app unchanged. Durations are road-network estimates without live traffic, and the UI says so.

## Heuristic classification (geocoding results)

The Nominatim provider is configured with `addressdetails=1` and requests up to 5 candidates. Classification uses `house_number` and result `type` (not text guessing). House numbers like `12А`, `12/2`, `12 корп. 3` are accepted when present in `address.house_number`.

- **resolved**: exactly one in-scope candidate that is precise at building/house level *and* carries the requested house number. Considered precise if `address.house_number` exists (non-empty) OR the result `type` is building-like (`building`, `house`, `apartments`, `office`, `commercial`, `retail`, `industrial`, `warehouse`, `hotel`, `school`, `hospital`, `place_of_worship`, `government`, `public_building`, `civic`, `entrance`, `room`, `yes`). The house must also pass `house_numbers_equal()`; see *House number equality*. The provider returns every precise candidate, including a single one, so the service layer can apply the city scope and the house check to it.
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
- `POST /api/geocode` — geocode a small batch of addresses (max 5). Optional `city` scopes matches to that city. Results returned in input order, each carrying `searched_as`, `house`, `unit`, `unit_kind`, `unit_inferred`, `dropped_candidates`, `scope_message`, `found_house`, `found_city`, `needs_check`, `needs_check_reason` and `retry_city` so the UI can explain what happened. When `city` was blank, the response may also carry a top-level `city_suggestion` and `city_suggestion_share`. Cache: `resolved`, `partial`, `ambiguous`, `not_found` are cached (by normalized query + requested city). `error` is never cached. Rate limit: 1 req/s delay applied **only before real network calls** (cache hits do not wait). The limiter uses a **shared async lock** held across the delay and the request. If provider returns HTTP 429, we return `error` (no auto-retry).
- `GET /api/health` — health check.
- `POST /api/matrix` — compute the driving distance/duration matrix. Body is a bare JSON array of `{id, lat, lon}`; the first point is the start. Returns `ids` (echoed in matrix order), `durations_s` and `distances_m` (rounded to whole seconds/metres, `null` where no route exists), `problems` (`unreachable`, `far_from_road`), `provider`, `profile` and a user-facing `note`. Errors: `400` for validation or too many points, `502` with `{"message", "error_kind"}` (`retryable`/`setup`) for provider failures. Frontend wording: retryable → "Could not calculate the route right now. Try again."; setup → "The route service rejected the request. This is a setup problem, not your addresses."

## Environment variables

See `backend/.env.example`. `NOMINATIM_USER_AGENT` **must** be configured (no usable generic default). The app fails fast at startup if missing. Routing is configured by `OSRM_BASE_URL` (default: the public demo `https://router.project-osrm.org`), `OSRM_TIMEOUT_SECONDS`, `OSRM_RATE_LIMIT_DELAY_SECONDS` and `MATRIX_MAX_POINTS` (default 50).

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
.venv/bin/python scripts/measure_quality.py addresses.txt --trace
```

`--city` sets the active scope. Left blank, the script says so explicitly
(`city: none (taken from the addresses)`) rather than implying no scoping, and
each row's effective scope is the city written in that address.

Per row it prints the status, the active city, the house asked for against the
one found, the city the coordinate sits in, any `needs check` reason and retry
city, and an OpenStreetMap link for every resolved pin, so a wrong match can be
eyeballed. The summary separates `resolved (ok)` from `needs check`, lists any
inferred units, house mismatches and dropped out-of-scope matches, and reports
any suggested city.

`--street-level-report` explains every non-resolved row. It answers the question
our own filters hide: did the provider return the requested house at all, and did
our query or city filter throw it away? Each row gets exactly one of five
verdicts, most specific first, so the per-file counts always add up to the
non-resolved rows:

| verdict | meaning |
| --- | --- |
| `found_in_scope_but_rejected` | OSM has the house inside the active city and our own rules still dropped it — **a bug** |
| `found_outside_scope` | the requested house exists, but only in another city |
| `different_house` | only some other house number exists on that street |
| `street_only` | the street exists in the city, no house number anywhere |
| `nothing_returned` | the provider returned nothing for any of our queries |

A candidate counts as "the requested house" when it matches any number an
attempt actually sent. Each house candidate is listed with its city, whether it
was in scope, and whether it is the requested number.

`--trace` prints every fallback attempt: the query or the structured
street/city pair that was sent, the scope applied, the requested house, each
candidate with its house number and city, and the line that accepted or rejected
it. A row served from the batch cache shows as `cache` rather than as a request.

Sample input files belong in `backend/scripts/samples/`, which is gitignored:
real addresses never enter the repository.

A stored fixture (`backend/tests/fixtures/osrm_matrix_sample.json`) holds one real
OSRM response for six Kyiv landmarks, so the matrix property tests check real
numbers (road distance is never shorter than the straight line within the snap
tolerance, asymmetric matrices are accepted, the diagonal is zero) without
hitting the network.

## Demoing the matrix

`backend/scripts/matrix_demo.py` geocodes a text file of addresses with the same
pipeline as the API (first line = start point), requests the matrix, and prints
drive times (minutes) and distances (km) plus any problems. It hits the real
geocoder and the real routing service, so keep the file small and personal.

```bash
cd backend
.venv/bin/python scripts/matrix_demo.py scripts/samples/matrix_demo_kyiv.txt --city Київ
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
