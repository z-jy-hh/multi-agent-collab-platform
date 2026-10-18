/* S1 · global UI state.
 *
 * Contract: DATA ONLY — no DOM access, no fetch, no side effects at load time.
 * Declared as a top-level `const` in a classic script, so later scripts resolve
 * it as a global lexical binding (and must NOT redeclare it).
 */
const state = {
  currentRunId: null,       // live run started by this page (cancel target)
  selectedRunId: null,      // run selected in the sidebar
  showArchived: false,      // M10: local-only archive visibility toggle
  es: null,                 // live EventSource handle
  paused: false,
  pauseQueue: [],
  winZ: 10,
  wins: new Map(),          // winKey -> window element
  artById: {},              // op_id -> artifact (viewer)
  invokedAgents: new Set(), // agents actually invoked in this run (incl. replay)
  // W1a-3: backend inventory from the registered endpoint (dsh only for now)
  backends: {},             // backend_id -> {enabled, requires_authorization, ...}
  backendsLoaded: false,
  backendsError: null,
  // P2: profile drawer data + per-agent composition (sent as RunRequest.profiles)
  modesData: null,
  profilePlan: {},
};
