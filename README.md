# Papertrail PDF Toolkit

Papertrail is an original server-side PDF workbench. It currently provides 68 implemented operations across page organization, conversions, optimization, OCR, accessibility checks, annotations, comparison, document security, attachments, metadata, and workflow composition. The searchable 73-item tool library distinguishes working tools from the remaining roadmap; planned tools are not presented as working features.

The processing API receives uploaded documents. It reads the request into memory for processing on a worker thread, closes each upload buffer when the request finishes, and streams the generated output back in the response. The application does not create a job archive or intentionally persist uploaded/output documents. Framework upload buffering may use temporary server storage before the endpoint reads a file. Reverse proxies, hosting platforms, backups, and infrastructure logs are outside this application's retention controls and must be configured by the operator.

## Run locally

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app
```

Open `http://127.0.0.1:8000`. The app accepts up to 80 MiB of input per operation. Most operations do not accept encrypted PDFs. Use Decrypt PDF first when you have the document password.

## Deploy the processing server

The app is provider-neutral; it does not create or select cloud resources. Put it behind HTTPS and an authenticated reverse proxy before exposing it beyond a trusted network. The application has allowed-host and allowed-origin checks, but those are not user authentication, rate limiting, or a substitute for deployment controls.

Set these variables for a deployment. Replace the sample host and origin with the actual public hostname:

```text
PAPERTRAIL_HOST=0.0.0.0
PAPERTRAIL_PORT=8000
PAPERTRAIL_ALLOWED_HOSTS=pdf.example.com
PAPERTRAIL_ALLOWED_ORIGINS=https://pdf.example.com
```

Configure the reverse proxy's request-body limit slightly above 80 MiB to account for multipart framing, and avoid logging request bodies. Protect the service with authentication, rate limits, and appropriate storage/temporary-directory policies. The `/health` endpoint can be used for a deployment health check.

The browser calls relative `/api/...` paths, so the frontend and processing API must be served from the same origin. If the interface shows a fetch error, open `/health` on that same host first; it should return `{"status":"ok"}`. A static repository preview is not a running processing server. For local use, start it with `python -m app` and open `http://127.0.0.1:8000`. For a hosted instance, deploy the app and API together and set the allowed host/origin variables above.

## OCR setup

OCR uses the Tesseract executable installed on the processing server, plus its language data. English is the default; Hindi and English+Hindi are selectable when the corresponding language packs are installed. The app can list installed language packs but does not install them. Without Tesseract, the OCR tools report that setup is required and the rest of the toolkit remains available. OCR can export recognized text to `.txt` or create a raster-backed searchable PDF, with a 100-page per-operation cap for searchable output.

## Current tools and limitations

- **Page organization:** merge, split into page groups, rotate, reorder, extract pages, delete pages, duplicate selected pages, reverse page order, insert blank pages, set PDF page labels, crop all pages by a margin, and scale pages to A4, Letter, or Legal.
- **Conversions:** PNG/JPEG/BMP/TIFF images to PDF, PDF pages to PNG files in a ZIP or raster-image PowerPoint slides, PDF text/Markdown/DOCX/XLSX/accessibility-text export, UTF-8 text to PDF, basic Markdown-to-PDF, text-focused HTML-to-PDF, EPUB-to-PDF, text-focused DOCX/XLSX/PPTX-to-PDF, and SVG-to-PDF.
- **Optimization and accessibility:** PDF structural compression, embedded-image recompression, linearize for progressive web viewing, validate pages with the PDF parser, recover and rewrite damaged PDFs, grayscale/deskew/enhance scanned pages, remove visually duplicate pages, and report basic PDF accessibility indicators.
- **OCR:** export recognized text, create searchable PDFs, and list installed Tesseract language packs on the processing server. OCR tools are setup-required until Tesseract is detected.
- **Annotations:** centered text watermark, positioned text stamps, headers/footers with optional page counts, page numbers, exact-text highlighting and redaction, comments, and rectangle drawing.
- **Comparison:** compare extracted text, generate a side-by-side page PDF, or create a thresholded visual-difference PDF. Each comparison requires exactly two input PDFs, in original-then-revised order.
- **Security and forms:** encrypt PDFs with separate user/owner passwords, decrypt with the document password, apply basic reader-enforced permission profiles, sign or certify using a per-request PFX/P12 upload, verify embedded digital signatures, fill fields by name, flatten supported text/choice fields, and remove hidden metadata, links, scripts, attachments, and form data.
- **Attachments:** download embedded attachments in a ZIP or remove all embedded attachments.
- **Metadata:** update title, author, subject, and keywords, or clear standard and XMP metadata.
- **Batch processing:** run up to 10 validated PDF-to-PDF operations in order as one temporary request.
- **Workflow builder and presets:** visually compose up to 10 supported PDF-to-PDF steps, run them as one request, and save/load/delete named workflow settings in this browser. Presets contain operation names and settings only; they do not include PDFs, outputs, or server job history and are not synced to another browser.

Page rasterization in grayscale intentionally removes selectable text/vector content; duplicate detection may remove pages with identical visual output even if their underlying text differs. Page resizing may lose annotations, forms, and document structure. Operations that rewrite a PDF can invalidate existing digital signatures. Compression is not guaranteed to reduce file size. The application keeps inputs and outputs in process memory after the upload buffer is read and returns outputs directly; it does not provide durable storage, authentication, user accounts, or a public hosted service.

Deskew and scan enhancement rasterize pages at 120 DPI, so selectable text, vector detail, and form fields are not preserved. Scan processing is limited to 100 pages and 8 million pixels per page.

Text highlighting and redaction only find exact, selectable text and may not cover text embedded in images. Review redacted output before sharing. PDF permission flags are enforced by reader applications and are not strong access control. Comparison reports render pages at 72 DPI; visual differences are thresholded and can omit subtle changes.

Form filling supports text, choice, checkbox, and radio fields. Flattening supports only text and choice fields and replaces their appearance with basic static text; unsupported widget types are rejected rather than silently discarded.

Sanitization is intentionally broad: it also removes links and resets form data, so review the output before distributing it.

Digital signing and certification require a valid PKCS#12 (`.p12` or `.pfx`) certificate and its password for each request. The certificate is processed with the PDF in request memory and is not saved by the application. Signature verification reports cryptographic integrity separately from certificate trust; trust depends on trust roots and revocation data configured on the processing server.

The accessibility report checks a few document signals only; it is not a PDF/UA conformance validator.

HTML conversion intentionally extracts text and ignores scripts, styles, external resources, and images; it is not a browser-faithful page renderer. EPUB conversion follows the book's spine order and similarly preserves chapter text only, not original styling or images.

Word and spreadsheet conversion supports modern `.docx` and `.xlsx` files only and extracts readable text/cell values; it does not preserve complex page layout, images, fonts, styling, charts, or table structure from PDFs.

PowerPoint-to-PDF extracts slide text and table values into a text-focused PDF; it does not render the original slide layout, images, charts, fonts, or animations. PDF-to-PowerPoint creates one non-editable 96-DPI page image per slide, limited to 100 pages, 8 million pixels per page, and 40 million rendered pixels per request.

## Remaining roadmap

The catalog still marks five entries as planned: PDF/A conversion, structure tagging, reading-order editing, watch-folder processing, and job history. They are not active features. PDF/A and tagged-PDF work need standards-aware implementation and validation rather than metadata-only changes; watch-folder processing would require explicitly scoped server filesystem access; job history is intentionally excluded because this app does not keep a job archive.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The test suite covers implemented PDF operations, API upload handling, digital signing and certification, invalid page selections, catalog status, and the missing-Tesseract error path.
