/* W1b · backend inventory rows (pure).
 *
 * State comes from the order-backed endpoint only:
 *   !enabled            -> disabled   (未接通)
 *   requires_authorization -> unauthorized (需授权)
 *   else                -> available  (可打开)
 * Disabled rows carry no data-open, so they are not clickable by construction.
 */
function backendState(b, unreachable) {
  const x = b || {};
  if (!x.enabled) return "disabled";
  if (unreachable) return "unreachable";     // observed, not declared
  if (x.requires_authorization) return "unauthorized";
  return "available";
}

function backendRowHTML(b) {
  const x = b || {};
  const st = backendState(x);
  const label = st === "disabled" ? "未接通" : (st === "unauthorized" ? "需授权" : "可打开");
  const open = (st === "disabled") ? "" : ` data-open="${escAttr(x.backend_id || "")}"`;
  const transport = [x.adapter, x.transport, x.protocol_version]
    .filter((v) => v !== null && v !== undefined && v !== "").join(" · ");
  return `
  <div class="bk-row" data-state="${st}"${open}>
    <span class="bk-name">${esc(x.backend_id || "?")}</span>
    <span class="bk-meta">${esc(transport)}</span>
    <span class="bk-st ${st}">${label}</span>
  </div>`;
}

/* W1a-3 · window badge. Precedence is deliberate:
 *   real session evidence  -> native   (the ONLY proof of "native")
 *   backend disabled       -> unconnected
 *   needs authorization    -> unauthorized
 *   otherwise              -> api (no proof yet; never guess "native")
 */
function backendBadge(b, hasSession, unreachable) {
  if (hasSession) return { kind: "native", label: "原生" };
  const st = backendState(b, unreachable);
  if (st === "disabled") return { kind: "unconnected", label: "未接通" };
  if (st === "unreachable") return { kind: "unconnected", label: "不可达" };
  if (st === "unauthorized") return { kind: "unauthorized", label: "需授权" };
  return { kind: "api", label: "API" };
}

/* Window subtitle: what the platform will actually speak. */
function backendDetail(b) {
  const x = b || {};
  const parts = [x.adapter, x.transport];
  if (x.protocol_version !== null && x.protocol_version !== undefined) {
    parts.push("v" + x.protocol_version);
  }
  return parts.filter(Boolean).join(" · ") || "未登记后端";
}
