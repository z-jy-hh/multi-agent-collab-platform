/* S1 · versions dock row markup (pure).
 *
 * Contract: one row in -> one HTML string out. No DOM, no state, no fetch.
 * The diff button keeps its data-* contract so the caller can delegate clicks.
 */
function versionRowHTML(v) {
  const ver = String(v.version).padStart(3, "0");
  const diff = v.diff
    ? `<button class="ghost copy-btn" data-diff-key="${esc(String(v.version))}" ` +
      `data-diff-title="v${ver} 提示词 diff">查看 diff</button>`
    : `<div class="muted">diff 不可用:${esc(v.diff_unavailable_reason || "未知原因")}</div>`;
  return `
    <div class="evolution-item ${esc(v.decision)}">
      <b>v${ver}</b> [${esc(v.decision)}]
      Δ=${Number(v.score_delta).toFixed(4)}
      <div class="muted">${esc(String(v.reason || "").slice(0, 64))}</div>
      ${diff}
    </div>`;
}
