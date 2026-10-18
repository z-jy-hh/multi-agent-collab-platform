/* W1a-4 · window geometry (pure).
 *
 * Drag/resize used to be inline arithmetic, untestable outside a browser.
 * These two functions hold the rules (min size, no negative origin) so the
 * logic is covered by node tests without a DOM.
 */
function clampPosition(x, y, maxX, maxY) {
  return {
    x: Math.max(0, Math.min(maxX, x)),
    y: Math.max(0, Math.min(maxY, y)),
  };
}

function resizeBox(r0, dx, dy, dir, minW, minH) {
  const w0 = r0.w, h0 = r0.h;
  const mw = minW === undefined ? 220 : minW;
  const mh = minH === undefined ? 120 : minH;
  let { x, y, w, h } = r0;
  if (dir.includes("e")) w = Math.max(mw, w0 + dx);
  if (dir.includes("s")) h = Math.max(mh, h0 + dy);
  if (dir.includes("w")) {
    w = Math.max(mw, w0 - dx);
    x = Math.max(0, r0.x + (w0 - w));
    w = r0.x + w0 - x;            // the left edge stops at 0 and the width follows
  }
  if (dir.includes("n")) {
    h = Math.max(mh, h0 - dy);
    y = Math.max(0, r0.y + (h0 - h));
    h = r0.y + h0 - y;
  }
  return { x, y, w, h };
}
