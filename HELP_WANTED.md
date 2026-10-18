# Help wanted

We are looking for reviewers and contributors on five specific problems. Everything below is
backed by a reproducible test suite; we are not asking for generic advice.

If you want to help, open an issue on this repository (or a Discussion) and quote the section
number. Small, focused PRs are very welcome.

## 1. AG-UI mapping for our event contract

We already emit a stable event stream (`agent_card`, `agent_output`, `tool_call`,
`spi_permission_request`, `permission_answered`, `hetero_dispatch_result`, …) and we have an
ACP-based backend adapter.

**Question:** what is the least risky way to add an [AG-UI](https://docs.ag-ui.com) adapter —
a translation layer from our events to AG-UI events (`RUN_STARTED`, `TEXT_MESSAGE_CONTENT`,
`TOOL_CALL_*`, `STATE_DELTA`, …) — without breaking the existing contract or the UI?
Has anyone mapped ACP sessions to AG-UI before? We would rather add a mapping than fork the SDK.

## 2. A permission model that is deny-by-default

Today: the backend offers options, the UI shows only those options, and a missing answer is
auto-rejected after 45 s. That is fail-closed at the *option* level, but not at the *policy* level.

**Question:** how would you model a deny-wins policy (allow/deny principals, per-tool allowlist,
ambiguous → deny) that survives per-run consent and per-agent scopes? Reference implementations
or a small algebra we could adopt? Our sensitive calls would go through a tool gateway.

## 3. Hybrid runtime: some roles native, some CrewAI

`routing.map` sends selected agent ids to a native ACP backend; everything else stays on CrewAI.

**Question:** what are the traps in running both in one run — session reuse, context accounting,
cancellation semantics, cost attribution? We have seen duplicate sessions when a legacy binding
and a routing entry both fire; we removed the legacy binding, but we would like a general rule.

## 4. Per-agent isolation on a Windows-first local host

Each run currently gets one working directory (single-root contract). Multiple agents writing the
same root is the obvious risk.

**Question:** what is the pragmatic path to per-agent isolation for a local, Windows-first
platform — per-agent containers, per-agent sandbox users, or a filesystem overlay? We want to keep
the "one computer per agent" property without making local setup painful.

## 5. Testing an inline-JS console without brittle pixel tests

Our console is a single `index.html` with inline JS plus a few ES modules. We have pure-function
tests (node) and a Playwright smoke suite, but structural tests are easy to write and easy to
bypass.

**Question:** how do you keep an inline-JS UI testable as it grows — extract everything into
modules, adopt a framework, or use DOM-level harnesses? What has actually worked for you?

## 6. Visualising multi-agent logic without spaghetti

We draw a "logic canvas": nodes are agents/tasks, edges are handoffs, parsed from the same
topology YAML the runner uses. Three toggleable layers (assignment / execution / progress) are
drawn on top of the skeleton, and a run can be replayed from trace.

**Question:** what representation stays readable when a run has 9 roles, 20+ subtasks and
conditional edges — layered DAG, swimlanes by agent, or a sequence view plus a per-agent mini-graph?
We would also like to know how you replay a *past* run's state without pretending it is live
(scrubber over events vs snapshot per step). Pointers to LangGraph Studio / AutoGen Studio /
OpenBot-style graphs that actually scale would help.

## 7. A UI critique we can act on

We aligned our console to a static design (screenshot in `docs/assets/console.png`): dark theme,
one window per agent, `对话/轨迹` tabs, in-window permission cards, a run-history dock, a composer.

**Question:** looking at the screenshot, what are the **first three things** you would change for
a tool an operator stares at for hours — information density, window model, or the permission flow?
We are especially unsure about (a) how many windows to show before switching to a list, and
(b) whether a permission card should block the window or sit beside the conversation.

## 8. Optimisation: where would you cut first?

One run is plan → match → N workers → review. The native backend serialises per client (one
handler slot); the console receives one SSE event per step and prepends to a pane; a long run can
produce thousands of events. We have **no profile yet** — we want to measure before we cut.

**Questions:**

1. **Tokens/latency** — how do you stop the plan, each worker and the review from re-reading the
   same context, without losing fidelity? Any standard "context budget" pattern (summarise once,
   pass by reference, structured hand-off) that survived real use?
2. **Concurrency** — is per-backend single-flight + parallel across backends the right model?
   How do you avoid head-of-line blocking when one agent holds the only slot?
3. **UI under load** — for an event stream at 10–50 events/s, do you coalesce per frame, virtualise
   the list, or move to a reducer/state store? What do AG-UI clients actually do?
4. **Storage** — traces and artifacts grow without bound. Retention/compaction patterns you
   actually use (JSONL + zstd? SQLite? object store)?
5. **Measurement** — what would you instrument first: per-node start/end spans, token counts per
   call, queue wait time, or render time? We would rather add spans than guess.

---

### What you get from us

- A deterministic offline mode (no API key needed) and a `gate_all` script that runs every check.
- Traces, artifacts and a real-machine smoke report in `docs/`.
- We will credit reviewers in the README and in release notes.
