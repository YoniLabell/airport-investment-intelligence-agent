# Design notes

Airport Investment Intelligence — architecture, methodology, assumptions,
limitations and tradeoffs.

> **Scope statement, up front.** This is an investment **screening** tool. It
> narrows a field of ~66 airports down to the handful worth modelling properly.
> It is **not** a financial valuation model: there are no construction costs, no
> capital structure, no traffic forecasts, no discount rates, no NPV or IRR.
> Anyone treating an Expansion Score as a valuation is misreading it.

---

## 1. Architecture

### 1.1 The shape

```
┌──────────────────┐       fetch        ┌────────────────────────────────┐
│  Static dashboard│ ─────────────────► │  FastAPI                       │
│  HTML · CSS · JS │ ◄───────────────── │  validation + shaping only     │
│  chat · rankings │   (same origin;    │  + serves the dashboard files  │
│  compare · score │    no CORS)        └──────────────┬─────────────────┘
└──────────────────┘                                   │
                                        ┌──────────────▼─────────────────┐
                                        │  Agent (app/agent)             │
                                        │  Claude picks tools & narrates │
                                        └──────────────┬─────────────────┘
                                                       │ structured JSON
                                        ┌──────────────▼─────────────────┐
                                        │  Analytics (app/analytics)     │
                                        │  pandas. All arithmetic lives  │
                                        │  here and only here.           │
                                        └──────────────┬─────────────────┘
                                        ┌──────────────▼─────────────────┐
                                        │  Data (app/data)               │
                                        │  BTS → cache → demo            │
                                        │  HISTORICAL — drives every      │
                                        │  metric, ranking and score      │
                                        └────────────────────────────────┘

                                        ┌────────────────────────────────┐
                                        │  Services (app/services)       │
                                        │  AviationWeather.gov METAR     │
                                        │  LIVE — context only, reachable │
                                        │  from the agent and the API,    │
                                        │  never from analytics           │
                                        └────────────────────────────────┘
```

Four layers, each depending only on the one below it. The UI holds no analytics;
the API holds no analytics; the agent holds no analytics. Everything numeric is
in `app/analytics/`, which knows nothing about HTTP or LLMs and is therefore
trivially testable — which is why 232 tests run in under four seconds with no
network and no API key.

### 1.2 Where AI is used, and where it is not

**Claude does exactly two things:**

1. **Tool selection and argument extraction.** Turning *"how do Boston and
   Providence compare?"* into `compare_airports(iatas=["BOS","PVD"])`, including
   resolving follow-up references like *"which one is better?"* against the
   conversation history.
2. **Explanation.** Turning a JSON breakdown into prose an analyst can read,
   leading with the driver that mattered.

**Claude never:**

- computes, estimates, recalls or interpolates a statistic;
- performs arithmetic on tool results — no summing, averaging, percentage or
  growth maths of its own;
- decides regional membership. *"New England"* is resolved by
  `app/analytics/regions.py` against the `region` column of the airport metadata
  table. A model's recollection of which states are in New England is not a
  dependency of this system.

This is enforced three ways: the system prompt states the rule first and
plainly; there is a purpose-built tool for every derived quantity so the model
never *needs* to do maths; and the UI renders the raw tool output beside every
answer, so a wrong number is visibly traceable to a Python function rather than
to a hallucination.

The failure mode this design eliminates is the expensive one: a confident,
well-written, subtly wrong number in an investment memo.

### 1.3 The tool-use loop

`app/agent/service.py` runs a **manual** Messages API loop rather than the SDK's
beta `tool_runner`. Two reasons: the product requires every tool call and raw
result surfaced to the UI (that transparency *is* the feature), and a manual
loop keeps the service off a beta dependency. The loop caps at
`AGENT_MAX_TOOL_ROUNDS` (default 6), returns all tool results for one assistant
turn in a single user message (splitting them suppresses parallel tool calls),
marks failed tools with `is_error` so the model can recover rather than 500, and
handles a `refusal` stop reason explicitly.

Model: `claude-opus-5`, with server-side refusal fallbacks enabled and an
automatic downgrade to the standard endpoint if that beta is unavailable to the
account.

### 1.4 Graceful degradation

Three independent failure paths, each with an honest answer:

| Failure | Behaviour |
|---|---|
| No `ANTHROPIC_API_KEY` | A deterministic intent router (`app/agent/fallback.py`) parses the question, calls the same tools, renders a markdown answer. Labelled *"Analytics engine (no AI narration)"*. |
| Anthropic API errors or times out | Same fallback, with the reason surfaced to the user. |
| Live data source unavailable | `DataRepository` falls back to a cached pull, then to the bundled demo snapshot, relabelling the provenance each time. |

The app is never simply *down*. It degrades to a less capable but still correct
and still honest state — which for a screening tool that gets demoed on
conference-room WiFi is the right tradeoff.

### 1.5 The dashboard

`frontend/` is plain HTML, CSS custom properties and three ES modules, served
by `StaticFiles` from the same FastAPI app. No framework, no bundler, no
`package.json`.

Three consequences worth stating:

- **Same origin.** The browser never makes a cross-origin request, so the UI
  needs no API base URL and CORS cannot be misconfigured. Hosting the bundle
  separately still works — `app.js` honours `window.AII_API_BASE`.
- **The mount is registered last.** Starlette matches routes in order, so
  `/health` and `/api/*` always win and the static mount only catches what is
  left. There is a test asserting exactly that.
- **The UI computes nothing.** It formats and lays out figures the API already
  calculated. The same discipline that keeps arithmetic out of the LLM keeps it
  out of the browser: one place to audit, one place to fix.

Everything rendered from API text — answers, tool output, airport names — is
HTML-escaped first, including inside the Markdown renderer, so model-authored
text cannot inject nodes into the page.

### 1.6 Two sources, deliberately not connected

The project reads from two public sources that answer different questions:

| | Source | Horizon | Feeds |
|---|---|---|---|
| Historical | US DOT / BTS T-100 | Years | Every metric, ranking, proxy and score |
| Live | AviationWeather.gov (NOAA/NWS) METAR | Right now | Nothing. Context only. |

**Why keep them apart.** Current weather is the most tempting irrelevant
variable in this whole domain: it is vivid, it is free, and it correlates with
nothing an investor cares about on a ten-year horizon. Fog at SFO this morning
says nothing about whether SFO needs another concourse. Letting it touch a score
would be a category error, and — worse — would make an investment number depend
on a third party's uptime.

So the separation is structural, not just documented:

- Live weather lives in its own package (`app/services/`) that `app.analytics`
  does not import. The dependency simply does not exist.
- Every conditions payload carries `used_in_scoring: false`, `data_kind:
  "live_operational_context"` and a `source_role` sentence stating the split, so
  the labelling travels with the data through the API, the tool result and the
  chat answer.
- The system prompt tells the agent to report both but never to use conditions
  as evidence for or against an expansion case.
- A test asserts an airport's Expansion Score is identical whether the weather
  feed is healthy, dead, or reporting LIFR.

**Why METAR, and why an ICAO lookup table.** AviationWeather.gov is a genuinely
public, keyless JSON API from NOAA/NWS — no signup, no quota negotiation. It
keys on ICAO identifiers while this project speaks IATA, so `app/services/icao.py`
resolves them: a `K` prefix for the contiguous US, and an enumerated table for
Alaska (`PA`), Hawaii (`PH`), Guam (`PG`) and Puerto Rico / USVI (`TJ`/`TI`),
where the prefix rule does not hold. A test walks every airport in the dataset
and asserts none is unmappable, so adding an airport cannot silently break the
lookup.

**Failure policy.** The provider returns failures, never raises them. Callers
always get a payload with a `status` of `ok`, `no_report`, `unsupported`,
`unavailable` or `disabled`. `GET /api/airports/{iata}/conditions` answers 200
even on an upstream outage, because a supplementary panel failing is not a
failed request. Observations are cached for ten minutes — METARs are issued
about hourly, so anything shorter is wasted traffic — and failures are
deliberately not cached, so a transient outage does not poison the cache for a
full TTL.

**What is derived, and what is reported.** Flight category comes from the API's
`fltCat` when present. When it is absent the provider derives it from the
standard FAA visibility/ceiling thresholds and sets `flight_category_derived:
true`, so a reader can tell a reported category from a computed one. Everything
else — wind, visibility, ceiling, temperature — is reported as the API gives it,
with unit conversions (hPa to inHg) shown alongside the original.

### 1.7 `/health`

`/health` does no I/O at all: no dataset load, no upstream call, no Anthropic
request. It returns 200 from in-process state. A health check that fails because
a third party is having a bad day is a health check that causes outages rather
than detecting them. There is a test that monkeypatches every outbound HTTP path
to raise and asserts `/health` still returns 200.

---

## 2. Scoring methodology

### 2.1 The Expansion Score

```
score = 100 × [ 0.30·demand_pressure
              + 0.25·passenger_growth
              + 0.20·capacity_constraint
              + 0.15·flight_growth
              + 0.10·long_haul_connectivity ]
```

Two composite pillars, deliberately split along the **airside/landside** line so
they measure different things:

**Demand pressure (30%)** — how hard the aircraft and the airfield are working.

```
0.60 · scale(load_factor,           0.76 → 0.88)
0.40 · scale(departures_per_runway, 5,000 → 90,000)
```

**Capacity constraint (20%)** — how constrained the *terminal* is. Terminal
capacity is what an expansion actually buys, so this pillar is about landside
and regulatory ceilings.

```
0.55 · scale(passengers_per_gate,   60,000 → 400,000)
0.20 · (1 if slot_controlled else 0)
0.25 · scale(capacity_growth_lag,   -0.005 → 0.02)
```

The remaining three pillars are single scaled metrics: passenger CAGR
(`0.005 → 0.07`), flight CAGR (`0.005 → 0.055`), and long-haul departure share
(`0.0 → 0.25`).

### 2.2 Why fixed anchors instead of percentile ranks

The obvious alternative is to score each metric by its percentile within the
cohort. We chose fixed anchor bands instead, for three reasons:

1. **Stability.** Adding one airport to the dataset would shift every other
   airport's percentile — and therefore its score. With fixed anchors, adding an
   airport changes nothing else.
2. **Portability of meaning.** A 68 means the same thing in a six-airport New
   England shortlist as in a national ranking. Percentile scoring makes "the
   best of six mediocre airports" and "the best of sixty" look identical.
3. **Auditability.** An analyst can recompute a score by hand from the published
   anchor table. There is a test that does precisely that and asserts agreement.

The cost is that the anchors are a judgement call, and they encode a view of
what "constrained" looks like at a US airport. They are all in one dictionary
(`ANCHORS`), documented, and versioned — so disagreeing with them is a
five-minute change plus a re-run of the test suite, not a rewrite.

### 2.3 Correlation between pillars, acknowledged

Passengers per gate and departures per runway both rise with airport size, and
load factor correlates with both. The pillars are not statistically independent,
so the score is somewhat size-weighted. We accept this: an analyst screening for
*terminal expansion* opportunities does in fact want large, busy, growing
airports surfaced. What we avoided is double-counting the *same* metric in two
pillars — each raw metric appears in exactly one place.

### 2.4 The Unmet Demand Proxy

Kept **outside** the Expansion Score, as a separate 0–100 index, because it
answers a different question and because bundling a proxy into a headline number
would launder its uncertainty.

```
index = 100 × [ 0.35·scale(load_factor, 0.78 → 0.90)
              + 0.25·scale(passenger_cagr,      0.005 → 0.07)
              + 0.25·scale(capacity_growth_lag, -0.005 → 0.02)
              + 0.15·scale(flight_cagr,         0.005 → 0.055) ]
```

**Why it can only ever be a proxy.** Public aviation datasets are records of
what *happened*: flights operated, seats offered, passengers carried. Latent
demand — the trips people wanted and did not take because there was no seat, no
route, or no affordable fare — leaves no trace in them. Spill and recapture
modelling requires fare data, schedule search data and catchment surveys that
BTS does not publish.

So the index infers pressure from four observable signals, of which the third —
**seat capacity growing more slowly than passengers** — is the most diagnostic:
it is the observable shadow of a supply ceiling. The disclaimer is attached to
the number in the data structure itself, so it travels through the API, the chat
answer and the UI rather than living only in documentation.

### 2.5 Long-haul

`distance_miles >= LONG_HAUL_MILES`, default **2,500 statute miles**, applied to
non-stop great-circle segment distance. Nothing else. Not a domestic /
international distinction, not an equipment-type inference.

The threshold is a single environment variable, and there is a test asserting
the share is monotonically non-increasing as the threshold rises. A 0% result
(SNA) is a real finding — SNA's runway length constrains its stage length — not
a data gap.

Long-haul earns only 10% of the score. It is a genuine driver of terminal
requirements (wide-body gates, customs and immigration halls, longer dwell
times), but it is the weakest predictor of expansion need among the five, and
several strong domestic candidates would be unfairly penalised at a higher
weight.

---

## 3. Data

### 3.1 Provider abstraction

`AirportDataProvider` is a `Protocol` with one method, `load() -> AirportDataset`.
`AirportDataset` validates its three tables' columns on construction, so a
malformed source fails loudly at the boundary rather than producing a plausible
wrong number six layers up.

Swapping the source — a warehouse, a vendor feed, OAG schedules — means writing
one class and changing one line in `DataRepository`. Nothing in `analytics/`,
`agent/`, `api/` or the frontend moves.

### 3.2 Why BTS T-100 is read from an extract, not an API

The US DOT publishes T-100 Domestic and International Segment through TranStats.
There is no stable, keyless JSON API for it — the public route is a filtered
CSV/ZIP export. `BTSDataProvider` therefore accepts the extract two ways: a
directory of downloaded files (`BTS_LOCAL_EXTRACT_DIR`, what an analyst actually
has) or a direct URL (`BTS_T100_URL`, fetched with a hard timeout). Both parse
into the same shape. Pretending a fictional REST endpoint exists would have been
a worse deliverable than being straight about how this data is actually
distributed.

Airport *metadata* — region, gates, runways, slot control — is stable reference
data and is joined from the static table even on the live path. T-100 does not
contain it, and it does not change year to year.

### 3.3 The three-state provenance

Every response carries `live` / `cached` / `demo`, and the UI badges it. This
matters more than it looks: the single most dangerous failure mode for a
screening tool is silently serving stale or synthetic data while looking
authoritative. Demo data is labelled `DEMO` at construction time in the demo
provider, and no code path can relabel it as live — a test asserts this.

### 3.4 The bundled dataset, honestly described

66 US airports, 919 non-stop segments, 2022–2024. Airport-level volumes are
**rounded approximations of publicly reported FAA/BTS figures**; the route table
is **synthesized** to have the structure of a T-100 segment extract, with
distances computed by haversine from real coordinates. `scripts/generate_seed_data.py`
regenerates it deterministically and documents every parameter.

It exists so the app is demoable on a plane. It is not a basis for an investment
decision, and both the README and the UI say so.

---

## 4. Assumptions

1. **Enplanements are a usable proxy for terminal load.** Terminal sizing
   actually keys off peak-hour passengers, not annual totals. Peak-hour data is
   not public; annual volume per gate is the best available stand-in.
2. **Gates and runways are a usable proxy for physical capacity.** Gate *size*
   (narrow-body vs wide-body stands), runway *length*, and terminal square
   footage matter and are not captured. Runway length in particular is why SNA
   has no long-haul service — the model sees the effect, not the cause.
3. **A three-year window is enough to read a trend.** It is short. It is also
   what post-pandemic aviation data honestly supports: reach further back and
   CAGR measures recovery from 2020, not structural growth.
4. **Load factor above the anchor band means constraint.** High load factors can
   also mean disciplined capacity management by a dominant carrier. The proxy
   cannot distinguish these.
5. **Slot control is a binary.** In reality FAA Level 2 and Level 3 designations
   differ materially. Treated as one flag.
6. **Every airport is scored on one national scale.** A constrained regional
   airport and a constrained mega-hub are scored by the same anchors.

---

## 5. Limitations

**Of the data**

- Live METAR conditions are point-in-time context and carry no historical depth
  here: the app reads the latest observation, not a climatology. A question like
  "how often is BOS below minimums in January" is out of scope for this
  integration, and would need the AWC's historical METAR archive.
- No delay minutes, so no true congestion measure — hence the explicit
  redefinition of "congestion" as throughput per unit of capacity, stated in
  every congestion response.
- No fare, yield or revenue data. Nothing here speaks to whether an expansion
  would *pay*.
- No catchment demographics, no ground-access constraints, no local economic
  indicators.
- No existing capital plans. An airport already mid-expansion scores identically
  to one that has never broken ground — arguably the single largest practical
  gap for real use.
- No environmental, political, noise-ordinance or land-availability constraints,
  which are frequently the binding constraint in practice.
- No cargo. ANC in particular is one of the world's busiest cargo airports and
  this tool sees only its passenger operations.

**Of the model**

- The weights are a judgement call, not a fitted model. There is no labelled
  training set of "airports that should have expanded", so the weights encode a
  reasonable prior rather than an estimated relationship.
- Anchor bands are calibrated to mid-2020s US airports and would need revisiting
  for another era or another country.
- The score is a **ranking aid**. The difference between 61 and 64 is noise; the
  difference between 61 and 38 is signal.

**Of the AI layer**

- Claude can still misroute a question to the wrong tool, or misread a
  breakdown. It cannot invent a number, which is the failure that would matter,
  but tool-selection errors remain possible. The rendered tool calls under every
  answer exist so a reviewer can catch them.
- Conversation history is replayed as plain text; earlier tool results are not
  re-sent. This keeps requests small and is right for follow-ups like *"which
  one is better?"*, but a question depending on the precise figures of a
  much earlier turn will cause a re-fetch rather than a recall.

---

## 6. Tradeoffs

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Where arithmetic lives | Python only | Let the LLM compute | Non-negotiable for an investment tool. A confident wrong number is worse than no number. |
| Score normalisation | Fixed anchor bands | Cohort percentiles | Stable, portable, hand-auditable. Costs us a judgement call on the anchors. |
| Unmet demand | Separate labelled proxy | Fold into the score | Bundling a proxy into a headline number launders its uncertainty. |
| Agent loop | Manual Messages loop | SDK `tool_runner` (beta) | Needed raw tool calls surfaced to the UI; avoids a beta dependency. |
| No-key behaviour | Deterministic router | Error out | The app must be demoable without a key, and correctness must not depend on the LLM. |
| Data source | Extract-based BTS provider | Fake a REST API | Honest about how DOT actually distributes T-100. |
| Cache | In-process dict + JSON on disk | Redis | Zero infrastructure for a two-service deploy. Swap it if this ever scales horizontally. |
| Dataset breadth | 66 airports | The 9 named ones | Rankings need a cohort. Nine airports cannot produce a meaningful national ranking. |
| Live weather | A separate `app/services/` package | A field on the metrics bundle | Structural isolation beats a naming convention. Analytics cannot import what it does not depend on, so weather can never leak into a score. |
| Weather failures | Returned as a `status`, never raised | HTTP 5xx on outage | Conditions are supplementary. A dead third party should degrade one panel, not fail the request. |
| ICAO mapping | Enumerated table plus a K-prefix rule | Ask the model, or a heuristic | Same discipline as region membership: the mapping is data, not a recollection. |
| Metric caching | Memoized per (dataset, threshold) | Recompute per request | The metric frame is a pure function of its inputs; recomputing it per request is wasted work. |
| Frontend | Static HTML/CSS/ES modules | Streamlit, or React + a bundler | No build step, no node toolchain, and it deploys as files inside the API service. A dashboard this size does not need a framework or a compile step. |
| Frontend state | In-memory JS object | A store, or a database | Sessions are per-user and disposable; persistence would be scope creep. |
| Markdown rendering | ~120 lines in `js/markdown.js` | `marked` from a CDN | The agent emits a known, narrow subset. A focused renderer avoids a CDN that may be unreachable and lets us escape HTML before any markup is produced. |
| Scoring window | 2022–2024 | 2019 baseline | A 2019 baseline makes every CAGR a pandemic-recovery artefact. |

---

## 7. What would come next

Roughly in order of value per unit of effort:

1. **Wire a real T-100 extract** and validate the score distribution against it.
   The provider is written; it needs a data drop and a calibration pass.
2. **Peak-hour passenger estimates** from schedule data — the single biggest
   improvement to terminal-load fidelity.
3. **Existing capital plans** as a suppressor, so airports mid-expansion stop
   surfacing as candidates.
4. **FAA OPSNET delay data**, which would let "congestion" mean congestion.
5. **Sensitivity analysis in the UI** — let an analyst move the weights and watch
   the ranking respond. The scoring layer already supports it; it needs controls.
6. **Cargo volumes**, without which ANC and similar airports are badly
   mischaracterised.
