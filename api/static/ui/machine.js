/* W1a · UI state derivation (pure).
 *
 * The console used to let each panel decide its own status; this is the single
 * rule that maps real inputs to one of the seven documented states
 * (docs/ui-enums.md §1). Nothing here touches the DOM.
 */
const STATE_LABEL = Object.freeze({
  empty: "空",
  live: "实时",
  paused: "暂停",
  done: "完成",
  failed: "失败",
  replay: "回放",
  offline: "断线",
});

function deriveState(input) {
  const i = input || {};
  if (!i.hasRun) return "empty";
  if (i.replay) return "replay";          // replay wins over terminal states
  if (i.offline) return "offline";
  if (i.paused) return "paused";
  const status = String(i.status || "").toLowerCase();
  if (status === "running" || status === "started" || status === "") return "live";
  if (status === "done" || status === "concluded") return "done";
  if (status === "failed" || status === "error" || status === "cancelled"
      || status === "canceled") return "failed";
  return "live";
}

function stateLabel(state) {
  return STATE_LABEL[state] || String(state || "");
}
