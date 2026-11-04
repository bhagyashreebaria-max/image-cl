# Papertrail PDF Toolkit

Papertrail is an original server-side PDF workbench. Its searchable 69-item catalog includes 65 server-side operations and four browser-only utilities across page organization, conversions, optimization, accessibility checks, annotations, comparison, document security, attachments, metadata, and workflow composition.

The processing API receives uploaded documents. It reads the request into memory for processing on a worker thread, closes each upload buffer when the request finishes, and returns generated output directly in the response. The application does not create a server-side job archive or intentionally persist uploaded/output documents. The browser-only history feature stores up to 50 recent job records in local browser storage; records include tool and file names, timestamps, and sizes, but not document contents or outputs. Framework upload buffering may use temporary server storage before the endpoint reads a file. Reverse proxies, hosting platforms, backups, and infrastructure logs are outside this application's retention controls and must be configured by the operator.

## Run locally

Requires Go 1.22 or newer and Python 3.10 or newer.

PyMuPDF remains a required processing dependency. Its AGPL-3.0 terms and the Artifex Commercial License option may affect deployment or distribution. This project makes no legal-clearance claim; obtain qualified legal review of the applicable obligations before launch and secure any license that review determines is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
go run .
```

Open `http://127.0.0.1:8000`. The Go API serves the browser app, owns the public API routes, and starts the existing Python PDF processor as a private worker on `127.0.0.1:8001`; only the Go API port is intended to be exposed. Go validates requests, reconstructs the internal multipart worker request, and streams results back. The app accepts up to 80 MiB of input per operation. Most operations do not accept encrypted PDFs. Use Decrypt PDF first when you have the document password.

`GET /api` describes the API routes. `GET /api/tools` lists every operation and its current availability; `GET /api/status` reports capabilities and limits. Run an operation with `POST /api/process/{tool}` using multipart form data: attach input documents under the `files` field and include that tool's options as additional form fields. `POST /api/inspect` accepts one document under `file`. Successful processing responses stream the generated file with a `Content-Disposition` filename. Errors use JSON with a human-readable `detail` field. Existing `/api/...` routes and the browser UI continue to work through the Go API.

The Go API streams reconstructed input to the worker and streams output to the client, limits request bodies to 80 MiB plus 2 MiB for multipart framing, and waits up to 15 minutes for a worker response. Multipart input parsing uses temporary files above an 8 MiB memory threshold and removes those temporary files after dispatch. The Python worker retains the per-process processing concurrency limit and queue timeout described below. An unavailable worker yields HTTP 503; worker response timeouts yield HTTP 504.

## Deploy the Go API and processing worker

The app is provider-neutral; it does not create or select cloud resources. Run the Go API from the repository root with its Python dependencies installed. By default it starts one Python worker bound to loopback and calls that worker through an internal protocol. Expose the Go API only; do not expose the worker port. Put the API behind HTTPS and an authenticated reverse proxy before exposing it beyond a trusted network. The application has allowed-host and allowed-origin checks, but those are not user authentication, rate limiting, or a substitute for deployment controls.

Set these variables for a deployment. Replace the sample host and origin with the actual public hostname:

```text
PAPERTRAIL_HOST=0.0.0.0
PAPERTRAIL_PORT=8000
PAPERTRAIL_WORKER_PORT=8001
PAPERTRAIL_PYTHON_COMMAND=python
PAPERTRAIL_WORKER_STARTUP_TIMEOUT=60s
PAPERTRAIL_ALLOWED_HOSTS=pdf.example.com
PAPERTRAIL_ALLOWED_ORIGINS=https://pdf.example.com
```

Configure the reverse proxy's request-body limit slightly above 82 MiB to account for multipart framing, and avoid logging request bodies. Protect the service with authentication, rate limits, and appropriate storage/temporary-directory policies. The `/health` endpoint checks that the worker is ready and can be used for a deployment health check. `PAPERTRAIL_WORKER_STARTUP_TIMEOUT` is a positive Go duration; the worker's port must be available locally. `PAPERTRAIL_ALLOWED_HOSTS` is enforced by the Go API; the private worker is restricted to loopback hosts. Other `PAPERTRAIL_*` processing settings (including concurrency settings) are inherited by the worker.

Each server process accepts at most two PDF-processing or inspection jobs concurrently by default. Set `PAPERTRAIL_MAX_CONCURRENT_JOBS` to a value from 1 to 64 to tune that per-process limit for the host's available memory and CPU. Requests wait up to 30 seconds for a slot, then receive HTTP 503 with `Retry-After`. An operation accepts at most 100 uploaded files. Every raster-based operation is capped at 8 million pixels per page and 40 million pixels per operation; image-to-PDF is limited to 100 image pages as well. These limits bound common processing workloads but do not make arbitrary uploads safe to process without authentication, quotas, and infrastructure-level resource limits. A multi-worker or multi-replica deployment multiplies the configured concurrency; size it against measured peak memory rather than request count alone.

This repository is a synchronous, stateless processing service, not a globally distributed multi-tenant platform. It has no user or tenant database, durable job queue, shared rate limiter, or persistent object storage. Horizontal replicas can serve independent requests, but they do not coordinate capacity or quotas. Serving very large populations requires additional architecture and measured capacity planning: authenticated API ingress, per-tenant quotas, a durable queue and separately scaled workers, object storage with lifecycle controls, a database for tenant/job metadata, and centralized metrics, tracing, and alerting. The current implementation has not been load-tested at production scale and cannot substantiate claims of support for trillions of users.

The browser calls relative `/api/...` paths, so the frontend and Go API must be served from the same origin. If the interface shows a fetch error, open `/health` on that same host first; it should return `{"status":"ok"}` when the worker is ready. A static repository preview is not a running processing server. For local use, start the Go API with `go run .` and open `http://127.0.0.1:8000`. For a hosted instance, deploy the Go API and worker together and set the allowed host/origin variables above. For Python processor development without the Go API, `python -m app` still starts the processor API directly on port 8000 by default; do not expose the worker port in a Go-backed deployment.

## Current tools and limitations

- **Page organization:** merge, split into page groups, rotate, reorder, extract pages, delete pages, duplicate selected pages, reverse page order, insert blank pages, set PDF page labels, crop all pages by a margin, and scale pages to A4, Letter, or Legal.
- **Conversions:** PNG/JPEG/BMP/TIFF images to PDF, PDF pages to PNG files in a ZIP or raster-image PowerPoint slides, PDF text/Markdown/DOCX/XLSX/accessibility-text export, UTF-8 text to PDF, basic Markdown-to-PDF, text-focused HTML-to-PDF, EPUB-to-PDF, text-focused DOCX/XLSX/PPTX-to-PDF, and SVG-to-PDF. Raster exports are limited to 8 million pixels per page and 40 million rendered pixels per request.
- **Optimization and accessibility:** PDF structural compression, embedded-image recompression, linearize for progressive web viewing, validate pages with the PDF parser, recover and rewrite damaged PDFs, grayscale/deskew/enhance scanned pages, remove visually duplicate pages, and report basic PDF accessibility indicators.
- **Structure and reading order:** add basic page-level structure tags to PDFs with selectable text and set the order of those page elements. Tagging rejects existing structure trees and does not infer headings, describe images, or produce PDF/UA-conforming documents. Reading-order editing only supports PDFs created by this basic tagging operation; it changes logical reading order without moving visible pages.
- **Annotations:** centered text watermark, positioned text stamps, headers/footers with optional page counts, page numbers, exact-text highlighting and redaction, comments, and rectangle drawing.
- **Comparison:** compare extracted text, generate a side-by-side page PDF, or create a thresholded visual-difference PDF. Each comparison requires exactly two input PDFs, in original-then-revised order.
- **Security and forms:** encrypt PDFs with separate user/owner passwords, decrypt with the document password, apply basic reader-enforced permission profiles, sign or certify using a per-request PFX/P12 upload, verify embedded digital signatures, fill fields by name, flatten supported text/choice fields, and remove hidden metadata, links, scripts, attachments, and form data.
- **Attachments:** download embedded attachments in a ZIP or remove all embedded attachments.
- **Metadata:** update title, author, subject, and keywords, or clear standard and XMP metadata.
- **Batch processing:** run up to 10 validated PDF-to-PDF operations in order as one temporary request.
- **Workflow builder and presets:** visually compose up to 10 supported PDF-to-PDF steps, run them as one request, and save/load/delete named workflow settings in this browser. Presets contain operation names and settings only; they do not include PDFs, outputs, or server job history and are not synced to another browser.
- **Watch folder:** select a folder in a supported browser and process newly added or changed top-level PDFs after they remain stable across two 3-second checks while the page remains open. Existing PDFs are skipped. Choose a single operation from the safe, no-extra-options list; each selected PDF is uploaded to the processing server. Failed files are not retried until changed or added again. The folder selection is not saved, and the ten most recent output links remain available in the page until you leave the tool.
- **Local job history:** browse and clear up to 50 recent jobs saved in this browser. History contains filenames and job metadata only; it cannot restore or download a document.

Page rasterization in grayscale intentionally removes selectable text/vector content; duplicate detection may remove pages with identical visual output even if their underlying text differs. Page resizing may lose annotations, forms, and document structure. Operations that rewrite a PDF can invalidate existing digital signatures. Compression is not guaranteed to reduce file size. The application keeps inputs and outputs in process memory after the upload buffer is read and returns outputs directly; it does not provide durable storage, authentication, user accounts, or a public hosted service.

Deskew and scan enhancement rasterize pages at 120 DPI, so selectable text, vector detail, and form fields are not preserved. Scan processing is limited to 100 pages, 8 million pixels per page, and 40 million rendered pixels per request.

Text highlighting and redaction only find exact, selectable text and may not cover text embedded in images. Review redacted output before sharing. PDF permission flags are enforced by reader applications and are not strong access control. Comparison reports render pages at 72 DPI; visual differences are thresholded and can omit subtle changes.

Form filling supports text, choice, checkbox, and radio fields. Flattening supports only text and choice fields and replaces their appearance with basic static text; unsupported widget types are rejected rather than silently discarded.

Sanitization is intentionally broad: it also removes links and resets form data, so review the output before distributing it.

Digital signing and certification require a valid PKCS#12 (`.p12` or `.pfx`) certificate and its password for each request. The certificate is processed with the PDF in request memory and is not saved by the application. Signature verification reports cryptographic integrity separately from certificate trust; trust depends on trust roots and revocation data configured on the processing server.

The accessibility report checks a few document signals only; it is not a PDF/UA conformance validator.

HTML conversion intentionally extracts text and ignores scripts, styles, external resources, and images; it is not a browser-faithful page renderer. EPUB conversion follows the book's spine order and similarly preserves chapter text only, not original styling or images.

Word and spreadsheet conversion supports modern `.docx` and `.xlsx` files only and extracts readable text/cell values; it does not preserve complex page layout, images, fonts, styling, charts, or table structure from PDFs.

PowerPoint-to-PDF extracts slide text and table values into a text-focused PDF; it does not render the original slide layout, images, charts, fonts, or animations. PDF-to-PowerPoint creates one non-editable 96-DPI page image per slide, limited to 100 pages, 8 million pixels per page, and 40 million rendered pixels per request.

## Tests

```powershell
go test ./...
python -m unittest discover -s tests -v
```

The Go tests cover API discovery, internal worker dispatch, health/readiness, request limits, and host/origin restrictions. The Python test suite covers implemented PDF operations, API dispatch for every server-side catalog tool, API upload handling, digital signing and certification, invalid page selections, catalog status and excluded-tool discovery, browser-only tool endpoint protection, and raster workload limits.
