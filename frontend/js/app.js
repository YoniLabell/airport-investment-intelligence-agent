/**
 * Airport Investment Intelligence — dashboard controller.
 *
 * Plain ES modules, no framework and no build step. The rule this file follows
 * is the same one the backend follows: the UI never computes an analytic
 * figure. Every number rendered here arrived from the API already calculated;
 * this module only formats and lays it out.
 */

import { api, APIError } from './api.js';
import { renderMarkdown, escapeHtml } from './markdown.js';

/* ── DOM helpers ─────────────────────────────────────────────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Build an element from an HTML string (already escaped by the caller). */
function html(markup) {
  const template = document.createElement('template');
  template.innerHTML = markup.trim();
  return template.content.firstElementChild;
}

/* ── Formatting ──────────────────────────────────────────────────────── */
const fmt = {
  int:   (v) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }),
  dec:   (v, d = 1) => Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct:   (v, d = 1) => `${(Number(v) * 100).toFixed(d)}%`,
  pp:    (v, d = 2) => `${Number(v) >= 0 ? '+' : ''}${(Number(v) * 100).toFixed(d)} pp`,
  /** Format a comparison-table cell using the `kind` the API supplies. */
  byKind(value, kind) {
    if (kind === 'pct') return fmt.pct(value, 2);
    if (kind === 'pp') return fmt.pp(value);
    if (kind === 'count') return fmt.int(value);
    return fmt.dec(value, 1);
  },
};

const RATING_CLASS = {
  'Strong candidate': 'rating-strong',
  Promising: 'rating-promising',
  'Watch list': 'rating-watch',
  'Low priority': 'rating-low',
};

/** Fixed order so a component keeps its colour across every chart on the page. */
const COMPONENT_ORDER = [
  ['demand_pressure', 'Demand pressure'],
  ['passenger_growth', 'Passenger growth'],
  ['capacity_constraint', 'Capacity constraint'],
  ['flight_growth', 'Flight growth'],
  ['long_haul_connectivity', 'Long-haul connectivity'],
];

const EXAMPLE_QUESTIONS = [
  ['Regional screening', 'Which airports in New England are strong candidates for terminal expansion?'],
  ['Head-to-head', 'Compare LAX and SNA congestion levels.'],
  ['Route mix', 'What percentage of flights from ANC are long-haul?'],
  ['Demand signal', 'What is the unmet flight demand at SFO and why?'],
];

const SORT_OPTIONS = [
  ['expansion_score', 'Airport Expansion Score'],
  ['unmet_demand_index', 'Unmet Demand Proxy'],
  ['passenger_cagr', 'Passenger growth (CAGR)'],
  ['flight_cagr', 'Flight growth (CAGR)'],
  ['load_factor', 'Seat utilization'],
  ['passengers_per_gate', 'Passengers per gate'],
  ['departures_per_runway', 'Departures per runway'],
  ['long_haul_departure_share', 'Long-haul departure share'],
  ['passengers', 'Passengers'],
];

/* ── State ───────────────────────────────────────────────────────────── */
const state = {
  overview: null,
  airports: [],
  regions: [],
  chat: [],          // [{ role, content, meta? }]
  busy: false,
  compareView: 'full',
  loaded: { rank: false, compare: false, score: false },
};

/* ── Data status ─────────────────────────────────────────────────────── */
const STATUS_TEXT = {
  live: ['status-live', 'LIVE', 'Fetched from the upstream US DOT / BTS source.'],
  cached: ['status-cached', 'CACHED', 'A stored copy of a previous live pull.'],
  demo: ['status-demo', 'DEMO', 'Bundled offline snapshot — not live data.'],
};

function statusBadge(status) {
  const [cls, label] = STATUS_TEXT[status] || ['status-demo', String(status).toUpperCase()];
  return `<span class="badge"><span class="status-dot ${cls}"></span>${escapeHtml(label)} DATA</span>`;
}

/** One short line per status. The DEMO badge in the topbar carries the warning,
 *  so it is deliberately not repeated at length here. */
const STATUS_NOTE = {
  demo: 'Bundled offline snapshot. Illustrates the methodology; not live BTS output.',
  cached: 'Stored copy of a previous live pull.',
  live: 'Fetched from the upstream US DOT / BTS source.',
};

function renderDataStatus(provenance) {
  const [cls, label] = STATUS_TEXT[provenance.status] || ['status-demo', 'UNKNOWN'];
  $('#badge-slot').innerHTML = statusBadge(provenance.status);
  $('#data-status').innerHTML = `
    <div class="status-head"><span class="status-dot ${cls}"></span>${escapeHtml(label)}</div>
    <div class="status-source">${escapeHtml(provenance.source_name)}</div>
    <p class="fineprint">${escapeHtml(
      STATUS_NOTE[provenance.status] || provenance.description)}</p>`;
}

function renderCoverage(overview) {
  const years = overview.years;
  $('#kpi-airports').textContent = fmt.int(overview.airport_count);
  $('#kpi-routes').textContent = fmt.int(overview.route_count);
  $('#kpi-years').textContent = `${years[0]}–${years[years.length - 1]}`;
  $('#cov-regions').textContent = fmt.int((overview.regions || []).length);
  $('#cov-threshold').textContent =
    `\u2265 ${fmt.int(overview.long_haul_threshold_miles)} sm`;
}

/* ── Live aviation API status ────────────────────────────────────────── */
/** Statuses that mean the upstream answered us. */
const LIVE_REACHABLE = new Set(['ok', 'no_report']);

/**
 * Probe the existing conditions endpoint once to learn whether the live
 * aviation feed is usable. No new endpoint: this reuses the per-airport route
 * the Expansion Score tab already calls, and the backend caches the upstream
 * result, so the probe is close to free.
 */
async function loadLiveApiStatus() {
  const kpi = $('#kpi-live');
  const badge = $('#live-badge-slot');
  kpi.textContent = 'Checking…';
  kpi.className = 'kpi-value is-status kpi-off';

  const probe = pick(['SFO', 'LAX', 'BOS'], 0);
  if (!probe) { kpi.textContent = 'Unavailable'; return; }

  let status;
  try {
    status = (await api.conditions(probe)).status;
  } catch {
    status = 'unavailable';
  }

  if (LIVE_REACHABLE.has(status)) {
    kpi.className = 'kpi-value is-status kpi-ok';
    kpi.innerHTML = '<span class="status-dot status-live"></span>Connected';
    badge.innerHTML = '<span class="badge badge-live">'
      + '<span class="status-dot"></span>LIVE AVIATION API</span>';
    return;
  }

  // Only advertise the badge when the feed is actually available.
  badge.innerHTML = '';
  kpi.className = 'kpi-value is-status kpi-off';
  kpi.textContent = status === 'disabled' ? 'Disabled' : 'Unavailable';
}

function showGlobalError(message) {
  const el = $('#global-error');
  el.textContent = message;
  el.hidden = false;
}

/* ── Chat ────────────────────────────────────────────────────────────── */
function renderExamples() {
  const wrap = $('#examples');
  wrap.innerHTML = '';
  EXAMPLE_QUESTIONS.forEach(([eyebrow, question]) => {
    const button = html(`<button type="button" class="example">
        <span class="example-eyebrow">${escapeHtml(eyebrow)}</span>
        <span class="example-text">${escapeHtml(question)}</span>
      </button>`);
    button.addEventListener('click', () => askQuestion(question));
    wrap.append(button);
  });
}

function turnElement(role, bodyMarkup) {
  return html(`
    <div class="turn turn-${role}">
      <div class="avatar">${role === 'user' ? 'You' : 'AI'}</div>
      <div class="turn-body">${bodyMarkup}</div>
    </div>`);
}

function answerMeta(result) {
  const [, label] = STATUS_TEXT[result.data_status] || ['', String(result.data_status).toUpperCase()];
  const engine = result.used_llm
    ? `Claude (${escapeHtml(result.model || 'unknown model')})`
    : 'Analytics engine (no AI narration)';

  let markup = `<div class="meta-line">${escapeHtml(label)} data <span class="meta-sep">·</span> ${engine}</div>`;

  if (result.degraded && result.degraded_reason) {
    markup += `<div class="banner banner-info" style="margin:9px 0 0">${escapeHtml(result.degraded_reason)}</div>`;
  }

  const calls = result.tool_calls || [];
  if (calls.length) {
    const bodies = calls.map((call) => `
      <div class="tool-call">
        <div class="tool-name">${escapeHtml(call.tool)}(${escapeHtml(JSON.stringify(call.input))})</div>
        <pre class="tool-json">${escapeHtml(JSON.stringify(call.output, null, 2))}</pre>
      </div>`).join('');
    markup += `<details class="tools">
        <summary>Tools used (${calls.length}) — every figure above came from these</summary>
        ${bodies}
      </details>`;
  }
  return markup;
}

async function askQuestion(question) {
  const text = String(question || '').trim();
  if (!text || state.busy) return;

  state.busy = true;
  $('#chat-send').disabled = true;
  $('#chat-input').value = '';
  $('#chat-clear').hidden = false;

  const transcript = $('#transcript');
  $('#transcript-hint').hidden = true;
  transcript.append(turnElement('user', escapeHtml(text)));

  const pending = turnElement('assistant',
    '<div class="answer"><span class="thinking"><i></i><i></i><i></i> Running analytics…</span></div>');
  transcript.append(pending);
  pending.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // History is the prior turns only — the API appends the current message.
  const history = state.chat.map(({ role, content }) => ({ role, content }));

  try {
    const result = await api.chat(text, history);
    pending.querySelector('.turn-body').innerHTML =
      `<div class="answer">${renderMarkdown(result.answer)}</div>${answerMeta(result)}`;
    state.chat.push({ role: 'user', content: text });
    state.chat.push({ role: 'assistant', content: result.answer });
  } catch (err) {
    pending.querySelector('.turn-body').innerHTML =
      `<div class="banner banner-error" style="margin:0">${escapeHtml(err.message)}</div>`;
  } finally {
    state.busy = false;
    $('#chat-send').disabled = false;
    pending.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

/* ── Rankings ────────────────────────────────────────────────────────── */
function componentLegend() {
  return `<div class="legend">${COMPONENT_ORDER.map(([, label], i) =>
    `<span class="legend-item"><span class="legend-swatch legend-${i + 1}"></span>${escapeHtml(label)}</span>`
  ).join('')}</div>`;
}

function stackChart(rows) {
  const bars = rows.map((row) => {
    const points = COMPONENT_ORDER.map(([key]) => Number(row.component_points[key] || 0));
    // Show the API's own total, not the sum of the rounded component points —
    // otherwise the bar label can disagree with the table by 0.1.
    const total = row.expansion_score;
    const segments = points.map((value, i) =>
      `<span class="stack-seg seg-${i + 1}" style="width:${(value / 100) * 100}%"
             title="${escapeHtml(COMPONENT_ORDER[i][1])}: ${fmt.dec(value, 1)} pts"></span>`
    ).join('');
    return `<div class="stack-row">
        <span class="stack-label">${row.rank}. ${escapeHtml(row.iata)}</span>
        <span class="stack-bar">${segments}</span>
        <span class="stack-total">${fmt.dec(total, 1)}</span>
      </div>`;
  }).join('');

  return `<div class="card">
      <h3 class="card-title">Score composition — each bar sums to the airport's total score</h3>
      <div class="stack-chart">${bars}</div>
      ${componentLegend()}
    </div>`;
}

async function loadRankings() {
  const body = $('#rank-body');
  const region = $('#rank-region').value || null;
  const sortBy = $('#rank-sort').value;
  const limit = Number($('#rank-limit').value);
  body.innerHTML = '<p class="loading">Loading rankings…</p>';

  try {
    const data = await api.rank(region, limit, sortBy);
    if (!data.results.length) {
      body.innerHTML = `<div class="empty">${escapeHtml(data.note || 'No airports matched that filter.')}</div>`;
      return;
    }
    const rows = data.results.map((r) => `
      <tr>
        <td class="num">${r.rank}</td>
        <td><strong>${escapeHtml(r.iata)}</strong> — ${escapeHtml(r.name)}</td>
        <td>${escapeHtml(r.region)}</td>
        <td>
          <div class="scorebar">
            <span class="meter"><i style="width:${Math.min(r.expansion_score, 100)}%"></i></span>
            <span class="val">${fmt.dec(r.expansion_score, 1)}</span>
          </div>
        </td>
        <td><span class="rating ${RATING_CLASS[r.rating] || 'rating-low'}">${escapeHtml(r.rating)}</span></td>
        <td class="num">${fmt.pct(r.load_factor, 1)}</td>
        <td class="num">${fmt.pct(r.passenger_cagr, 2)}</td>
        <td class="num">${fmt.int(r.passengers_per_gate)}</td>
        <td class="num">${fmt.pct(r.long_haul_departure_share, 1)}</td>
      </tr>`).join('');

    body.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th class="num">#</th><th>Airport</th><th>Region</th><th>Expansion score</th>
            <th>Rating</th><th class="num">Load factor</th><th class="num">Pax growth</th>
            <th class="num">Pax / gate</th><th class="num">Long-haul</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="fineprint" style="margin:10px 0 16px">
        Ranked by ${escapeHtml(data.sort_label)} · ${fmt.int(data.candidate_pool)} airports in scope ·
        ${data.latest_year} data.
      </p>
      ${stackChart(data.results)}`;
  } catch (err) {
    body.innerHTML = `<div class="banner banner-error">${escapeHtml(err.message)}</div>`;
  }
}

/* ── Comparison ──────────────────────────────────────────────────────── */
function componentBars(score) {
  const rows = COMPONENT_ORDER.map(([key, label], i) => {
    const component = score.components.find((c) => c.key === key);
    if (!component) return '';
    const pct = (component.points / component.max_points) * 100;
    return `<div class="cmp-comp-row">
        <div class="lbl"><span>${escapeHtml(label)}</span>
          <span>${fmt.dec(component.points, 1)} / ${fmt.dec(component.max_points, 0)}</span></div>
        <span class="meter"><i class="seg-${i + 1}" style="width:${pct}%"></i></span>
      </div>`;
  }).join('');
  return `<div class="cmp-comp">${rows}</div>`;
}

async function loadComparison() {
  const body = $('#compare-body');
  const codes = [$('#cmp-a').value, $('#cmp-b').value, $('#cmp-c').value]
    .filter(Boolean)
    .filter((code, index, all) => all.indexOf(code) === index);

  if (codes.length < 2) {
    body.innerHTML = '<div class="empty">Pick two different airports to compare.</div>';
    return;
  }
  body.innerHTML = '<p class="loading">Comparing…</p>';

  try {
    const data = await api.compare(codes, state.compareView);
    const result = data.result;

    if (state.compareView === 'congestion') {
      const rows = result.airports.map((a) => `
        <tr>
          <td><strong>${escapeHtml(a.iata)}</strong> — ${escapeHtml(a.name)}</td>
          <td class="num">${a.runways}</td>
          <td class="num">${a.gates}</td>
          <td class="num">${fmt.int(a.departures_per_runway)}</td>
          <td class="num">${fmt.int(a.passengers_per_gate)}</td>
          <td class="num">${fmt.pct(a.load_factor, 1)}</td>
          <td>${a.slot_controlled ? 'yes' : 'no'}</td>
        </tr>`).join('');
      body.innerHTML = `
        <div class="banner banner-info">${escapeHtml(result.definition)}</div>
        <div class="table-wrap"><table>
          <thead><tr><th>Airport</th><th class="num">Runways</th><th class="num">Gates</th>
            <th class="num">Departures / runway</th><th class="num">Passengers / gate</th>
            <th class="num">Load factor</th><th>Slot-controlled</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
        <div class="stat-grid" style="margin-top:16px">
          <div class="stat"><div class="stat-label">Busiest airfield (per runway)</div>
            <div class="stat-value">${escapeHtml(result.busiest_airfield)}</div></div>
          <div class="stat"><div class="stat-label">Busiest terminal (per gate)</div>
            <div class="stat-value">${escapeHtml(result.busiest_terminal)}</div></div>
        </div>`;
      return;
    }

    const headers = result.iatas.map((c) => `<th class="num">${escapeHtml(c)}</th>`).join('');
    const rows = result.table.map((row) => `
      <tr>
        <td>${escapeHtml(row.label)}</td>
        ${result.iatas.map((code) =>
          `<td class="num">${escapeHtml(fmt.byKind(row.values[code], row.kind))}</td>`).join('')}
        <td><span class="leader-pill">${escapeHtml(row.leader)}</span></td>
      </tr>`).join('');

    const cards = result.airports.map((entry) => {
      const score = entry.score;
      return `<div class="card">
          <div class="stat-label">${escapeHtml(entry.iata)}</div>
          <div class="hero" style="margin:4px 0 2px">
            <span class="hero-score">${fmt.dec(score.expansion_score, 1)}</span>
            <span class="rating ${RATING_CLASS[score.rating] || 'rating-low'}">${escapeHtml(score.rating)}</span>
          </div>
          <p class="fineprint">Unmet Demand Proxy:
            ${fmt.dec(entry.unmet_demand.unmet_demand_index, 1)} / 100</p>
          ${componentBars(score)}
        </div>`;
    }).join('');

    body.innerHTML = `
      <div class="banner banner-good">${escapeHtml(result.verdict)}</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Metric</th>${headers}<th>Leader</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <p class="fineprint" style="margin:10px 0 18px">
        The Unmet Demand Proxy row is a screening proxy, not a measurement of true latent demand.
      </p>
      <h3 class="card-title">Score breakdown by airport</h3>
      <div class="cmp-cards">${cards}</div>`;
  } catch (err) {
    body.innerHTML = `<div class="banner banner-error">${escapeHtml(err.message)}</div>`;
  }
}

/* ── Expansion score ─────────────────────────────────────────────────── */
async function loadScore() {
  const body = $('#score-body');
  const iata = $('#score-airport').value;
  if (!iata) return;
  body.innerHTML = '<p class="loading">Scoring…</p>';

  try {
    const [score, detail] = await Promise.all([api.score(iata), api.metrics(iata)]);
    const core = detail.metrics;
    const proxy = detail.unmet_demand;
    const longHaul = detail.long_haul;

    const componentRows = score.components.map((c) => `
      <tr>
        <td>${escapeHtml(c.label)}</td>
        <td class="num">${fmt.dec(c.weight_pct, 0)}%</td>
        <td class="num">${escapeHtml(c.raw_display)}</td>
        <td class="num">${escapeHtml(String(c.anchor_low))} → ${escapeHtml(String(c.anchor_high))}</td>
        <td><div class="scorebar">
              <span class="meter"><i style="width:${c.sub_score * 100}%"></i></span>
              <span class="val">${fmt.dec(c.sub_score, 2)}</span>
            </div></td>
        <td class="num">${fmt.dec(c.points, 1)} / ${fmt.dec(c.max_points, 0)}</td>
      </tr>`).join('');

    const explanations = score.components.map((c) =>
      `<li><b>${escapeHtml(c.label)}</b> (${fmt.dec(c.points, 1)} pts): ${escapeHtml(c.explanation)}</li>`
    ).join('');

    const signalRows = proxy.signals.map((s) => `
      <tr>
        <td>${escapeHtml(s.label)}</td>
        <td class="num">${escapeHtml(s.raw_display)}</td>
        <td class="num">${fmt.dec(s.weight * 100, 0)}%</td>
        <td class="num">${fmt.dec(s.points, 1)}</td>
      </tr>`).join('');

    const routeRows = longHaul.top_long_haul_routes.map((r) => `
      <tr><td>${escapeHtml(r.destination)}</td>
        <td class="num">${fmt.int(r.distance_miles)}</td>
        <td class="num">${fmt.int(r.departures)}</td></tr>`).join('');

    body.innerHTML = `
      <div class="card">
        <div class="hero">
          <div>
            <div class="stat-label">Expansion score</div>
            <div><span class="hero-score">${fmt.dec(score.expansion_score, 1)}</span>
                 <span class="hero-of">/ 100</span></div>
          </div>
          <div>
            <div class="stat-label">Rating</div>
            <div style="margin-top:6px"><span class="rating ${RATING_CLASS[score.rating] || 'rating-low'}">
              ${escapeHtml(score.rating)}</span></div>
          </div>
          <div>
            <div class="stat-label">Strongest drivers</div>
            <div class="stat-note" style="font-size:14px;margin-top:6px">
              ${escapeHtml(score.top_drivers.join(', '))}</div>
          </div>
        </div>
        <div class="meter" style="height:9px;margin-top:16px">
          <i style="width:${Math.min(score.expansion_score, 100)}%"></i>
        </div>
        <p class="fineprint" style="margin-top:10px">${escapeHtml(score.methodology)}</p>
      </div>

      <div id="conditions-slot"></div>

      <div class="card">
        <h3 class="card-title">Why this airport scored what it scored</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>Component</th><th class="num">Weight</th><th class="num">Value</th>
            <th class="num">Anchor band</th><th>Sub-score</th><th class="num">Points</th></tr></thead>
          <tbody>${componentRows}</tbody>
        </table></div>
        <ul class="breakdown-list">${explanations}</ul>
      </div>

      <div class="two-col">
        <div class="card">
          <h3 class="card-title">Unmet Demand Proxy</h3>
          <div class="hero" style="margin-bottom:10px">
            <span class="hero-score" style="font-size:34px">${fmt.dec(proxy.unmet_demand_index, 1)}</span>
            <span class="hero-of">/ 100</span>
          </div>
          <p class="stat-note">${escapeHtml(proxy.interpretation)}</p>
          <div class="table-wrap" style="margin-top:12px"><table>
            <thead><tr><th>Signal</th><th class="num">Value</th><th class="num">Weight</th>
              <th class="num">Points</th></tr></thead>
            <tbody>${signalRows}</tbody>
          </table></div>
          <div class="banner banner-warn" style="margin:12px 0 0">${escapeHtml(proxy.disclaimer)}</div>
        </div>

        <div class="card">
          <h3 class="card-title">Long-haul connectivity</h3>
          <div class="hero" style="margin-bottom:10px">
            <span class="hero-score" style="font-size:34px">
              ${fmt.pct(longHaul.long_haul_departure_share, 1)}</span>
            <span class="hero-of">of departures</span>
          </div>
          <p class="stat-note">Defined as ${escapeHtml(longHaul.definition)}.
            ${longHaul.long_haul_route_count} of ${longHaul.route_count} non-stop destinations
            qualify; departure-weighted average stage length is
            ${fmt.int(longHaul.average_stage_length_miles)} miles.</p>
          ${routeRows
            ? `<div class="table-wrap" style="margin-top:12px"><table>
                 <thead><tr><th>Destination</th><th class="num">Distance (mi)</th>
                   <th class="num">Departures</th></tr></thead>
                 <tbody>${routeRows}</tbody></table></div>`
            : '<div class="empty" style="margin-top:12px">No non-stop segment from this airport clears the long-haul threshold.</div>'}
        </div>
      </div>

      <div class="card">
        <h3 class="card-title">Underlying metrics</h3>
        <div class="stat-grid">
          <div class="stat"><div class="stat-label">Passengers (${core.latest_year})</div>
            <div class="stat-value">${fmt.int(core.passengers)}</div></div>
          <div class="stat"><div class="stat-label">Departures</div>
            <div class="stat-value">${fmt.int(core.flights)}</div></div>
          <div class="stat"><div class="stat-label">Load factor</div>
            <div class="stat-value">${fmt.pct(core.load_factor, 1)}</div></div>
          <div class="stat"><div class="stat-label">Passenger CAGR</div>
            <div class="stat-value">${fmt.pct(core.passenger_cagr, 2)}</div></div>
          <div class="stat"><div class="stat-label">Runways</div>
            <div class="stat-value">${core.runways}</div></div>
          <div class="stat"><div class="stat-label">Gates</div>
            <div class="stat-value">${core.gates}</div></div>
          <div class="stat"><div class="stat-label">Departures / runway</div>
            <div class="stat-value">${fmt.int(core.departures_per_runway)}</div></div>
          <div class="stat"><div class="stat-label">Passengers / gate</div>
            <div class="stat-value">${fmt.int(core.passengers_per_gate)}</div></div>
        </div>
      </div>`;
  } catch (err) {
    body.innerHTML = `<div class="banner banner-error">${escapeHtml(err.message)}</div>`;
    return;
  }

  // Deliberately fired after the score has painted, and deliberately NOT part
  // of the Promise.all above: a weather failure must never be able to stop the
  // Expansion Score from rendering. Not awaited, so it cannot block either.
  loadConditions(iata);
}

/* ── Live operational context ────────────────────────────────────────── */
const CONDITION_FAILURES = {
  unavailable: 'Live conditions currently unavailable.',
  no_report: 'No recent METAR available for this airport.',
  unsupported: 'Live conditions are not available for this airport.',
};

/** Humanize the age the API already computed — formatting only, no new metric. */
function observationAge(minutes) {
  if (minutes === null || minutes === undefined) return null;
  if (minutes < 1) return 'Updated just now';
  if (minutes < 60) return `Updated ${Math.round(minutes)} minute${Math.round(minutes) === 1 ? '' : 's'} ago`;
  const hours = minutes / 60;
  return `Updated ${hours.toFixed(hours < 10 ? 1 : 0)} hours ago`;
}

async function loadConditions(iata) {
  const slot = $('#conditions-slot');
  if (!slot) return;
  slot.innerHTML = `<div class="card"><div class="cond-head">
      <h3 class="card-title" style="margin:0">Live operational context</h3>
      <span class="cond-source">AVIATIONWEATHER.GOV</span></div>
    <p class="loading" style="padding:12px 0 0">Loading current conditions…</p></div>`;

  let data;
  try {
    data = await api.conditions(iata);
  } catch (err) {
    // The endpoint answers 200 even when the upstream is down, so reaching here
    // means the request itself failed. Same user-facing outcome.
    data = { status: 'unavailable' };
  }

  // The user may have switched airports while this was in flight; a late
  // response must not overwrite the card for a different airport.
  if ($('#score-airport').value !== iata) return;
  slot.innerHTML = renderConditions(data);
}

function renderConditions(data) {
  // `disabled` means the integration is switched off by configuration. That is
  // not an error and must not be shown as one — render nothing at all.
  if (data.status === 'disabled') return '';

  const shell = (inner) => `<div class="card">
      <div class="cond-head">
        <h3 class="card-title" style="margin:0">Live operational context</h3>
        <span class="cond-source" title="${escapeHtml(data.source || 'AviationWeather.gov')}">AVIATIONWEATHER.GOV</span>
      </div>
      ${inner}
      <p class="cond-note">Live weather is operational context only and is
        <strong>not</strong> used in the Airport Expansion Score.</p>
    </div>`;

  if (data.status !== 'ok') {
    const message = CONDITION_FAILURES[data.status] || CONDITION_FAILURES.unavailable;
    return shell(`<p class="stat-note" style="margin-top:12px">${escapeHtml(message)}</p>`);
  }

  const category = data.flight_category;
  const pill = category
    ? `<span class="fltcat fltcat-${escapeHtml(category.toLowerCase())}"
             title="${escapeHtml(data.flight_category_meaning || '')}">${escapeHtml(category)}</span>`
    : '';
  const age = observationAge(data.observation_age_minutes);

  // Only fields the API actually returned; nothing is derived here.
  const rows = [
    ['Visibility', data.visibility && data.visibility.display],
    ['Wind', data.wind && data.wind.display],
    // "no significant weather" is a real API value but an empty row for the
    // reader, so it is dropped rather than displayed.
    ['Weather', data.weather && data.weather.summary !== 'no significant weather'
      ? data.weather.summary : null],
    ['Ceiling', data.ceiling_feet_agl === null || data.ceiling_feet_agl === undefined
      ? null : `${fmt.int(data.ceiling_feet_agl)} ft AGL`],
    ['Temperature', data.temperature_c === null || data.temperature_c === undefined
      ? null : `${fmt.dec(data.temperature_c, 0)} °C`],
  ].filter(([, value]) => value !== null && value !== undefined && value !== '');

  const list = rows.length
    ? `<dl class="cond-list">${rows.map(([label, value]) =>
        `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`).join('')}</dl>`
    : '';

  return shell(`
    <div class="cond-summary">
      ${pill}
      ${data.station_name ? `<span class="cond-station">${escapeHtml(data.station_name)}</span>` : ''}
    </div>
    ${list}
    ${age ? `<p class="stat-note" style="margin-top:12px">${escapeHtml(age)}</p>` : ''}
    ${data.raw_metar ? `<div class="cond-metar">${escapeHtml(data.raw_metar)}</div>` : ''}`);
}

/* ── Wiring ──────────────────────────────────────────────────────────── */
function optionList(select, entries, selected) {
  select.innerHTML = entries
    .map(([value, label]) =>
      `<option value="${escapeHtml(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`)
    .join('');
}

function airportOptions(includeBlank = false) {
  const entries = state.airports.map((a) => [a.iata, `${a.iata} — ${a.name}`]);
  return includeBlank ? [['', '— none —'], ...entries] : entries;
}

/** Tabs load their data on first activation, so the initial paint stays fast. */
function activateTab(name) {
  $$('.tab').forEach((tab) => {
    const on = tab.dataset.tab === name;
    tab.classList.toggle('is-active', on);
    tab.setAttribute('aria-selected', String(on));
  });
  $$('.tabpanel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.id === `panel-${name}`);
  });

  if (name === 'rank' && !state.loaded.rank) { state.loaded.rank = true; loadRankings(); }
  if (name === 'compare' && !state.loaded.compare) { state.loaded.compare = true; loadComparison(); }
  if (name === 'score' && !state.loaded.score) { state.loaded.score = true; loadScore(); }
}

function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute('data-theme', theme);
  else document.documentElement.removeAttribute('data-theme');
}

function initTheme() {
  let stored = null;
  try { stored = localStorage.getItem('aii-theme'); } catch { /* private mode */ }
  applyTheme(stored);

  $('#theme-btn').addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    const isDark = current
      ? current === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    const next = isDark ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem('aii-theme', next); } catch { /* ignore */ }
  });
}

function wireEvents() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => activateTab(tab.dataset.tab)));

  $('#chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    askQuestion($('#chat-input').value);
  });
  $('#chat-clear').addEventListener('click', () => {
    state.chat = [];
    $('#transcript').innerHTML = '';
    $('#chat-clear').hidden = true;
    $('#transcript-hint').hidden = false;
  });

  $('#rank-region').addEventListener('change', loadRankings);
  $('#rank-sort').addEventListener('change', loadRankings);
  $('#rank-limit').addEventListener('input', (e) => { $('#rank-limit-out').textContent = e.target.value; });
  $('#rank-limit').addEventListener('change', loadRankings);

  ['#cmp-a', '#cmp-b', '#cmp-c'].forEach((sel) =>
    $(sel).addEventListener('change', loadComparison));
  $$('#cmp-view button').forEach((button) => {
    button.addEventListener('click', () => {
      state.compareView = button.dataset.view;
      $$('#cmp-view button').forEach((b) => b.classList.toggle('is-active', b === button));
      loadComparison();
    });
  });

  $('#score-airport').addEventListener('change', loadScore);

  $('#refresh-btn').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Refreshing…';
    try {
      await api.dataStatus(true);
      state.overview = await api.overview();
      renderDataStatus(state.overview.provenance);
      renderCoverage(state.overview);
      loadLiveApiStatus();
      state.loaded = { rank: false, compare: false, score: false };
      activateTab($('.tab.is-active').dataset.tab);
    } catch (err) {
      showGlobalError(err.message);
    } finally {
      button.disabled = false;
      button.textContent = 'Refresh data';
    }
  });
}

/** Pick a default airport by preference, falling back to whatever exists. */
function pick(preferred, fallbackIndex = 0) {
  const codes = state.airports.map((a) => a.iata);
  return preferred.find((code) => codes.includes(code)) || codes[fallbackIndex] || '';
}

async function init() {
  // The docs link follows the API base, so a separately hosted bundle still
  // points at the right service.
  $('#api-docs').href = `${(window.AII_API_BASE || '').replace(/\/$/, '')}/docs`;
  initTheme();
  renderExamples();
  wireEvents();

  try {
    const [overview, airports, regions] = await Promise.all([
      api.overview(), api.airports(), api.regions(),
    ]);
    state.overview = overview;
    state.airports = airports.airports;
    state.regions = regions.regions;
  } catch (err) {
    const detail = err instanceof APIError ? err.message : String(err);
    showGlobalError(`Cannot load data from the API. ${detail}`);
    $('#data-status').innerHTML = '<div class="banner banner-error" style="margin:0">Backend unreachable.</div>';
    return;
  }

  renderDataStatus(state.overview.provenance);
  renderCoverage(state.overview);

  optionList($('#rank-region'),
    [['', 'All US airports'], ...state.regions.map((r) => [r.region, r.region])], '');
  optionList($('#rank-sort'), SORT_OPTIONS, 'expansion_score');
  optionList($('#cmp-a'), airportOptions(), pick(['LAX'], 0));
  optionList($('#cmp-b'), airportOptions(), pick(['SNA'], 1));
  optionList($('#cmp-c'), airportOptions(true), '');
  optionList($('#score-airport'), airportOptions(), pick(['SFO'], 0));

  activateTab('chat');

  // Fired after the dashboard has painted: the live feed is supplementary, so
  // it must never hold up the analytics view.
  loadLiveApiStatus();
}

init();
