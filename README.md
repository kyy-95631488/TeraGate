<div align="center">

# TeraBox Gateway

### A lightweight Python API for TeraBox share links, file metadata, downloads, and HLS.

Run it locally, use the hosted API, or integrate the endpoints into your own tools.

[![PyPI](https://img.shields.io/pypi/v/terabox-gateway?style=flat-square&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/terabox-gateway/)
[![GitHub](https://img.shields.io/badge/GitHub-kyy--95631488%2FTeraGate-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/kyy-95631488/TeraGate)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-MIT-16A34A?style=flat-square)](#license)

[Quick start](#quick-start) · [API](#api) · [Configuration](#configuration) · [Deployment](#deployment)

</div>

---

## Overview

TeraBox Gateway is a Flask service and installable Python package for working with TeraBox share links. It resolves file information through a unified proxy, offers optional local resolution with configured cookies, streams downloads, and exposes HLS playlists and media segments.

This repository is maintained at [kyy-95631488/TeraGate](https://github.com/kyy-95631488/TeraGate) and is based on the [original TeraBox Gateway project](https://github.com/saahiyo/terabox-gateway).

### Highlights

- **File and folder metadata** with optional direct-link resolution.
- **Unified proxy API** for resolving shares, cached lookups, HLS playlists, and segments.
- **Streamed downloads** with HTTP byte-range support.
- **Local resolution fallback** for eligible requests; verification challenges are reported, not bypassed.
- **Interactive API docs** at `/docs` and an OpenAPI document at `/swagger.json`.
- **Rate limiting, response caching, and configurable retries** for transient network failures.
- **Vercel configuration** for deploying your own instance.
- **Cookie configuration** through environment variables or a private JSON file.

> **Privacy:** Treat TeraBox cookies, direct links, and admin keys as credentials. Keep them out of source control and avoid sharing them publicly.

## Requirements

- Python 3.9 or later.
- A current TeraBox `ndus` cookie for authenticated requests.
- Internet access to the configured TeraBox proxy and upstream services.

## Quick start

### Install from PyPI

```bash
python -m pip install terabox-gateway
```

Create a `.env` file in your working directory and add your cookie:

```dotenv
COOKIE_JSON=YOUR_NDUS_COOKIE
```

Start the API:

```bash
terabox-gateway --host 127.0.0.1
```

The server defaults to binding `0.0.0.0:5000`. The command above limits it to your computer; open `http://127.0.0.1:5000` to use the API. To check that it is responding:

```bash
curl http://127.0.0.1:5000/health
```

Open the interactive API reference at [http://127.0.0.1:5000/docs](http://127.0.0.1:5000/docs).

### Run from source

```powershell
git clone https://github.com/kyy-95631488/TeraGate.git
Set-Location TeraGate
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
notepad .env
```

Add `COOKIE_JSON=YOUR_NDUS_COOKIE` and `HOST=127.0.0.1` to the `.env` file that opens, save it, then run:

```powershell
python main.py
```

If `.env.example` is not present in your checkout, create `.env` yourself with `COOKIE_JSON=YOUR_NDUS_COOKIE`. You can also start the installed CLI:

```powershell
terabox-gateway --host 127.0.0.1 --port 5000
```

Use `--host 0.0.0.0` only when you intentionally want the service reachable from other devices or networks.

## Configure cookies

The gateway accepts a single `ndus` token, a JSON object of cookies, or a browser-exported JSON array. Cookie sources are checked in this order:

1. `COOKIE_JSON`
2. `TERABOX_COOKIES_JSON`
3. `TERABOX_COOKIES_FILE`

Examples:

```dotenv
# One cookie value
COOKIE_JSON=YOUR_NDUS_COOKIE

# Or a JSON object
# COOKIE_JSON={"ndus":"YOUR_NDUS_COOKIE","other":"COOKIE_VALUE"}
```

For browser-exported JSON arrays, set `TERABOX_COOKIES_FILE` to a private file path. Array entries are accepted only for configured TeraBox domains.

To obtain a cookie, sign in to TeraBox in your browser and export the cookie for your own session. Cookies expire; replace them if authenticated requests start failing. Do not commit real cookie values.

## API

Base URL for local development: `http://127.0.0.1:5000`

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/` | API information and status |
| `GET`, `HEAD` | `/download` | Stream a TeraBox download and forward byte-range requests |
| `GET` | `/health` | Gateway health check |
| `GET` | `/api` | File listing, link resolution, and proxy modes |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/swagger.json` | OpenAPI specification |
| `GET` | `/admin/<path>` | Forward supported admin and analytics requests to the proxy |

### Resolve a share

The default `/api` mode returns file details with short proxy links:

```bash
curl -G "http://127.0.0.1:5000/api" \
  --data-urlencode "url=https://1024terabox.com/s/YOUR_SHARE_ID"
```

Use `resolve=true` to request fully resolved direct links:

```bash
curl -G "http://127.0.0.1:5000/api" \
  --data-urlencode "url=https://1024terabox.com/s/YOUR_SHARE_ID" \
  --data-urlencode "resolve=true"
```

The response includes a status and a `files` list. Each file may include a name, size, thumbnail, file identifiers, and a download link. For the complete schema and live request examples, use `/docs`.

### Stream a download

`/download` requires an HTTPS TeraBox download URL from a supported host. The gateway applies its configured cookies and forwards range requests:

```bash
curl -L -G "http://127.0.0.1:5000/download" \
  --data-urlencode "url=https://TERABOX_DOWNLOAD_URL" \
  -o downloaded-file
```

### Proxy modes

Use `/api` with `mode` for direct proxy operations:

| Mode | Purpose | Common parameters |
| --- | --- | --- |
| `resolve` | Resolve a share and extract metadata | `surl` |
| `lookup` | Query cached metadata | `surl` or `fid` |
| `page` | Retrieve the raw share page for diagnostics | `surl` |
| `api` | Call the share API when a `jsToken` is already known | `jsToken`, `shorturl` |
| `stream` | Get an HLS playlist with rewritten segment URLs | `surl`; optional `type` |
| `segment` | Proxy a media segment | `url` |
| `health` | Check proxy service health | — |

Example:

```bash
curl -G "http://127.0.0.1:5000/api" \
  --data-urlencode "mode=resolve" \
  --data-urlencode "surl=YOUR_SHARE_ID"
```

For proxy-specific options and response details, see [`tboxproxy_usage.md`](tboxproxy_usage.md).

### Admin routes

The `/admin/*` routes forward supported analytics and database-inspection requests to the configured proxy. The proxy may require an admin key, supplied as a `key` query parameter or an `x-admin-key` header. Keep this key private.

## Configuration

Set values in a local `.env` file or in the process environment:

| Variable | Purpose | Default |
| --- | --- | --- |
| `COOKIE_JSON` | Cookie value, JSON object, or browser-exported cookie array | — |
| `TERABOX_COOKIES_JSON` | Alternative JSON cookie value | — |
| `TERABOX_COOKIES_FILE` | Path to a private cookie JSON file | — |
| `HOST` | Server bind address | `0.0.0.0` |
| `PORT` | Server port | `5000` |
| `FLASK_DEBUG` | Enable Flask debug mode with `1` | `0` |
| `RATE_LIMIT` | Requests per client per rate-limit window | `30` |
| `RATE_WINDOW` | Rate-limit window in seconds | `60` |
| `CACHE_TTL` | Cache entry lifetime in seconds | `60` |
| `CACHE_MAX_SIZE` | Maximum cache entries | `500` |
| `HTTP_MAX_RETRIES` | Retries for transient HTTP/network failures | `3` |
| `HTTP_INITIAL_DELAY` | Initial retry backoff in seconds | `0.5` |
| `HTTP_BACKOFF_FACTOR` | Multiplier for retry backoff | `2.0` |

Avoid enabling Flask debug mode in a public deployment. Do not expose an unauthenticated gateway to the internet unless you have added appropriate access controls and network protections.

## Deployment

### Vercel

The repository includes a `vercel.json` configuration for deploying the root `main.py` entrypoint.

1. Import the repository into Vercel.
2. Configure `COOKIE_JSON` as an encrypted environment variable in the Vercel project settings.
3. Deploy, then check the deployment's `/health` endpoint and open `/docs`.

Do not put cookies in source files, deployment logs, or public URLs. Serverless execution limits and upstream service behavior may affect long-running downloads or streams.

### Run behind a production server

The built-in Flask server is intended for local development. For a public deployment, use a suitable production WSGI/ASGI setup for Flask async views, configure HTTPS and access controls, and review the platform's connection and streaming limits before enabling large downloads or HLS.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| `401`/`403` or authentication failures | Refresh the TeraBox cookie and verify the configured cookie source. |
| Share resolution fails | Confirm the URL uses a supported domain and that the proxy service is reachable. |
| `/download` rejects a URL | Use an HTTPS download URL on a supported TeraBox host; a share-page URL is not a direct download URL. |
| Network errors repeat | Check DNS/network access and upstream availability. Retries apply to transient failures; they cannot fix a persistent DNS or authentication issue. |
| HLS playback does not start | Try resolving the share first, then request a fresh `stream` playlist; verify the client supports HLS. |
| `/docs` is unavailable | Check that the gateway is running and that `/swagger.json` responds. |

## Development

Install the project and its runtime dependencies:

```bash
python -m pip install -e .
```

Run locally:

```bash
python main.py
```

Inspect the OpenAPI document at `/swagger.json`. The unified proxy implementation and additional usage examples are documented in [`tboxproxy_usage.md`](tboxproxy_usage.md).

### Project structure

```text
.
├── main.py                     # Local/Vercel Flask entrypoint
├── pyproject.toml              # Package metadata and CLI entrypoint
├── requirements.txt            # Runtime dependencies
├── vercel.json                 # Vercel routing configuration
├── tboxproxy_usage.md          # Proxy modes and usage guide
└── src/
    └── terabox_gateway/
        ├── api.py              # Flask routes and API handlers
        ├── config.py           # Cookies, allowed domains, and settings
        ├── terabox_client.py   # TeraBox client and proxy integration
        ├── utils.py            # URL, response, and formatting helpers
        ├── cache.py            # In-memory cache
        ├── rate_limiter.py     # Per-client rate limiter
        ├── main.py             # Installed CLI entrypoint
        └── swagger/
            └── swagger.json   # OpenAPI specification
```

## Supported domains

The gateway currently recognizes:

`terabox.app` · `teraboxshare.com` · `terabox.com` · `1024tera.com` · `1024terabox.com` · `teraboxlink.com` · `terasharefile.com` · `terafileshare.com` · `terasharelink.com`

Some domains are configured with their `www` hostname as well. The `/download` route additionally requires HTTPS.

## License

The Python package metadata declares MIT, but this checkout currently has no standalone `LICENSE` file. Review the license terms before redistributing.

---

<div align="center">

Built for developers integrating TeraBox links into their tools.

</div>
