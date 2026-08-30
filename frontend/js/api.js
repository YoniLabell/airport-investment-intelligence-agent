/**
 * HTTP client for the Airport Investment Intelligence API.
 *
 * The dashboard is served by the same FastAPI app, so requests are same-origin
 * and relative by default. Set `window.AII_API_BASE` before this module loads
 * to point the UI at a different backend (useful if you ever host the static
 * files separately from the API).
 */

const BASE = (window.AII_API_BASE || '').replace(/\/$/, '');
const DEFAULT_TIMEOUT_MS = 60_000;

/** An API or transport failure, carrying the HTTP status when there was one. */
export class APIError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'APIError';
    this.status = status;
  }
}

async function request(method, path, { body, params, timeout = DEFAULT_TIMEOUT_MS } = {}) {
  let url = `${BASE}${path}`;
  if (params) {
    const search = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString();
    if (search) url += `?${search}`;
  }

  // AbortController is the only way to bound fetch(); without it a sleeping
  // free-tier backend would hang the UI indefinitely.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new APIError(`The API did not respond within ${Math.round(timeout / 1000)}s.`);
    }
    throw new APIError(`Could not reach the API at ${BASE || window.location.origin}.`);
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    if (response.ok) throw new APIError('The API returned a non-JSON response.');
  }

  if (!response.ok) {
    throw new APIError(detailOf(payload) || `Request failed (HTTP ${response.status}).`,
                       response.status);
  }
  return payload;
}

/** FastAPI reports errors as `detail`, which is a string or a validation array. */
function detailOf(payload) {
  const detail = payload && payload.detail;
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  }
  return '';
}

export const api = {
  health:      ()                  => request('GET',  '/health', { timeout: 10_000 }),
  overview:    ()                  => request('GET',  '/api/overview'),
  dataStatus:  (refresh = false)   => request('GET',  '/api/data-status', { params: { refresh } }),
  airports:    (region)            => request('GET',  '/api/airports', { params: { region } }),
  regions:     ()                  => request('GET',  '/api/regions'),
  metrics:     (iata)              => request('GET',  `/api/airports/${encodeURIComponent(iata)}/metrics`),
  score:       (iata)              => request('GET',  `/api/airports/${encodeURIComponent(iata)}/score`),
  compare:     (iatas, view = 'full') => request('POST', '/api/compare', { body: { iatas, view } }),
  rank:        (region, limit, sortBy) =>
    request('POST', '/api/rank', { body: { region, limit, sort_by: sortBy } }),
  chat:        (message, history = []) =>
    request('POST', '/api/chat', { body: { message, history } }),
};
