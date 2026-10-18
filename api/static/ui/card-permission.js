/* S1 · permission card markup (pure).
 *
 * Contract: builds an HTML string from the SERVER's frame only.
 *  - buttons come from `options`; we never invent allow-all/remember;
 *  - the run id / option id go into data-* attributes (no inline onclick with
 *    interpolated values, which is both an XSS vector and hard to test).
 */
function permCardHTML(payload, runId) {
  const p = payload || {};
  const tcId = p.tool_call_id || p.toolCallId || "";
  const tc = esc(tcId || "?");
  const options = Array.isArray(p.options) ? p.options.slice() : [];
  const buttons = options.map((o) =>
    `<button class="dc-perm-btn" data-perm-opt="${escAttr(o)}" ` +
    `data-perm-run="${escAttr(runId || "")}" ` +
    `data-perm-tc="${escAttr(tcId)}">${esc(o)}</button>`).join(" ");
  return `
    <div class="dc-perm-info">toolCallId: ${tc}</div>
    <div class="dc-perm-info">options: ${esc(options.join(", ") || "（服务端未给出）")}</div>
    ${buttons || '<div class="dc-perm-info">无可用选项（fail-closed）</div>'}`;
}

/* W1a-2: the card is mounted INSIDE the asking agent's window, so the queue
 * label lives next to it. Pure so the count logic is testable without a DOM. */
function permQueueLabel(pending) {
  const n = Number(pending) || 0;
  if (n <= 0) return "无待处理";
  return n === 1 ? "待处理 1 项" : `待处理 ${n} 项`;
}
