/* S1 · window chrome markup (pure).
 *
 * Contract: returns an HTML string only — no DOM access, no state, no fetch.
 * The caller resolves the display name (`winName`) and passes it in, which is
 * what keeps this file dependency-free and testable.
 *
 * P0 alignment: title row (name · sub · badge · state), an info row
 * (model / permission / session), and 对话/轨迹 tabs. `isFlow` windows (the
 * system/flow window) have no tabs and no info row.
 */
function winShellHTML(title, sub, isFlow) {
  const t = esc(title);
  const ta = escAttr(title);
  const s = esc(sub || "");
  const dirs = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
  const handles = dirs
    .map((d) => `<div class="rz rz-${d}" data-dir="${d}" aria-hidden="true"></div>`)
    .join("");
  const flow = !!isFlow;
  const chrome = flow ? "" : `
  <div class="win-info">
    <span>模型 <b class="win-model">—</b></span>
    <span>权限 <b class="win-perm">—</b></span>
    <span>本次会话 <b class="win-cost">—</b></span>
  </div>
  <div class="win-tabs" role="tablist">
    <button class="win-tab on" data-tab="chat" role="tab" aria-selected="true">对话</button>
    <button class="win-tab" data-tab="trace" role="tab" aria-selected="false">轨迹</button>
  </div>`;
  const panes = flow
    ? `<div class="win-body"></div>`
    : `<div class="win-body" data-pane="chat" role="tabpanel"></div>
       <div class="win-trace" data-pane="trace" role="tabpanel" hidden></div>`;
  return `
  <div class="win-head">
    <span class="win-title">${t}</span>
    <span class="win-badge" data-kind="api">API</span>
    <span class="win-sub">${s}</span>
    <span class="win-state" style="margin-left:auto;color:var(--dim3);font-size:10.5px"></span>
    <button class="win-btn" aria-label="查看 ${ta} 的逻辑画布" data-a="logic">逻辑</button>
    <button class="win-btn" aria-label="最小化 ${ta} 窗口" data-a="min">–</button>
    <button class="win-btn" aria-label="关闭 ${ta} 窗口" data-a="close">×</button>
  </div>
  ${chrome}
  ${panes}
  ${handles}
  `;
}
