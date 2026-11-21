# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do not open a public issue. Use the private vulnerability reporting of this GitHub repository ("Security" tab, "Report a vulnerability"). You will get a first answer within 14 days.

## Scope

The stack is meant to run locally. The compose file binds the web service to `127.0.0.1` only. It has no user accounts and stores only public data. The API container is attached only to an internal Docker network that it shares with the web container, so it cannot open connections to the internet or to other containers.

The API allows each client address at most 16 data requests in progress, out of 64 for all clients together. The client address is the one Caddy saw: the API takes it from `X-Forwarded-For` only on connections from the web container's fixed address, and a value that a client puts into that header itself is not used. On the `make prod-check` stack, while one container held 100 parallel connections to `/forecasts/days` for 10 seconds, a second container requesting the forecast and seven days of three series received 200 for all 406 of its requests in two runs; without the per-client limit, 168 of 304 such requests were answered 503 (see [Architecture](docs/explanation/architecture.md#network)).

What this does not cover:

- The limit counts concurrent requests per source address, not requests per second. A client with many addresses gets one limit for each (IPv6 addresses count per /64 network), and users behind one NAT share one. All browsers on the host machine reach Caddy through the published port from the same Docker gateway address, so locally they share one limit.
- The two slots and the short queue for large requests, the byte budget for unsent responses and uvicorn's limit of 256 open connections still apply to all clients together. A client within its 16 requests can keep the large-request slots busy, so other clients' requests for long periods wait or are refused.
- Behind another reverse proxy or load balancer, Caddy sees that proxy's address, and all clients share a single limit of 16. Such a proxy must forward the client address in `X-Forwarded-For`, and its address must be trusted by Caddy (`trusted_proxies`) and by the API (`FORWARDED_ALLOW_IPS` in `compose.yaml`).

Before exposing the stack beyond the local machine, put a reverse proxy with a per-client rate limit in front of it and configure it as in the last point.
