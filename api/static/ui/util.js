/* S1 · pure UI helpers.
 *
 * Contract: NO DOM access, NO state, NO fetch. Anything that needs those lives
 * in api.js / state.js instead. Keeping this file pure is what makes it safe to
 * load before the main inline script.
 */

/** HTML-escape a value for template strings (null/undefined become ""). */
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Attribute-safe escaping: esc() alone leaves quotes intact, which lets a value
 * break out of `data-x="..."` and inject an event handler. Always use this for
 * values placed inside attributes.
 */
function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** created_at is epoch seconds from SQLite; ISO strings are passed through. */
function fmtTime(t) {
  if (t === null || t === undefined || t === "") return "";
  if (typeof t === "number" || /^\d+(\.\d+)?$/.test(String(t))) {
    const d = new Date(Number(t) * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
      `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }
  return String(t).replace("T", " ").slice(0, 19);
}
