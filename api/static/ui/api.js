/* S1 · API transport layer.
 *
 * Contract: JSON in/out only; NO DOM, NO state. Swapping SSE -> WebSocket or
 * adding timeout/retry must not require touching call sites (17 of them), so
 * `fetchJSON` keeps its exact global name and signature.
 */

/**
 * fetch JSON with a uniform error shape.
 * The thrown Error carries `status` and `url` so callers (and the future
 * error-code UI) never have to parse the message text.
 */
async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const err = new Error(`${url} → HTTP ${r.status}`);
    err.status = r.status;
    err.url = url;
    throw err;
  }
  return r.json();
}
