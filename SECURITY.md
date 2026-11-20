# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do not open a public issue. Use the private vulnerability reporting of this GitHub repository ("Security" tab, "Report a vulnerability"). You will get a first answer within 14 days.

## Scope

The stack is meant to run locally. The compose file binds the web service to `127.0.0.1` only. It has no user accounts and stores only public data. The API's request limits apply to all clients together, so a single client can crowd out others; before exposing the stack beyond the local machine, put a reverse proxy with a per-client rate limit in front of it.
