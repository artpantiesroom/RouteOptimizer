# AGENTS.md

## 1. Project Overview

This project is a Progressive Web App (PWA) for planning and optimizing multi-stop routes.

Core user scenario:

A user provides a starting point and a list of addresses. The application geocodes the addresses, calculates an efficient visiting order, displays the optimized route on a map, and lets the user go through the stops one by one, opening each in an external navigation app (Waze or Google Maps).

The product must prioritize:

* simplicity;
* speed;
* clear UX;
* useful route optimization;
* mobile usability;
* low friction;
* reliable address handling.

The application is NOT intended to become a full fleet-management platform during the MVP stage.

## 2. Product Positioning

### What this product is

A **route planner / optimizer**. The full route lives inside the app: stops, order, statistics, progress.

### What this product is NOT

It is **not a navigator**. It does not provide turn-by-turn guidance, voice prompts, or traffic-based rerouting. Navigation is delegated to external apps.

### Target user

People who handle **many stops per shift** (roughly 10-50+), for example:

* couriers and small delivery businesses without a dispatcher;
* technicians and field workers with a daily list of visits;
* small shops delivering their own orders.

For 1-2 stops, Google Maps and a notepad are enough. The order is obvious and an optimizer adds nothing. These users are NOT the target.

The value of the product grows with the number of stops: manual ordering becomes slow and error-prone, and the difference between a "by eye" route and an optimized one becomes significant.

### Where the product must differentiate

Competitors already exist (Circuit/Spoke, Route4Me, OptimoRoute, Routific, MyRouteOnline and others). Route optimization alone is not a differentiator. This product competes on:

1. **Speed and zero friction:** paste a list, get a route, go. No registration, no setup.
2. **Address handling quality:** messy input (copied from messengers, invoices, spreadsheets) must be parsed, normalized, and validated well, including local address formats.
3. **Clarity:** a first-time user understands the app without instructions.

Do not add features merely because competitors have them.

## 3. Product Vision

The application should make route planning feel like:

```text
Paste addresses
      ↓
Recognize addresses
      ↓
Optimize
      ↓
See route
      ↓
Navigate stop by stop
```

The user should not need to understand routing algorithms, geocoding, graphs, APIs, or optimization.

Technical complexity must remain invisible to the user.

### Core product principle

The fastest route-planning experience is more important than the number of features.

## 4. MVP Scope

The first version should focus on the following workflow:

1. User enters a starting location.
2. User provides multiple destination addresses.
3. Application resolves addresses into coordinates.
4. Application validates unresolved or ambiguous addresses.
5. User optionally marks some stops as urgent.
6. Application calculates an optimized order.
7. Application displays the route on a map.
8. Application shows basic route statistics.
9. User goes through the stops one by one, opening the current stop in an external navigation app and marking it as done.

### MVP route information

At minimum:

* total distance;
* estimated travel time;
* number of stops;
* optimized stop order.

### Input methods

The initial architecture should allow:

* manual address input;
* pasted multiline address lists.

Pasted lists are the primary input method. Because the target user has many stops, fast and forgiving bulk input is a core feature, not a convenience.

CSV/Excel import is designed as an extension. It is not required for the first prototype, but it is the **first feature to add** after the end-to-end prototype works, since target users often have their orders in spreadsheets.

### Session state

The current route (stops, order, done/pending status) must survive a page reload or an accidental app close, since the user switches between this app and the navigator all day. Store it on the client (for example in local storage). This is NOT route history and NOT an account system.

## 5. Urgent Stops (MVP)

A stop may be marked as urgent.

Rules:

* Urgent stops are visited first. If there are several, they follow the order set by the user.
* The remaining stops are optimized starting from the last urgent stop.
* The UI shows the cost of urgency compared with the fully optimized route (for example: "urgent order adds +4 km and +9 min"), so the user understands the trade-off.

This is a deliberately simple "priority first" model.

### Time windows are NOT part of the MVP

Real time windows ("deliver between 12:00 and 13:00", "before 14:00") are a separate, much harder problem (VRP with time windows): they require a departure time, service time per stop, and handling of infeasible routes.

Time windows are implemented ONLY after explicit requests from target users.

### Architecture note

To avoid rewriting the contract later, `Stop` may carry optional fields such as `priority` (and later `deadline` or a time window), and the `Optimizer` interface accepts a set of constraints. Do not implement anything beyond urgent-first until it is requested.

## 6. Navigation Hand-off

The app does not navigate. It hands off one stop at a time.

Flow:

1. The app shows the full route, the order, and the current stop.
2. User taps "Navigate". The app opens Waze or Google Maps with the **coordinates** of the current stop.
3. User returns to the app and marks the stop as done.
4. The app highlights the next stop and offers to navigate to it.

Rules:

* Pass coordinates, not address text, so the external app does not re-geocode and choose a different point.
* Waze deep links accept a single destination, which fits the stop-by-stop flow. Verify against current Waze Deep Links documentation when implementing.
* Google Maps links support multiple waypoints only with a limit, so they must not be relied on for the whole route. Use the same one-stop-at-a-time approach.
* The navigator choice (Waze / Google Maps / Apple Maps) is one simple setting, remembered on the device.
* Marking a stop as done must be fast: one tap or swipe.

## 7. Explicitly Out of Scope for MVP

Do NOT implement the following unless explicitly requested:

* fleet management;
* driver accounts;
* dispatcher dashboards;
* live driver tracking;
* customer notifications;
* proof of delivery;
* billing;
* subscriptions;
* complex CRM;
* social features;
* internal navigation (turn-by-turn, voice guidance, traffic rerouting);
* custom map rendering;
* route history;
* advanced analytics;
* enterprise permissions;
* unnecessary authentication;
* time windows (see section 5).

The MVP must remain small.

## 8. Technology Direction

### Frontend

Preferred stack:

* React
* TypeScript
* Vite
* PWA support

The frontend is responsible for:

* user interface;
* address input;
* route visualization;
* validation feedback;
* stop progress (pending / done);
* navigator hand-off links;
* interaction with the backend;
* responsive/mobile UX;
* PWA functionality.

The frontend must not contain core route-optimization logic.

### Backend

Preferred stack:

* Python
* FastAPI

Python is chosen because the optimization ecosystem (Google OR-Tools, NumPy, geospatial libraries) is strongest there, and the backend is mostly orchestration of external services, where raw language speed is not the bottleneck.

The backend is responsible for:

* API endpoints;
* geocoding orchestration;
* routing-engine integration;
* route optimization;
* validation;
* future persistence;
* business logic.

### Communication

Use REST/HTTP for normal request/response operations.

WebSocket should NOT be introduced merely because the project is capable of using it. It should be introduced only when the application actually requires real-time communication, such as:

* live route recalculation;
* live driver positions;
* collaborative route editing;
* real-time progress updates.

For the MVP, ordinary HTTP requests are preferred unless a concrete requirement proves otherwise.

## 9. Routing Architecture

The application must not implement its own road network or map database.

Use external geospatial services where appropriate.

The architecture should keep the routing provider replaceable.

Conceptually:

```text
Frontend
   ↓
FastAPI
   ↓
Geocoding provider
   ↓
Coordinates
   ↓
Routing provider
   ↓
Distance / duration matrix
   ↓
Optimization algorithm
   ↓
Optimized route
   ↓
Frontend
```

Do not tightly couple business logic to one specific routing provider.

### Provider cost and limits

Public free instances of geocoders and routers (for example public Nominatim or public OSRM) have strict usage limits and are suitable for development and the prototype only. Plan for self-hosting or a paid provider if the product is validated. Keep this possibility open through the provider abstraction.

### Honest travel time

Without a live traffic source, estimated travel time is based on the road network and typical speeds, not on real traffic. The UI must not present it as a traffic-aware estimate.

## 10. Optimization

The system should distinguish between:

### Distance optimization

Minimize total route distance.

### Time optimization

Minimize estimated travel time.

### Balanced optimization

Optimize according to a combination of distance and travel time.

The exact optimization strategy may evolve.

The architecture must allow the optimization algorithm to be replaced without rewriting the frontend.

Possible technologies include:

* Google OR-Tools;
* custom heuristics;
* 2-opt;
* nearest-neighbor;
* other TSP/VRP algorithms.

Do not prematurely implement a sophisticated algorithm before a simple working solution exists.

Urgent-first behavior (section 5) is applied around the optimizer: fixed urgent prefix, then optimization of the remaining stops from the last urgent stop.

## 11. Important Domain Concepts

Keep these concepts separate in the code:

### Address

Human-readable location provided by the user.

### Coordinate

Resolved geographic position:

```text
latitude
longitude
```

### Stop

A destination with its original user data and resolved coordinates.

```text
Stop
├── original_address
├── coordinate
├── priority        (optional, urgent or normal)
├── status          (pending / done)
└── (future) time window
```

### Route

An ordered collection of stops.

### Route Result

The optimized route plus calculated metadata.

```text
RouteResult
├── ordered_stops
├── total_distance
├── total_duration
├── optimization_mode
└── urgency_cost    (extra distance / time caused by urgent stops, if any)
```

Do not mix raw addresses, coordinates, routing data, and UI state unnecessarily.

## 12. Provider Abstraction

External services should be accessed through abstractions.

For example:

```text
Geocoder
    └── provider implementation

Router
    └── provider implementation

Optimizer
    └── optimization implementation
```

The rest of the application should depend on the abstraction rather than directly on a specific provider.

This makes it possible to change:

```text
OSRM → GraphHopper
```

or:

```text
Provider A → Provider B
```

without rewriting the entire application.

The navigator hand-off (Waze / Google Maps / Apple Maps link builders) follows the same idea: one small function per navigator behind a common interface.

## 13. UX Principles

The application should feel simple enough that a first-time user can understand it without instructions.

Prefer:

* one primary action;
* obvious inputs;
* clear validation;
* minimal configuration;
* immediate feedback;
* mobile-first layouts.

Avoid:

* excessive settings;
* technical terminology;
* unnecessary dialogs;
* unnecessary navigation;
* hidden actions;
* multi-step setup when one step is possible.

The user should always understand:

1. what the application expects;
2. what it is currently doing;
3. what went wrong;
4. what they can do next.

## 14. Mobile First

The application is expected to be used heavily from smartphones, often one-handed and on the move.

Mobile UX is not a secondary version of desktop UX.

All core workflows must work comfortably on a phone.

The route screen must prioritize:

* map visibility;
* stop order;
* current stop and next stop;
* route statistics;
* navigation action;
* one-tap "done".

Do not create desktop layouts first and attempt to repair mobile behavior afterward.

## 15. Performance

Avoid unnecessary requests.

Address geocoding and route calculations can be expensive, especially with 30-50 stops.

The application should:

* debounce user input where appropriate;
* avoid duplicate geocoding requests;
* cache reusable results when practical;
* avoid recalculating unchanged routes;
* provide visible loading states (including progress for long address lists);
* handle slow external APIs gracefully.

Never block the UI without feedback.

## 16. Error Handling

External geospatial services are unreliable dependencies.

The application must gracefully handle:

* invalid addresses;
* ambiguous addresses;
* addresses that cannot be found;
* API failures;
* timeouts;
* rate limits;
* unavailable routing providers;
* partially resolved route lists.

Never silently discard an address.

If an address cannot be resolved, the user must be told which address caused the problem and what they can do (edit it, pick a suggested match, or skip it knowingly).

## 17. Code Quality

Prefer simple, readable code over clever abstractions.

Rules:

* avoid premature abstraction;
* avoid unnecessary dependencies;
* avoid duplicated business logic;
* keep functions focused;
* use meaningful names;
* keep modules reasonably small;
* type important data structures;
* validate external data;
* do not hide errors.

Do not introduce architecture solely for theoretical future requirements.

## 18. Frontend Rules

React components should primarily handle presentation and user interaction.

Do not put large amounts of route optimization or geospatial business logic directly inside React components.

Prefer separating:

```text
UI
↓
hooks / application logic
↓
API client
↓
backend
```

Keep API communication centralized rather than scattering raw `fetch()` calls throughout components.

## 19. Backend Rules

FastAPI endpoints should remain thin.

Prefer:

```text
API endpoint
    ↓
service
    ↓
domain logic
    ↓
provider
```

Avoid placing the entire application inside route handlers.

External API responses must be validated before being used by the rest of the application.

## 20. Security

Never expose:

* API keys;
* private credentials;
* secrets;
* provider tokens

to the frontend unless the provider explicitly requires public credentials.

Secrets belong in environment variables or an appropriate secret-management mechanism.

Never commit `.env` files containing real credentials.

User address lists can contain personal data (customer addresses). Do not log full address lists in production logs and do not send them to third-party services beyond the geocoding/routing providers that are actually needed.

## 21. Dependencies

Before adding a dependency, ask:

1. Is it actually necessary?
2. Does the project already provide an equivalent capability?
3. Is the dependency actively maintained?
4. Does it significantly increase complexity?
5. Can the feature reasonably be implemented without it?

Do not install libraries simply because they are popular.

## 22. Development Philosophy

Build vertically.

Prefer:

```text
working input
    ↓
working geocoding
    ↓
working route
    ↓
working optimization
    ↓
working UI
```

over building a large architecture before anything works.

Every major development step should produce something testable.

## 23. MVP Development Rule

When choosing between:

```text
A) complicated but theoretically scalable
B) simple and working
```

choose B unless there is a concrete technical reason not to.

The MVP exists to validate the product idea.

Do not optimize architecture before validating the user workflow.

## 24. Future Features

Potential future features include (roughly in expected order):

1. CSV/Excel import (first after the prototype);
2. drag-and-drop stop ordering;
3. saved routes;
4. time windows (only on explicit request from target users);
5. multiple optimization modes in the UI;
6. route sharing;
7. route history;
8. vehicle constraints;
9. OCR address recognition;
10. voice input;
11. live traffic;
12. multi-driver routes;
13. authentication;
14. accounts;
15. subscriptions.

These features must not influence the MVP implementation unless they require an architectural decision now.

## 25. Product Validation

The idea is validated with real users, not by assumption.

Before and during the MVP:

* talk to 5-10 potential users (couriers, field workers, small delivery businesses) and learn what they use today and what frustrates them;
* test the prototype on realistic data: 20-40 real-looking addresses, including messy formatting;
* watch where users hesitate or make mistakes;
* find out which constraint they ask for first (expected: time windows).

If target users are satisfied with Google Maps and a notepad, that is a valid result and must be taken seriously.

## 26. Decision Rule

When requirements are ambiguous:

1. Prefer the smallest implementation that solves the user's actual problem.
2. Prefer reversible technical decisions.
3. Avoid speculative infrastructure.
4. Preserve provider independence where reasonably cheap.
5. Prioritize user experience over technical novelty.
6. Do not add features without a demonstrated use case.

If a proposed change significantly expands the MVP, stop and explain why before implementing it.

## 27. Definition of Done

A feature is not considered complete merely because the code compiles.

For a user-facing feature, verify:

* it works on desktop;
* it works on mobile;
* loading states exist;
* error states exist;
* invalid input is handled;
* API failures are handled;
* no obvious console errors remain;
* the main workflow remains understandable;
* it works with a realistic number of stops (30+), not only with 3.

## 28. Current Priority

The immediate priority is NOT monetization or scaling.

The current priority is proving this workflow:

```text
ADDRESS LIST
     ↓
GEOCODING
     ↓
ROUTE CALCULATION
     ↓
OPTIMIZATION
     ↓
MAP
     ↓
NAVIGATION HAND-OFF (stop by stop)
```

The first milestone is a functional end-to-end prototype.

Suggested order of vertical slices:

1. Paste address list → geocode → show results with clear errors for unresolved addresses.
2. Distance/duration matrix from the routing provider.
3. Simple optimization (nearest-neighbor, then improve) → ordered stops.
4. Map with route and stop list on mobile.
5. Navigator hand-off for the current stop + mark as done + next stop.
6. Urgent stops with urgency cost.
7. Client-side session persistence.

Only after that should the project expand.
