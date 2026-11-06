# Prism Document Studio

Prism is a request-scoped PDF workspace built around five API surfaces: Compose, Transform, Protect, Inspect, and Pipeline. The Studio keeps documents and page order together so users can make a sequence of supported changes and export one result without a tool catalog or intermediate download loop.

The public API is versioned under `/api/v1/engine`. The Go service validates and forwards requests to a private Python worker, which performs the supported PDF operations with PyMuPDF and related libraries. The capability response is authoritative: it lists the operations actually exposed by the engine. A primitive name does not imply support for every PDF operation in that category.

## Run locally

Requires Go 1.22 or newer and Python 3.10 or newer.

PyMuPDF remains a required processing dependency. Its AGPL-3.0 terms and the Artifex Commercial License option may affect deployment or distribution. This project makes no legal-clearance claim; obtain qualified legal review of the applicable obligations before launch and secure any license that review determines is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
go run .
```

Open `http://127.0.0.1:8000`. The Go API serves the Studio, owns the public API routes, and starts the Python processor on loopback. Expose the Go port only; the worker port is private. Put the API behind HTTPS and an authenticated reverse proxy before exposing it beyond a trusted network. Host and origin checks are not user authentication or rate limiting.

## API

`GET /api` describes the entry points. `GET /api/v1/engine` returns supported operations, limits, and the current request-storage disclosure. Upload processing endpoints accept `multipart/form-data`; attach PDFs under `files`, and pass a JSON array in `steps` or `recipe`.

| Route | Request fields | Purpose |
|---|---|---|
| `POST /api/v1/engine/compose` | `files`, optional `steps` | Merge documents in upload order, then run ordered page operations. |
| `POST /api/v1/engine/transform` | `files`, required `steps` | Run supported optimization or watermark operations. |
| `POST /api/v1/engine/protect` | `files`, required `steps` | Run supported encryption, decryption, redaction, or sanitization operations. |
| `POST /api/v1/engine/inspect` | one `file` | Return structural information and bounded page previews. |
| `POST /api/v1/engine/pipeline` | `files`, required `recipe` | Run up to 10 ordered Compose, Transform, and Protect steps in one request. |

Each step has this shape:

```json
{
  "primitive": "compose",
  "operation": "rotate-pages",
  "options": { "pages": [2], "rotation": 90 }
}
```

Supported Compose operations are `rotate`, `rotate-pages`, `reorder`, `reverse`, `delete`, `extract`, `duplicate`, `blank`, `crop`, and `resize`. Transform supports `compress`, `compress-images`, `grayscale`, `linearize`, and `watermark`. Protect supports `encrypt`, `decrypt`, `redact`, `scrub-metadata`, and `sanitize`. Exact option names and limits are returned by `GET /api/v1/engine`; invalid or unsupported operations return an error rather than a success-shaped placeholder.

The current engine does not expose every operation described in the original proposal. In particular, digital signing, permission profiles, image/text/office conversions, OCR, conformance validation, visual comparison, and operations that depend on those features are not exposed through the Prism API. Page sequencing in the Studio supports up to 60 total pages per run; larger documents can still be transformed or protected, but the Studio disables edits that require a complete page sequence. This is a working vertical slice, not a claim that all proposed engines or PDF capabilities are complete.

Successful file operations return the result directly with a `Content-Disposition` filename and input/output byte headers. Errors return JSON with a human-readable `detail` field. Existing catalog and tool-specific `/api/process/...` routes have been removed.

## Processing and retention

The application limits combined input files to 80 MiB and accepts at most 100 files per operation. Processing concurrency is bounded per Python worker; it defaults to two active jobs and can be set from 1 to 64 using `PAPERTRAIL_MAX_CONCURRENT_JOBS`. Requests wait up to 30 seconds for a slot before receiving HTTP 503 with `Retry-After`.

The Go gateway parses multipart requests with an 8 MiB in-memory threshold; larger multipart bodies may use temporary files, which the gateway removes after forwarding. The Python framework may also buffer uploads. The worker reads accepted documents into memory for processing, and generated outputs are returned without a job archive. This is not a zero-disk streaming guarantee: configure and monitor temporary storage, proxies, logs, backups, and hosting infrastructure according to your retention requirements.

The PDF processor is synchronous and stateless. There is no user or tenant database, durable queue, shared rate limiter, or persistent object storage. The current implementation has not been load-tested at production scale, and no memory, startup-latency, or processing-speed comparison is claimed.

For deployment, expose only the Go API, set `PAPERTRAIL_HOST`, `PAPERTRAIL_PORT`, `PAPERTRAIL_ALLOWED_HOSTS`, and `PAPERTRAIL_ALLOWED_ORIGINS` for the public service, and configure an authenticated HTTPS reverse proxy. Keep request-body logging disabled. The `/health` endpoint reports worker readiness. `PAPERTRAIL_WORKER_STARTUP_TIMEOUT` controls how long Go waits for the private worker.

## Tests

```powershell
go test ./...
python -m unittest discover -s tests -v
```

The Go tests cover versioned API discovery, private-worker forwarding, readiness, upload limits, and host/origin restrictions. The Python tests cover the supported engine primitives and pipeline, strict operation validation, page sequencing, processing limits, PDF transformations, and existing low-level PDF helpers.
