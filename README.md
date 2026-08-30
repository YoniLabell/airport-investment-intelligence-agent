# Airport Investment Intelligence

An analyst tool for screening **US airports as candidates for terminal expansion
or modernization**. Ask a question in plain English; get a defensible number
with the arithmetic shown.

> **This is an investment *screening* tool, not a financial valuation model.**
> It ranks relative opportunity from public operating data. It contains no
> construction costs, capital structure, traffic forecasts, IRR or NPV. Use it
> to decide which airports deserve a real model — not to replace one.

---

## What it does

Four questions it is built to answer:

| Question | What happens |
|---|---|
| *Which airports in New England are strong candidates for terminal expansion?* | Filters the six New England states from the airport metadata table, scores every airport, returns a ranked table |
| *Compare LAX and SNA congestion levels.* | Departures per runway, passengers per gate, seat utilization, slot control — side by side |
| *What percentage of flights from ANC are long-haul?* | Distance-filters ANC's non-stop segments at 2,500 miles and returns the departure, seat and passenger shares |
| *What is the unmet flight demand at SFO and why?* | Builds the Unmet Demand **Proxy** from four observable signals and explains each one |

Follow-ups work: ask *"Compare LAX and SNA"*, then *"which one is a better
expansion candidate?"* and the second question resolves against the first.

---

## The core architectural rule: the LLM does not do arithmetic

```
user question
    │
    ▼
Claude  ──chooses tools──►  deterministic Python (pandas)
    │                              │
    │                         structured JSON
    ▼                              │
explanation  ◄─────────────────────┘
```

Claude picks which tools to call and writes the prose. **Every number in an
answer came out of a Python function**, and the UI shows you exactly which tool
calls produced it. Claude is forbidden by system prompt from estimating a
statistic, doing arithmetic on tool output, or deciding which airports are in
New England — that last one comes from the metadata table, not the model's
memory.

The deterministic layer (`app/analytics/`) exposes:

| Function | Purpose |
|---|---|
| `get_airport_metrics` | Passengers, seats, departures, load factor, per-gate and per-runway throughput, growth rates |
| `compare_airports` / `congestion_comparison` | Head-to-head with explicit deltas and a leader per row |
| `rank_airports` | Ranking by any of nine metrics, optionally scoped to a region or state |
| `long_haul_breakdown` | Long-haul share and the routes driving it |
| `demand_pressure` | How hard the aircraft and airfield are working |
| `capacity_constraint` | How constrained the terminal is |
| `unmet_demand_proxy` | The 0–100 proxy index and its four signals |
| `expansion_score` | The 0–100 Airport Expansion Score with a full breakdown |
| `get_airport_conditions` | Current METAR conditions — **live context only, never scored** |

Every one of these is reachable from the HTTP API as well as from the chat agent.

---

## How the Expansion Score works

A transparent 0–100 index:

| Weight | Component | Built from |
|---:|---|---|
| **30%** | Demand pressure | 60% seat utilization (load factor) + 40% departures per runway |
| **25%** | Passenger growth | Passenger CAGR across the dataset's year span |
| **20%** | Capacity constraint | 55% passengers per gate + 20% slot control + 25% capacity-growth lag |
| **15%** | Flight growth | Departure CAGR |
| **10%** | Long-haul connectivity | Share of departures ≥ 2,500 miles |

Each component is scaled 0–1 against a **fixed anchor band** — not against the
other airports in the list. So a score means the same thing whether you rank 9
airports or 900, and adding an airport never silently moves everyone else's
score. The anchors live in one dictionary, `ANCHORS` in
[`app/analytics/scoring.py`](app/analytics/scoring.py):

```python
"load_factor":               (0.76, 0.88)     # 0.76 scores 0, 0.88 scores 1
"departures_per_runway":     (5_000, 90_000)
"passengers_per_gate":       (60_000, 400_000)
"passenger_cagr":            (0.005, 0.07)
"flight_cagr":               (0.005, 0.055)
"capacity_growth_lag":       (-0.005, 0.02)
"long_haul_departure_share": (0.0, 0.25)
```

`score = 100 × Σ (weight × sub_score)`, and 0–39 is *Low priority*, 40–54 *Watch
list*, 55–69 *Promising*, 70+ *Strong candidate*.

**Every score shows its work.** `GET /api/airports/AUS/score` returns, for each
of the five components, the raw value, the anchor band it was scaled against,
the resulting sub-score, the weight, the points earned out of the points
available, and a sentence explaining it in English. The test suite recomputes a
score by hand from the published formula to prove the two agree.

### Definitions, stated once

- **Long-haul** — a non-stop segment whose great-circle distance is
  **≥ 2,500 statute miles** (`LONG_HAUL_MILES`). Purely distance-based; it is
  *not* a domestic/international split. SNA has a 0% long-haul share because
  none of its non-stops clear the threshold — a genuine characteristic of the
  airport, not missing data.
- **Congestion** — throughput per unit of physical capacity: departures per
  runway, passengers per gate, seat utilization. This dataset carries no delay
  minutes, so the tool never makes a claim about delays.
- **Unmet demand** — see below.

### The Unmet Demand Proxy is a proxy

Public aviation datasets record flights that were **flown** and passengers who
**flew**. They contain no record of the trips people wanted to take but could
not book, or of fares that priced demand out. True latent demand is not
measurable from this data.

So the tool publishes a clearly-labelled 0–100 **proxy** built from four
observable signals:

| Weight | Signal | Reads as |
|---:|---|---|
| 35% | Seat utilization pressure | Aircraft leave close to full; incremental demand has nowhere to sit |
| 25% | Passenger growth | Demand is expanding |
| 25% | Capacity growth lagging passenger growth | Seats added more slowly than passengers arriving — the clearest observable sign of a supply ceiling |
| 15% | Flight growth | Airlines are adding departures, or cannot |

The disclaimer travels with the number through the API, the chat answer and the
UI. It is a screening flag, not an estimate of latent passengers.

---

## Data

**Source of record: US DOT / Bureau of Transportation Statistics T-100 Segment.**

The data layer is deliberately swappable — `app/data/` defines an
`AirportDataProvider` protocol, and the rest of the app only talks to
`DataRepository`. Two providers ship:

- **`BTSDataProvider`** — reads T-100 Segment records, either from a directory
  of downloaded TranStats CSV/ZIP extracts (`BTS_LOCAL_EXTRACT_DIR`, the
  realistic path — TranStats has no keyless JSON API, it exports filtered CSVs)
  or from a direct URL (`BTS_T100_URL`), fetched with a hard timeout. It
  aggregates segments into per-airport annual volumes and per-segment distances.
- **`DemoDataProvider`** — the bundled offline snapshot in `app/data/seed/`,
  covering 66 US airports and 919 non-stop segments across 2022–2024.

The repository tries them in order and **always tells you which one won**:

```
live upstream  →  cached copy of a previous live pull  →  bundled demo
```

Every API response and every chat answer carries a status of `live`, `cached` or
`demo`, and the UI shows it as a badge in the header and sidebar. **Demo data is
never presented as live.** If the external source fails, the app stays fully
demoable and says plainly that it is running on bundled data.

### Live operational context: AviationWeather.gov

Separately from the historical analytics above, the app reads **current**
conditions from the NOAA/NWS Aviation Weather Center's public, keyless JSON API:

```
GET https://aviationweather.gov/api/data/metar?ids=KSFO&format=json
```

`app/services/aviation_weather.py` converts IATA to ICAO (SFO → KSFO, and the
enumerated Alaska/Hawaii/territory exceptions such as ANC → PANC, HNL → PHNL),
calls the API with a hard timeout, and returns a structured observation: flight
category, visibility, wind, present weather, cloud layers and ceiling,
temperature, altimeter, observation time and age, and the raw METAR.

**The two sources do different jobs, and the split is enforced in code:**

| | Source | Role |
|---|---|---|
| **Historical** | US DOT / BTS T-100 | Every metric, ranking, Unmet Demand Proxy and Expansion Score |
| **Live** | AviationWeather.gov | Operational context only — *never* an input to any score |

Today's fog does not make an airport a better expansion candidate. Every
conditions payload carries `used_in_scoring: false` and a `source_role` string
saying so; the agent's system prompt forbids using conditions as investment
evidence; and a test asserts an airport's score is byte-identical whether the
weather feed is healthy, degraded or reporting LIFR.

If AviationWeather.gov is slow or down, the endpoint still returns **HTTP 200**
with `status` set to `unavailable` — an outage degrades one panel, never a
request. Statuses are `ok`, `no_report`, `unsupported`, `unavailable` and
`disabled`. Set `ENABLE_LIVE_WEATHER=false` to switch the integration off
entirely.

### About the bundled demo data

Airport-level passenger, seat and flight totals are **rounded approximations of
publicly reported FAA/BTS figures**; the route table is a **synthesized**
stand-in for the structure of a T-100 segment extract, with distances computed
by real haversine from real airport coordinates. It is there to demonstrate the
methodology, not to support an investment decision. `scripts/generate_seed_data.py`
regenerates it deterministically and is the provenance record for every value.

To run on real data: download a T-100 Segment extract from
<https://transtats.bts.gov/DL_SelectFields.aspx>, drop the CSVs in a folder, set
`BTS_LOCAL_EXTRACT_DIR=/path/to/that/folder` and `USE_DEMO_DATA=false`. The
status badge flips to **LIVE**.

---

## Running locally

```bash
git clone <this-repo>
cd airport-investment-intelligence-agent

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then add your ANTHROPIC_API_KEY (optional)
```

One process serves everything:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open:

| URL | What it is |
|---|---|
| <http://localhost:8000> | **The dashboard** |
| <http://localhost:8000/docs> | Interactive API docs |
| <http://localhost:8000/health> | Health probe |
| <http://localhost:8000/api> | JSON service banner |

The dashboard is plain HTML, CSS and ES modules in `frontend/`, served by the
same FastAPI app. There is no build step, no bundler and no node toolchain —
edit a file and reload the page.

Run the tests:

```bash
pytest                              # 232 tests
```

Regenerate the demo dataset:

```bash
python scripts/generate_seed_data.py
```

> **No Anthropic key?** Everything still works. Questions are routed by a
> deterministic intent router straight to the same analytics functions, and the
> answer is labelled *"Analytics engine (no AI narration)"* so nobody mistakes it
> for the AI response. You lose the prose, not the numbers.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Service banner and endpoint index |
| `GET` | `/health` | Liveness. **200 with no external calls, ever** |
| `GET` | `/api/airports?region=New England` | Covered airports, optionally filtered |
| `GET` | `/api/regions` | Regions and their member airports |
| `GET` | `/api/airports/{iata}/metrics` | Metrics + long-haul + unmet-demand proxy |
| `GET` | `/api/airports/{iata}/score` | Expansion Score with full breakdown |
| `GET` | `/api/airports/{iata}/conditions` | Current METAR conditions (live context, not analytics) |
| `GET` | `/api/data-status` | Live / cached / demo, with provenance |
| `GET` | `/api/overview` | Dataset coverage summary |
| `POST` | `/api/compare` | `{"iatas": ["LAX","SNA"], "view": "full"\|"congestion"}` |
| `POST` | `/api/rank` | `{"region": "New England", "limit": 5, "sort_by": "expansion_score"}` |
| `POST` | `/api/chat` | `{"message": "...", "history": [...]}` |

```bash
curl localhost:8000/health
curl "localhost:8000/api/airports?region=New%20England"
curl localhost:8000/api/airports/SFO/score
curl localhost:8000/api/airports/SFO/conditions
curl -X POST localhost:8000/api/rank \
  -H 'content-type: application/json' \
  -d '{"region":"New England","limit":5}'
curl -X POST localhost:8000/api/chat \
  -H 'content-type: application/json' \
  -d '{"message":"What percentage of flights from ANC are long-haul?"}'
```

---

## Deploying to Render

The repo ships a [`render.yaml`](render.yaml) blueprint for **one web service**
that serves both the API and the dashboard. The backend binds `$PORT`; nothing
is hardcoded.

1. Push this repository to GitHub.
2. In Render: **New → Blueprint**, pick the repo, let it read `render.yaml`.
3. Render creates **`airport-intelligence`**, running
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT` with health check `/health`.
4. Set the one secret: **Environment → `ANTHROPIC_API_KEY`**. It is declared
   `sync: false`, so Render prompts for it and it never enters git. Skip it and
   the app runs in deterministic mode.
5. Deploy, then verify `https://<your-service>.onrender.com/health` returns
   `{"status":"ok",...}`, `/docs` renders, and `/` shows the dashboard.

**Why one service and not two?** The original plan used a Streamlit frontend,
which needs its own Python runtime and therefore its own service. A static
HTML/JS dashboard does not: serving it from FastAPI means one deploy, one URL,
no cross-origin requests and no CORS to misconfigure. Fewer moving parts on a
free tier that sleeps.

**Free tier note.** Free services sleep after inactivity, so the first request
after a nap takes ~30s while the container wakes. The dashboard's 60-second
client timeout absorbs it.

### Splitting the dashboard onto its own service

`frontend/` is a self-contained static bundle, so you can host it on Render's
static-site CDN if you prefer. Add a second service to `render.yaml`:

```yaml
  - type: web
    name: airport-intelligence-ui
    runtime: static
    staticPublishPath: ./frontend
    # Tell the bundle where the API lives; app.js reads window.AII_API_BASE.
    buildCommand: >-
      printf 'window.AII_API_BASE=%s;' "\"https://$API_HOST\"" > frontend/config.js
    envVars:
      - key: API_HOST
        fromService: { type: web, name: airport-intelligence, property: host }
```

and add `<script src="/config.js"></script>` above the module script in
`index.html`. Keep `CORS_ALLOW_ORIGINS` set on the API, since the browser is
then making a cross-origin request.

## Project layout

```
app/
  config.py              env-driven settings (no secrets in source)
  main.py                FastAPI app, CORS, lifespan, error handler
  api/routes.py          HTTP layer — validation and shaping only
  models/schemas.py      pydantic request/response models
  data/
    dataset.py           AirportDataset + DataStatus/DataProvenance
    provider.py          AirportDataProvider protocol
    bts_provider.py      US DOT / BTS T-100 Segment
    demo_provider.py     bundled offline snapshot
    repository.py        live → cached → demo ladder
    cache.py             TTL cache (memory + disk)
    seed/                the bundled CSVs
  analytics/
    metrics.py           deterministic metrics, CAGR, long-haul
    scoring.py           Expansion Score + Unmet Demand Proxy
    ranking.py           ranking and comparison
    regions.py           region/state resolution
  services/              live operational context (kept out of analytics)
    icao.py              IATA -> ICAO station-code resolution
    aviation_weather.py  AviationWeather.gov METAR provider
  agent/
    prompts.py           the system prompt (incl. the arithmetic ban)
    tools.py             tool schemas + dispatch to analytics
    service.py           Claude tool-use loop
    fallback.py          deterministic router for no-key / API-down
frontend/               static dashboard, no build step
  index.html             page shell
  styles.css             design tokens + components, light and dark
  js/api.js              fetch client with timeouts and typed errors
  js/markdown.js         ~120-line renderer for the agent's answers
  js/app.js              tabs, rendering, state
tests/                   232 tests
scripts/                 seed-data generator
docs/DESIGN.md           architecture, methodology, limitations
```

---

## Example questions to try

- Which airports in New England are strong candidates for terminal expansion?
- Compare LAX and SNA congestion levels.
- What percentage of flights from ANC are long-haul?
- What is the unmet flight demand at SFO and why?
- Which one is a better expansion candidate? *(as a follow-up)*
- Rank the top 5 airports nationwide by unmet demand.
- Why did AUS score so highly?
- How does BOS compare with PVD?
- What are the current conditions at SFO? *(live METAR, not part of any score)*

See [`docs/DESIGN.md`](docs/DESIGN.md) for the architecture, methodology,
assumptions, limitations and tradeoffs.
