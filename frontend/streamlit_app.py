"""Airport Investment Intelligence — Streamlit dashboard.

Run locally with::

    streamlit run frontend/streamlit_app.py

The backend URL comes from ``API_BASE_URL``; nothing is hardcoded for
production. All figures shown here are computed by the FastAPI service.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api_client import AirportAPIClient, APIError  # noqa: E402

st.set_page_config(
    page_title="Airport Investment Intelligence",
    page_icon="🛫",
    layout="wide",
    initial_sidebar_state="expanded",
)

EXAMPLE_QUESTIONS = [
    "Which airports in New England are strong candidates for terminal expansion?",
    "Compare LAX and SNA congestion levels.",
    "What percentage of flights from ANC are long-haul?",
    "What is the unmet flight demand at SFO and why?",
]

STATUS_STYLE = {
    "live": ("🟢", "LIVE", "Fetched from the upstream US DOT / BTS source."),
    "cached": ("🟡", "CACHED", "A stored copy of a previous live pull."),
    "demo": ("🔵", "DEMO", "Bundled offline snapshot — not live data."),
}

CSS = """
<style>
  .block-container {padding-top: 3.5rem; max-width: 1400px;}
  .aii-header {border-bottom: 1px solid rgba(128,128,128,.25); padding-bottom: .75rem;
               margin-bottom: 1.25rem;}
  .aii-title {font-size: 1.9rem; font-weight: 650; margin: 0; letter-spacing: -.02em;}
  .aii-sub {opacity: .7; font-size: .95rem; margin-top: .15rem;}
  .aii-badge {display: inline-block; padding: .18rem .6rem; border-radius: 999px;
              font-size: .78rem; font-weight: 600; letter-spacing: .04em;
              border: 1px solid rgba(128,128,128,.35);}
  .aii-note {font-size: .82rem; opacity: .68; line-height: 1.45;}
  div[data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>
"""


# ---------------------------------------------------------------------------
# Client and cached reads
# ---------------------------------------------------------------------------
@st.cache_resource
def get_client() -> AirportAPIClient:
    return AirportAPIClient(os.getenv("API_BASE_URL", "http://localhost:8000"))


@st.cache_data(ttl=300, show_spinner=False)
def load_overview() -> dict[str, Any]:
    return get_client().overview()


@st.cache_data(ttl=300, show_spinner=False)
def load_airports() -> list[dict[str, Any]]:
    return get_client().airports()["airports"]


@st.cache_data(ttl=300, show_spinner=False)
def load_regions() -> list[dict[str, Any]]:
    return get_client().regions()["regions"]


@st.cache_data(ttl=300, show_spinner=False)
def load_ranking(region: str | None, limit: int, sort_by: str) -> dict[str, Any]:
    return get_client().rank(region=region, limit=limit, sort_by=sort_by)


@st.cache_data(ttl=300, show_spinner=False)
def load_score(iata: str) -> dict[str, Any]:
    return get_client().score(iata)


@st.cache_data(ttl=300, show_spinner=False)
def load_comparison(iatas: tuple[str, ...], view: str) -> dict[str, Any]:
    return get_client().compare(list(iatas), view=view)


# ---------------------------------------------------------------------------
# Shared chrome
# ---------------------------------------------------------------------------
def data_badge(status: str) -> str:
    icon, label, _ = STATUS_STYLE.get(status, ("⚪", status.upper(), ""))
    return f'{icon} <span class="aii-badge">{label} DATA</span>'


def render_header(overview: dict[str, Any] | None) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    status = (overview or {}).get("provenance", {}).get("status", "demo")
    st.markdown(
        f'<div class="aii-header">'
        f'<p class="aii-title">Airport Investment Intelligence</p>'
        f'<p class="aii-sub">Terminal expansion &amp; modernization screening for '
        f'US airports &nbsp;·&nbsp; {data_badge(status)}</p></div>',
        unsafe_allow_html=True,
    )


def render_sidebar(overview: dict[str, Any] | None) -> None:
    with st.sidebar:
        st.subheader("Data source")
        if not overview:
            st.error("Backend unreachable.")
            st.caption(f"API_BASE_URL = {get_client().base_url}")
            return

        prov = overview["provenance"]
        icon, label, blurb = STATUS_STYLE.get(prov["status"], ("⚪", "UNKNOWN", ""))
        st.markdown(f"### {icon} {label}")
        st.caption(prov["source_name"])
        if prov["status"] == "demo":
            st.warning("Bundled demo data. Figures illustrate the methodology and "
                       "are **not** live BTS output.", icon="⚠️")
        elif prov["status"] == "cached":
            st.info(blurb, icon="ℹ️")
        else:
            st.success(blurb, icon="✅")

        st.markdown('<p class="aii-note">' + prov["description"] + "</p>",
                    unsafe_allow_html=True)
        if prov.get("notes"):
            st.markdown(f'<p class="aii-note"><em>{prov["notes"]}</em></p>',
                        unsafe_allow_html=True)

        st.divider()
        st.subheader("Coverage")
        st.metric("Airports", overview["airport_count"])
        st.metric("Non-stop segments", f"{overview['route_count']:,}")
        years = overview["years"]
        st.metric("Years", f"{years[0]}–{years[-1]}")
        st.caption(
            f"Long-haul threshold: **{overview['long_haul_threshold_miles']:,.0f} "
            "statute miles** (non-stop great-circle distance)."
        )

        st.divider()
        st.caption(f"API: `{get_client().base_url}`")
        if st.button("Refresh data", use_container_width=True):
            st.cache_data.clear()
            try:
                get_client().data_status(refresh=True)
            except APIError as exc:
                st.error(str(exc))
            st.rerun()

        st.divider()
        st.markdown(
            '<p class="aii-note"><strong>Screening tool, not a valuation model.</strong> '
            "Scores rank relative opportunity from public operating data. They contain "
            "no construction costs, capital structure, traffic forecasts or returns."
            "</p>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Tab: chat
# ---------------------------------------------------------------------------
def render_chat() -> None:
    st.subheader("Ask the analyst agent")
    st.caption(
        "Claude selects deterministic Python tools, runs them, and explains the "
        "results. It never computes a statistic itself. Follow-up questions keep "
        "the conversation's context."
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending" not in st.session_state:
        st.session_state.pending = None

    with st.container():
        st.markdown("**Example questions**")
        cols = st.columns(2)
        for index, question in enumerate(EXAMPLE_QUESTIONS):
            if cols[index % 2].button(question, key=f"ex_{index}",
                                      use_container_width=True):
                st.session_state.pending = question

    if st.session_state.messages and st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

    st.divider()

    # The input is read before the transcript is drawn so the layout stays in
    # the same order on every rerun (examples -> input -> conversation).
    typed = st.chat_input("e.g. Which one is a better expansion candidate?")
    question = typed or st.session_state.pending
    st.session_state.pending = None

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            meta = message.get("meta")
            if meta:
                render_answer_meta(meta)

    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    history = [{"role": m["role"], "content": m["content"]}
               for m in st.session_state.messages[:-1]]
    with st.chat_message("assistant"):
        with st.spinner("Running analytics…"):
            try:
                result = get_client().chat(question, history)
            except APIError as exc:
                st.error(str(exc))
                st.session_state.messages.pop()
                return
        st.markdown(result["answer"])
        render_answer_meta(result)

    st.session_state.messages.append({
        "role": "assistant",
        "content": result["answer"],
        "meta": result,
    })


def render_answer_meta(result: dict[str, Any]) -> None:
    """Show provenance for an answer: which tools ran, and on what data."""
    status = result.get("data_status", "demo")
    icon, label, _ = STATUS_STYLE.get(status, ("⚪", status.upper(), ""))
    engine = (f"Claude ({result['model']})" if result.get("used_llm")
              else "Analytics engine (no AI narration)")
    st.caption(f"{icon} {label} data · {engine}")

    if result.get("degraded") and result.get("degraded_reason"):
        st.info(result["degraded_reason"], icon="ℹ️")

    calls = result.get("tool_calls") or []
    if calls:
        with st.expander(f"Tools used ({len(calls)}) — every figure above came from these"):
            for call in calls:
                st.markdown(f"**`{call['tool']}`** · input `{call['input']}`")
                st.json(call["output"], expanded=False)


# ---------------------------------------------------------------------------
# Tab: rankings
# ---------------------------------------------------------------------------
SORT_OPTIONS = {
    "expansion_score": "Airport Expansion Score",
    "unmet_demand_index": "Unmet Demand Proxy",
    "passenger_cagr": "Passenger growth (CAGR)",
    "flight_cagr": "Flight growth (CAGR)",
    "load_factor": "Seat utilization",
    "passengers_per_gate": "Passengers per gate",
    "departures_per_runway": "Departures per runway",
    "long_haul_departure_share": "Long-haul departure share",
    "passengers": "Passengers",
}


def render_rankings(regions: list[dict[str, Any]]) -> None:
    st.subheader("Expansion candidate rankings")
    st.caption("Region membership comes from the airport metadata table, not the model.")

    left, middle, right = st.columns([2, 2, 1])
    region_names = ["All US airports"] + [r["region"] for r in regions]
    region = left.selectbox("Region", region_names, index=0)
    sort_by = middle.selectbox("Rank by", list(SORT_OPTIONS),
                               format_func=lambda k: SORT_OPTIONS[k])
    limit = right.slider("Show", min_value=3, max_value=25, value=10)

    query = None if region == "All US airports" else region
    try:
        data = load_ranking(query, limit, sort_by)
    except APIError as exc:
        st.error(str(exc))
        return

    if not data["results"]:
        st.warning(data.get("note") or "No airports matched that filter.")
        return

    frame = pd.DataFrame(data["results"])
    # Percentages are pre-formatted rather than left to column_config, which
    # varies its decimal places per value and makes the column hard to scan.
    display = pd.DataFrame({
        "#": frame["rank"],
        "Airport": frame["iata"] + " — " + frame["name"],
        "Region": frame["region"],
        "Score": frame["expansion_score"],
        "Rating": frame["rating"],
        "Load factor": frame["load_factor"].map("{:.1%}".format),
        "Pax growth": frame["passenger_cagr"].map("{:.2%}".format),
        "Pax / gate": frame["passengers_per_gate"].map("{:,.0f}".format),
        "Long-haul": frame["long_haul_departure_share"].map("{:.1%}".format),
    })
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Expansion score", min_value=0, max_value=100, format="%.1f"),
        },
    )
    st.caption(f"Ranked by {data['sort_label']} · {data['candidate_pool']} airports in "
               f"scope · {data['latest_year']} data.")

    # Zero-padded rank prefix keeps the chart in ranking order; Streamlit
    # otherwise sorts categorical indexes alphabetically.
    stacked = pd.DataFrame(
        [c["component_points"] for c in data["results"]],
        index=[f"{r['rank']:02d}  {r['iata']}" for r in data["results"]],
    ).rename(columns=lambda k: k.replace("_", " ").title())
    st.markdown("**Score composition** — each bar sums to the airport's total score.")
    st.bar_chart(stacked, height=320)


# ---------------------------------------------------------------------------
# Tab: comparison
# ---------------------------------------------------------------------------
def render_comparison(airports: list[dict[str, Any]]) -> None:
    st.subheader("Airport comparison")
    labels = {f"{a['iata']} — {a['name']}": a["iata"] for a in airports}
    default = [k for k in labels if k.startswith(("LAX", "SNA"))][:2]

    chosen = st.multiselect("Select two or more airports", list(labels),
                            default=default or list(labels)[:2], max_selections=4)
    if len(chosen) < 2:
        st.info("Pick at least two airports.")
        return

    codes = tuple(labels[c] for c in chosen)
    view = st.radio("View", ["full", "congestion"], horizontal=True,
                    format_func=lambda v: "Full comparison" if v == "full"
                    else "Congestion only")

    try:
        data = load_comparison(codes, view)
    except APIError as exc:
        st.error(str(exc))
        return

    result = data["result"]
    if view == "congestion":
        st.caption(result["definition"])
        frame = pd.DataFrame(result["airports"])
        st.dataframe(
            frame[["iata", "name", "runways", "gates", "departures_per_runway",
                   "passengers_per_gate", "load_factor", "slot_controlled"]]
            .rename(columns={
                "iata": "IATA", "name": "Airport", "runways": "Runways",
                "gates": "Gates", "departures_per_runway": "Departures / runway",
                "passengers_per_gate": "Passengers / gate",
                "load_factor": "Load factor", "slot_controlled": "Slot-controlled"}),
            hide_index=True, use_container_width=True,
            column_config={
                "Load factor": st.column_config.NumberColumn(format="percent"),
                "Departures / runway": st.column_config.NumberColumn(format="localized"),
                "Passengers / gate": st.column_config.NumberColumn(format="localized"),
            },
        )
        col_a, col_b = st.columns(2)
        col_a.metric("Busiest airfield (per runway)", result["busiest_airfield"])
        col_b.metric("Busiest terminal (per gate)", result["busiest_terminal"])
        return

    st.success(result["verdict"])
    rows = []
    for row in result["table"]:
        record = {"Metric": row["label"]}
        for code in result["iatas"]:
            record[code] = format_metric(row["values"][code], row["kind"])
        record["Leader"] = row["leader"]
        rows.append(record)
    # Size the table to its contents so the last row is not clipped.
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 height=(len(rows) + 1) * 35 + 3)
    st.caption("The Unmet Demand Proxy row is a screening proxy, not a measurement "
               "of true latent demand.")

    st.markdown("#### Score breakdown by airport")
    cols = st.columns(len(result["airports"]))
    for column, entry in zip(cols, result["airports"]):
        with column:
            score = entry["score"]
            st.metric(f"{entry['iata']} — {score['rating']}",
                      f"{score['expansion_score']:.1f}")
            for component in score["components"]:
                st.progress(
                    min(component["points"] / max(component["max_points"], 1e-9), 1.0),
                    text=f"{component['label']}: {component['points']:.1f}"
                         f"/{component['max_points']:.0f}",
                )


def format_metric(value: float, kind: str) -> str:
    if kind == "pct":
        return f"{value * 100:.2f}%"
    if kind == "pp":
        return f"{value * 100:+.2f} pp"
    if kind == "count":
        return f"{value:,.0f}"
    return f"{value:,.1f}"


# ---------------------------------------------------------------------------
# Tab: expansion score
# ---------------------------------------------------------------------------
def render_score(airports: list[dict[str, Any]]) -> None:
    st.subheader("Airport Expansion Score")
    labels = {f"{a['iata']} — {a['name']}": a["iata"] for a in airports}
    default_index = next((i for i, k in enumerate(labels) if k.startswith("SFO")), 0)
    chosen = st.selectbox("Airport", list(labels), index=default_index)

    try:
        data = load_score(labels[chosen])
        metrics = get_client().metrics(labels[chosen])
    except APIError as exc:
        st.error(str(exc))
        return

    head = st.columns([1, 1, 2])
    head[0].metric("Expansion score", f"{data['expansion_score']:.1f} / 100")
    head[1].metric("Rating", data["rating"])
    head[2].metric("Strongest drivers", ", ".join(data["top_drivers"]))

    st.progress(min(data["expansion_score"] / 100, 1.0))
    st.caption(data["methodology"])

    st.markdown("#### Why this airport scored what it scored")
    breakdown = pd.DataFrame([
        {
            "Component": c["label"],
            "Weight": f"{c['weight_pct']:.0f}%",
            "Value": c["raw_display"],
            "Anchor band": f"{c['anchor_low']:g} → {c['anchor_high']:g}",
            "Sub-score": c["sub_score"],
            "Points": f"{c['points']:.1f} / {c['max_points']:.0f}",
        }
        for c in data["components"]
    ])
    st.dataframe(breakdown, hide_index=True, use_container_width=True,
                 column_config={"Sub-score": st.column_config.ProgressColumn(
                     "Sub-score", min_value=0, max_value=1, format="%.2f")})

    for component in data["components"]:
        st.markdown(f"- **{component['label']}** ({component['points']:.1f} pts): "
                    f"{component['explanation']}")

    st.divider()
    left, right = st.columns(2)

    with left:
        st.markdown("#### Unmet Demand Proxy")
        proxy = metrics["unmet_demand"]
        st.metric("Index", f"{proxy['unmet_demand_index']:.1f} / 100")
        st.caption(proxy["interpretation"])
        st.dataframe(
            pd.DataFrame([{"Signal": s["label"], "Value": s["raw_display"],
                           "Weight": f"{s['weight'] * 100:.0f}%",
                           "Points": f"{s['points']:.1f}"} for s in proxy["signals"]]),
            hide_index=True, use_container_width=True,
        )
        st.warning(proxy["disclaimer"], icon="⚠️")

    with right:
        st.markdown("#### Long-haul connectivity")
        lh = metrics["long_haul"]
        st.metric("Long-haul departure share",
                  f"{lh['long_haul_departure_share'] * 100:.1f}%")
        st.caption(f"Defined as {lh['definition']}. "
                   f"{lh['long_haul_route_count']} of {lh['route_count']} non-stop "
                   f"destinations qualify; departure-weighted average stage length is "
                   f"{lh['average_stage_length_miles']:,.0f} miles.")
        if lh["top_long_haul_routes"]:
            st.dataframe(
                pd.DataFrame([{"Destination": r["destination"],
                               "Distance (mi)": r["distance_miles"],
                               "Departures": r["departures"]}
                              for r in lh["top_long_haul_routes"]]),
                hide_index=True, use_container_width=True,
            )
        else:
            st.info("No non-stop segment from this airport clears the long-haul "
                    "threshold.")

    st.divider()
    st.markdown("#### Underlying metrics")
    core = metrics["metrics"]
    grid = st.columns(4)
    grid[0].metric(f"Passengers ({core['latest_year']})", f"{core['passengers']:,}")
    grid[1].metric("Departures", f"{core['flights']:,}")
    grid[2].metric("Load factor", f"{core['load_factor'] * 100:.1f}%")
    grid[3].metric("Passenger CAGR", f"{core['passenger_cagr'] * 100:.2f}%")
    grid = st.columns(4)
    grid[0].metric("Runways", core["runways"])
    grid[1].metric("Gates", core["gates"])
    grid[2].metric("Departures / runway", f"{core['departures_per_runway']:,.0f}")
    grid[3].metric("Passengers / gate", f"{core['passengers_per_gate']:,.0f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        overview = load_overview()
    except APIError as exc:
        render_header(None)
        st.error(
            f"Cannot reach the backend at `{get_client().base_url}`.\n\n{exc}\n\n"
            "Start it with `uvicorn app.main:app --port 8000`, or set `API_BASE_URL`."
        )
        render_sidebar(None)
        return

    render_header(overview)
    render_sidebar(overview)

    try:
        airports = load_airports()
        regions = load_regions()
    except APIError as exc:
        st.error(str(exc))
        return

    chat_tab, rank_tab, compare_tab, score_tab = st.tabs(
        ["💬 Chat", "📊 Rankings", "⚖️ Comparison", "🎯 Expansion score"]
    )
    with chat_tab:
        render_chat()
    with rank_tab:
        render_rankings(regions)
    with compare_tab:
        render_comparison(airports)
    with score_tab:
        render_score(airports)


if __name__ == "__main__":
    main()
