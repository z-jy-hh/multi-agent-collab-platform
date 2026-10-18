/* S1 · UI enum constants (single source of truth in code).
 *
 * Mirrors docs/ui-enums.md. The test suite parses that document and fails if
 * the two drift apart, so this file must never grow a value the doc lacks.
 */
const STATE = Object.freeze({
  EMPTY: "empty",
  LIVE: "live",
  PAUSED: "paused",
  DONE: "done",
  FAILED: "failed",
  REPLAY: "replay",
  OFFLINE: "offline",
});

const PERM = Object.freeze({
  PENDING: "pending",
  ALLOWED: "allowed",
  REJECTED: "rejected",
  EXPIRED: "expired",
});

const BACKEND = Object.freeze({
  AVAILABLE: "available",
  UNAUTHORIZED: "unauthorized",
  UNREACHABLE: "unreachable",
  DISABLED: "disabled",
});

const EXECUTOR = Object.freeze({
  CREWAI: "crewai",
  SCRIPTED: "scripted",
  ACP: "acp",          // W4-1: role-level native execution (dsh ACP)
  UNKNOWN: "unknown",
});
