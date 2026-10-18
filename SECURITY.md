# Security

## Reporting

Please do **not** open a public issue for a vulnerability. Contact the maintainer privately
(open a minimal issue asking for a private channel if you have no other way to reach us).

## Design notes

- The platform is a **local-first** tool: `server.py` binds `127.0.0.1` by default. Do not expose
  it to the internet without putting it behind an authenticated proxy.
- Backends run as **child processes** with a single-root working directory. Treat a backend as
  code you chose to run.
- Permission requests are fail-closed at the option level: only server-offered option ids are
  accepted, an unanswered request is rejected after 45 seconds, and duplicate answers are refused.
- Secrets live in `.env` (git-ignored) and are never written to traces, artifacts or logs by the
  platform; redaction tests cover the common shapes.
