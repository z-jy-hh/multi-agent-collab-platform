/* S1/W1a · B2 (API) window block: one agent turn, rendered as data — not as a
 * fake chat bubble. The prompt is NOT persisted by the platform, so the block
 * says so explicitly instead of inventing one.
 */
function ioBlockHTML(meta) {
  const m = meta || {};
  const executor = esc(m.executor || "unknown");
  const native = !!m.sessionId;
  const badge = native
    ? `<span class="win-badge" data-kind="native">原生</span>`
    : `<span class="win-badge" data-kind="api">API</span>`;
  const session = native
    ? `<div class="io-k">会话 <span class="mono">${esc(m.sessionId)}</span></div>`
    : `<div class="io-k">无会话</div>`;
  const prompt = m.prompt
    ? `<pre>${esc(m.prompt)}</pre>`
    : `<div class="io-k io-miss">prompt：未在下发事件中携带（平台不持久化 prompt）</div>`;
  const response = m.response
    ? `<pre>${esc(m.response)}</pre>`
    : `<div class="io-k io-miss">response：产出未取到（${esc(m.truncated && m.chars == null ? "≥100000" : (m.chars ?? "?"))} 字）</div>`;
  const trunc = m.truncated ? `<div class="io-k io-miss">（已截断：仅显示前 100000 字符）</div>` : "";
  return `
  <div class="io-block">
    <div class="io-k">执行者 ${badge} · ${executor}</div>
    ${session}
    <div class="io-k">prompt</div>
    ${prompt}
    <div class="io-k">response</div>
    ${response}
    ${trunc}
  </div>`;
}
