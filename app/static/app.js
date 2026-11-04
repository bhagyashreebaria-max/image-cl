const state = {
  tool: "merge",
  files: [],
  pageCount: null,
  catalog: [],
  formFields: [],
  category: "All",
  busy: false,
  ocrAvailable: false,
  workflowTools: [],
  workflowOptions: {},
  workflowSteps: [{ tool: "compress", options: "{}" }],
};

const PRESET_STORAGE_KEY = "papertrail-workflow-presets-v1";
let presetStorageError = "";

const pdfTool = (title, action, note, extra = {}) => ({
  title,
  accept: ".pdf,application/pdf",
  extensions: ["pdf"],
  fileKind: "pdf",
  multiple: false,
  limit: "PDF · up to 80 MiB",
  note,
  action,
  fileType: "PDF",
  ...extra,
});

const toolDetails = {
  merge: {
    title: "Merge PDFs",
    accept: ".pdf,application/pdf",
    multiple: true,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB total",
    note: "Files merge in this order",
    action: "Merge PDFs",
    fileType: "PDF",
  },
  split: {
    title: "Split by range",
    accept: ".pdf,application/pdf",
    multiple: false,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB",
    note: "Use semicolons to make separate parts",
    action: "Split PDF",
    fileType: "PDF",
  },
  rotate: {
    title: "Rotate pages",
    accept: ".pdf,application/pdf",
    multiple: false,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB",
    note: "Rotation applies to every page",
    action: "Rotate PDF",
    fileType: "PDF",
  },
  reorder: {
    title: "Reorder pages",
    accept: ".pdf,application/pdf",
    multiple: false,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB",
    note: "Enter every page once, in the new order",
    action: "Save new order",
    fileType: "PDF",
    extensions: ["pdf"],
    fileKind: "pdf",
  },
  "image-to-pdf": {
    title: "Images to PDF",
    accept: ".png,.jpg,.jpeg,.bmp,.tif,.tiff,image/png,image/jpeg,image/bmp,image/tiff",
    multiple: true,
    subtitle: "or browse your computer",
    limit: "PNG · JPEG · BMP · TIFF · up to 80 MiB total",
    note: "Each image becomes one page",
    action: "Create PDF",
    fileType: "IMAGE",
    extensions: ["png", "jpg", "jpeg", "bmp", "tif", "tiff"],
    fileKind: "image",
  },
  compress: {
    title: "Compress PDF",
    accept: ".pdf,application/pdf",
    multiple: false,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB",
    note: "Compression varies by document",
    action: "Optimize PDF",
    fileType: "PDF",
  },
  "ocr-text": {
    title: "OCR to text",
    accept: ".pdf,application/pdf",
    multiple: false,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB",
    note: "Requires Tesseract installed on the processing server",
    action: "Extract text",
    fileType: "PDF",
    extensions: ["pdf"],
    fileKind: "pdf",
  },
  "ocr-searchable": {
    title: "Create searchable PDF",
    accept: ".pdf,application/pdf",
    multiple: false,
    subtitle: "or browse your computer",
    limit: "PDF · up to 80 MiB",
    note: "Requires Tesseract and language data on the processing server; limited to 100 pages",
    action: "Make searchable",
    fileType: "PDF",
    extensions: ["pdf"],
    fileKind: "pdf",
  },
  "ocr-languages": {
    title: "View installed OCR languages",
    accept: "",
    extensions: [],
    multiple: false,
    noFile: true,
    limit: "No document upload required",
    note: "Lists Tesseract language data installed on the processing server",
    action: "Check server languages",
    fileType: "INFO",
  },
  extract: pdfTool("Extract page ranges", "Extract pages", "Enter the pages to keep in the extracted PDF."),
  "delete-pages": pdfTool("Delete pages", "Remove pages", "The page ranges entered here will be removed."),
  "duplicate-pages": pdfTool("Duplicate pages", "Duplicate pages", "Each selected page will be duplicated immediately after itself."),
  "reverse-pages": pdfTool("Reverse page order", "Reverse pages", "All pages will be placed in reverse order."),
  "insert-blank": pdfTool("Insert blank pages", "Insert blank pages", "A blank page matching the selected page size is inserted after each selection."),
  "page-labels": pdfTool("Add page labels", "Save page labels", "Page labels are PDF navigation metadata; they do not print numbers on the page."),
  "crop-pages": pdfTool("Crop page boundaries", "Crop pages", "Enter an inset in PDF points (72 points = 1 inch)."),
  "resize-pages": pdfTool("Resize page boxes", "Resize pages", "Content is scaled proportionally onto the selected page size."),
  "pdf-to-images": pdfTool("PDF to images", "Export page images", "Each page is rendered as a PNG in a ZIP archive."),
  "pdf-to-text": pdfTool("PDF to text", "Extract text", "Text-based PDFs work best. Scanned pages require OCR."),
  "pdf-to-markdown": pdfTool("PDF to Markdown", "Export Markdown", "Extracts page text with Markdown page headings; complex layout is not reconstructed."),
  "accessible-text": pdfTool("Export accessible text", "Export readable text", "Extracts text blocks in top-to-bottom reading order with page headings."),
  batch: pdfTool("Run a batch of tools", "Run batch", "Runs up to 10 validated PDF-to-PDF steps in order; files are not retained."),
  "workflow-builder": pdfTool("Build a PDF workflow", "Run workflow", "Chain up to 10 supported PDF operations; optional named presets stay in this browser."),
  presets: {
    title: "Saved workflow presets",
    accept: "",
    extensions: [],
    multiple: false,
    noFile: true,
    localOnly: true,
    limit: "No document upload required",
    note: "Presets contain workflow settings only and are stored in this browser",
    action: "Manage presets",
    fileType: "INFO",
  },
  sign: {
    title: "Sign a PDF",
    accept: ".pdf,.p12,.pfx,application/pdf,application/x-pkcs12",
    extensions: ["pdf", "p12", "pfx"],
    multiple: true,
    maxFiles: 2,
    requiresCertificate: true,
    limit: "One PDF + one PFX/P12 certificate · up to 80 MiB total",
    note: "The private-key certificate and password are sent to the configured server for this request only.",
    action: "Sign PDF",
    fileType: "PDF",
  },
  certify: {
    title: "Certify a PDF",
    accept: ".pdf,.p12,.pfx,application/pdf,application/x-pkcs12",
    extensions: ["pdf", "p12", "pfx"],
    multiple: true,
    maxFiles: 2,
    requiresCertificate: true,
    limit: "One PDF + one PFX/P12 certificate · up to 80 MiB total",
    note: "Certification locks later changes; certificate and password are sent to the configured server for this request only.",
    action: "Certify PDF",
    fileType: "PDF",
  },
  "verify-signature": pdfTool("Verify signatures", "Check signatures", "Checks signature integrity; trust depends on the processing server's configured roots and revocation data."),
  "compress-images": pdfTool("Compress embedded images", "Compress images", "Re-encodes eligible images while preserving text and vector content."),
  "text-to-pdf": {
    title: "Text to PDF", accept: ".txt,text/plain", extensions: ["txt"], fileKind: "text",
    multiple: false, limit: "UTF-8 text · up to 80 MiB", note: "Text is laid out onto A4 pages",
    action: "Create PDF", fileType: "TXT",
  },
  "markdown-to-pdf": {
    title: "Markdown to PDF", accept: ".md,.markdown,text/markdown,text/plain", extensions: ["md", "markdown"], fileKind: "markdown",
    multiple: false, limit: "UTF-8 Markdown · up to 80 MiB", note: "Basic headings, lists, and links are converted to readable text",
    action: "Create PDF", fileType: "MD",
  },
  "html-to-pdf": {
    title: "HTML to PDF", accept: ".html,.htm,text/html", extensions: ["html", "htm"], fileKind: "html",
    multiple: false, limit: "UTF-8 HTML · up to 10 MiB", note: "Converts safe HTML text; images, styling, and scripts are intentionally excluded",
    action: "Create PDF", fileType: "HTML",
  },
  "epub-to-pdf": {
    title: "EPUB to PDF", accept: ".epub,application/epub+zip", extensions: ["epub"], fileKind: "epub",
    multiple: false, limit: "EPUB · up to 80 MiB", note: "Converts chapter text in reading order; original styling and images are not retained",
    action: "Create PDF", fileType: "EPUB",
  },
  "word-to-pdf": {
    title: "Word to PDF", accept: ".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    extensions: ["docx"], fileKind: "docx", multiple: false, limit: "DOCX · up to 80 MiB",
    note: "Converts document text without preserving complex layout, styles, or images",
    action: "Create PDF", fileType: "DOCX",
  },
  "pdf-to-word": {
    title: "PDF to Word", accept: ".pdf,application/pdf", extensions: ["pdf"], fileKind: "pdf",
    multiple: false, limit: "PDF · up to 80 MiB", note: "Exports extracted page text into an editable DOCX; page layout is not reconstructed",
    action: "Export DOCX", fileType: "PDF",
  },
  "excel-to-pdf": {
    title: "Excel to PDF", accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    extensions: ["xlsx"], fileKind: "xlsx", multiple: false, limit: "XLSX · up to 80 MiB",
    note: "Converts the first worksheet's cell values to text; formatting and charts are not preserved",
    action: "Create PDF", fileType: "XLSX",
  },
  "slides-to-pdf": {
    title: "Slides to PDF", accept: ".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation",
    extensions: ["pptx"], fileKind: "pptx", multiple: false, limit: "PPTX · up to 80 MiB",
    note: "Extracts slide text into a PDF; slide layout, images, and animations are not preserved",
    action: "Create PDF", fileType: "PPTX",
  },
  "pdf-to-slides": {
    title: "PDF to slides", accept: ".pdf,application/pdf", extensions: ["pdf"], fileKind: "pdf",
    multiple: false, limit: "PDF · up to 80 MiB",
    note: "Creates one slide image per page at 96 DPI; slide text is not editable",
    action: "Create slides", fileType: "PDF",
  },
  "pdf-to-excel": {
    title: "PDF to Excel", accept: ".pdf,application/pdf", extensions: ["pdf"], fileKind: "pdf",
    multiple: false, limit: "PDF · up to 80 MiB", note: "Exports each extracted text line as a row; it does not infer tables",
    action: "Export XLSX", fileType: "PDF",
  },
  linearize: pdfTool("Optimize for web view", "Linearize PDF", "Rebuilds the file for progressive page loading; it may not reduce file size."),
  validate: pdfTool("Validate PDF structure", "Check PDF", "Checks whether the server PDF engine can open every page."),
  pdfua: pdfTool("Basic PDF accessibility check", "Check accessibility", "Reports title, language, tags, and selectable-text indicators; not full PDF/UA certification."),
  grayscale: pdfTool("Convert to grayscale", "Convert to grayscale", "Pages are rasterized; selectable text and vector detail are not retained."),
  deskew: pdfTool("Deskew scanned pages", "Deskew pages", "Estimates small page rotation from scan text lines; output is rasterized."),
  "enhance-scan": pdfTool("Enhance scanned pages", "Enhance scans", "Improves grayscale contrast and sharpness; output is rasterized."),
  deduplicate: pdfTool("Remove duplicate pages", "Remove duplicates", "Compares rendered page appearances at 72 DPI; signatures and interactive content may be lost."),
  watermark: pdfTool("Add a watermark", "Add watermark", "Adds centered text over each page."),
  stamp: pdfTool("Stamp pages", "Stamp PDF", "Adds a bordered text stamp at a selected position on every page."),
  "headers-footers": pdfTool("Add headers and footers", "Add header/footer", "Adds text to every page; optional page numbering is included."),
  "page-numbers": pdfTool("Add page numbers", "Number pages", "Adds a centered page number to the bottom of each page."),
  "extract-attachments": pdfTool("Extract attachments", "Download attachments", "Embedded files are returned in a ZIP archive."),
  "remove-attachments": pdfTool("Remove attachments", "Remove attachments", "Deletes embedded files from the PDF."),
  "edit-metadata": pdfTool("Edit document metadata", "Update metadata", "Only the metadata fields you fill in will be changed."),
  "remove-metadata": pdfTool("Remove document metadata", "Remove metadata", "Clears standard document information and XMP metadata."),
  "fill-forms": pdfTool("Fill PDF forms", "Fill form", "Enter values using the exact field names listed after selecting a PDF."),
  "flatten-forms": pdfTool("Flatten form fields", "Flatten fields", "Converts supported text and choice fields to non-editable page content."),
  "remove-hidden": pdfTool("Remove hidden content", "Sanitize PDF", "Removes embedded files, scripts, metadata, links, thumbnails, and form data."),
  "svg-to-pdf": {
    title: "SVG to PDF", accept: ".svg,image/svg+xml", extensions: ["svg"], fileKind: "svg",
    multiple: false, limit: "SVG · up to 80 MiB", note: "Converts one SVG graphic into a PDF page",
    action: "Create PDF", fileType: "SVG",
  },
  repair: pdfTool("Repair a damaged PDF", "Repair PDF", "Attempts recovery and validates the rewritten document."),
  "compare-text": {
    title: "Compare PDF text", accept: ".pdf,application/pdf", extensions: ["pdf"], fileKind: "pdf",
    multiple: true, maxFiles: 2, limit: "2 PDFs · up to 80 MiB total", note: "Upload the original first, then the revised PDF",
    action: "Compare text", fileType: "PDF",
  },
  "compare-pages": {
    title: "Compare page images", accept: ".pdf,application/pdf", extensions: ["pdf"], fileKind: "pdf",
    multiple: true, maxFiles: 2, limit: "2 PDFs · up to 80 MiB total", note: "Upload the original first, then the revised PDF",
    action: "Compare pages", fileType: "PDF",
  },
  "visual-diff": {
    title: "Create a visual difference report", accept: ".pdf,application/pdf", extensions: ["pdf"], fileKind: "pdf",
    multiple: true, maxFiles: 2, limit: "2 PDFs · up to 80 MiB total", note: "Upload the original first, then the revised PDF",
    action: "Create visual diff", fileType: "PDF",
  },
  highlight: pdfTool("Highlight text", "Highlight text", "Highlights every exact text match. Enter a phrase below."),
  redact: pdfTool("Redact text", "Redact text", "Permanently removes exact text matches from the rewritten PDF."),
  annotate: pdfTool("Add a comment", "Add comment", "Adds a comment annotation to the first page."),
  draw: pdfTool("Draw on pages", "Mark pages", "Draws the same rectangle on every page using PDF points (72 points = 1 inch)."),
  encrypt: pdfTool("Encrypt a PDF", "Encrypt PDF", "Uses different user and owner passwords, each at least 8 characters."),
  decrypt: pdfTool("Decrypt a PDF", "Decrypt PDF", "Enter the current password to remove PDF encryption."),
  permissions: pdfTool("Set document permissions", "Set permissions", "Permissions are reader-enforced restrictions and can be bypassed by some PDF software."),
};

const elements = {
  nav: document.querySelector("#tool-nav"),
  fileInput: document.querySelector("#file-input"),
  dropzone: document.querySelector("#dropzone"),
  dropTitle: document.querySelector("#drop-title"),
  dropSubtitle: document.querySelector("#drop-subtitle"),
  fileLimit: document.querySelector("#file-limit"),
  fileArea: document.querySelector("#file-area"),
  fileList: document.querySelector("#file-list"),
  pageHint: document.querySelector("#page-hint"),
  options: document.querySelector("#option-fields"),
  runRow: document.querySelector(".run-row"),
  settingsNote: document.querySelector("#settings-note"),
  runButton: document.querySelector("#run-button"),
  runLabel: document.querySelector("#run-label"),
  toolTitle: document.querySelector("#tool-title"),
  breadcrumb: document.querySelector("#breadcrumb-tool"),
  notice: document.querySelector("#notice"),
  outputPlaceholder: document.querySelector("#output-placeholder"),
  outputResult: document.querySelector("#output-result"),
  download: document.querySelector("#download-link"),
  resultName: document.querySelector("#result-name"),
  resultMeta: document.querySelector("#result-meta"),
  resultIcon: document.querySelector("#result-file-icon"),
  catalogToggle: document.querySelector("#catalog-toggle"),
  catalogView: document.querySelector("#catalog-view"),
  workbench: document.querySelector("#workbench"),
  catalogSearch: document.querySelector("#catalog-search"),
  catalogFilters: document.querySelector("#catalog-filters"),
  catalogGroups: document.querySelector("#catalog-groups"),
  catalogSummary: document.querySelector("#catalog-summary"),
  privacyNote: document.querySelector(".privacy-note"),
  privacyMessage: document.querySelector("#privacy p"),
  introDescription: document.querySelector(".page-intro .intro-description"),
  introStamp: document.querySelector(".intro-stamp"),
  outputColumn: document.querySelector(".output-column"),
};

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

function setNotice(message, type = "info") {
  elements.notice.textContent = message;
  elements.notice.className = `notice${type === "error" ? " is-error" : ""}${type === "success" ? " is-success" : ""}`;
  elements.notice.hidden = !message;
}

function savedWorkflowPresets() {
  try {
    const stored = JSON.parse(localStorage.getItem(PRESET_STORAGE_KEY) || "[]");
    if (!Array.isArray(stored) || stored.some((preset) =>
      !preset || typeof preset.name !== "string" || !Array.isArray(preset.steps)
    )) {
      throw new Error("stored data has an unexpected format");
    }
    presetStorageError = "";
    return stored;
  } catch (error) {
    presetStorageError = `Saved workflow presets could not be read: ${error.message}`;
    return [];
  }
}

function storeWorkflowPresets(presets) {
  try {
    localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(presets));
    setNotice("Workflow presets updated in this browser. PDF files are not included.", "success");
    return true;
  } catch (error) {
    setNotice(`Workflow preset could not be saved in this browser: ${error.message}`, "error");
    return false;
  }
}

function validateWorkflowSteps(steps) {
  if (!Array.isArray(steps) || steps.length < 1 || steps.length > 10) {
    throw new Error("A workflow must contain between 1 and 10 steps.");
  }
  return steps.map((step) => {
    if (!state.workflowTools.includes(step.tool)) throw new Error("Choose a supported PDF operation for each step.");
    let options;
    try {
      options = JSON.parse(step.options);
    } catch {
      throw new Error(`Options for ${step.tool} must be valid JSON.`);
    }
    if (!options || typeof options !== "object" || Array.isArray(options)) {
      throw new Error(`Options for ${step.tool} must be a JSON object.`);
    }
    return { tool: step.tool, options };
  });
}

function workflowOptionPlaceholder(tool) {
  const examples = {
    "compress-images": { dpi: 150, quality: 70 },
    "crop-pages": { margin: 24 },
    "delete-pages": { ranges: "2,4-5" },
    draw: { x: 50, y: 50, width: 100, height: 60 },
    "duplicate-pages": { ranges: "1,3" },
    "edit-metadata": { title: "Reviewed document" },
    encrypt: { profile: "copy", user_password: "user-pass-1", owner_password: "owner-pass-1" },
    extract: { ranges: "1-3,5" },
    grayscale: { dpi: 120 },
    "headers-footers": { header: "Company", footer: "Draft", page_number: true },
    highlight: { phrase: "Important" },
    "insert-blank": { ranges: "1,3" },
    "page-labels": { prefix: "Appendix ", label_style: "D", first_number: 1 },
    "page-numbers": { first_number: 1 },
    permissions: { profile: "copy", user_password: "user-pass-1", owner_password: "owner-pass-1" },
    redact: { phrase: "Confidential" },
    reorder: { order: "3,1,2" },
    "resize-pages": { page_size: "a4" },
    rotate: { rotation: 90 },
    stamp: { watermark: "APPROVED", stamp_position: "center" },
    watermark: { watermark: "DRAFT" },
  };
  return JSON.stringify(examples[tool] || {});
}

function workflowOptionHint(tool) {
  const options = state.workflowOptions[tool] || [];
  return options.length ? `Allowed options: ${options.join(", ")}` : "This operation takes no options.";
}

function copyWorkflowSteps(steps) {
  return steps.map((step) => ({ tool: step.tool, options: step.options }));
}

function renderWorkflowBuilder() {
  const presets = savedWorkflowPresets();
  if (presetStorageError) {
    const error = document.createElement("p");
    error.className = "option-callout";
    error.textContent = presetStorageError;
    elements.options.append(error);
  }
  const intro = document.createElement("p");
  intro.className = "option-callout";
  intro.textContent = "Each step receives the previous step's PDF output. Options use the same JSON fields as batch processing. Saved presets store settings in this browser only.";
  elements.options.append(intro);

  const presetControls = document.createElement("div");
  presetControls.className = "workflow-preset-controls";
  const presetName = document.createElement("input");
  presetName.type = "text";
  presetName.maxLength = 60;
  presetName.placeholder = "Preset name";
  presetName.setAttribute("aria-label", "Preset name");
  const saveButton = document.createElement("button");
  saveButton.type = "button";
  saveButton.className = "text-button";
  saveButton.textContent = "Save workflow";
  saveButton.addEventListener("click", () => {
    const name = presetName.value.trim();
    if (!name) {
      setNotice("Enter a name before saving this workflow.", "error");
      presetName.focus();
      return;
    }
    try {
      validateWorkflowSteps(state.workflowSteps);
    } catch (error) {
      setNotice(error.message, "error");
      return;
    }
    const current = savedWorkflowPresets().filter((preset) => preset.name !== name);
    current.push({ name, steps: state.workflowSteps });
    storeWorkflowPresets(current);
  });
  presetControls.append(presetName, saveButton);

  const loadLabel = document.createElement("label");
  loadLabel.className = "visually-hidden";
  loadLabel.htmlFor = "workflow-preset-select";
  loadLabel.textContent = "Load saved workflow preset";
  const presetSelect = document.createElement("select");
  presetSelect.id = "workflow-preset-select";
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = "Load a saved preset";
  presetSelect.append(emptyOption);
  presets.forEach((preset) => {
    const option = document.createElement("option");
    option.value = preset.name;
    option.textContent = preset.name;
    presetSelect.append(option);
  });
  const loadButton = document.createElement("button");
  loadButton.type = "button";
  loadButton.className = "text-button";
  loadButton.textContent = "Load";
  loadButton.disabled = presets.length === 0;
  loadButton.addEventListener("click", () => {
    const selected = presets.find((preset) => preset.name === presetSelect.value);
    if (!selected) {
      setNotice("Choose a saved workflow preset to load.", "error");
      return;
    }
    try {
      validateWorkflowSteps(selected.steps);
      state.workflowSteps = copyWorkflowSteps(selected.steps);
      renderOptions();
      setNotice(`Loaded “${selected.name}”. Choose a PDF and run the workflow.`, "success");
    } catch (error) {
      setNotice(`This preset cannot be loaded: ${error.message}`, "error");
    }
  });
  presetControls.append(loadLabel, presetSelect, loadButton);
  elements.options.append(presetControls);

  const steps = document.createElement("div");
  steps.className = "workflow-steps";
  state.workflowSteps.forEach((step, index) => {
    const row = document.createElement("div");
    row.className = "workflow-step";
    const heading = document.createElement("strong");
    heading.textContent = `Step ${index + 1}`;
    const toolSelect = document.createElement("select");
    toolSelect.setAttribute("aria-label", `Step ${index + 1} operation`);
    state.workflowTools.forEach((tool) => {
      const option = document.createElement("option");
      option.value = tool;
      option.textContent = tool;
      toolSelect.append(option);
    });
    toolSelect.value = step.tool;
    toolSelect.addEventListener("change", () => {
      state.workflowSteps[index].tool = toolSelect.value;
      options.placeholder = workflowOptionPlaceholder(toolSelect.value);
      hint.textContent = workflowOptionHint(toolSelect.value);
    });
    const options = document.createElement("textarea");
    options.setAttribute("aria-label", `Step ${index + 1} options JSON`);
    options.value = step.options;
    options.placeholder = workflowOptionPlaceholder(step.tool);
    options.addEventListener("input", () => {
      state.workflowSteps[index].options = options.value;
    });
    const hint = document.createElement("span");
    hint.className = "field-hint";
    hint.textContent = workflowOptionHint(step.tool);
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "text-button";
    removeButton.textContent = "Remove step";
    removeButton.disabled = state.workflowSteps.length <= 1;
    removeButton.addEventListener("click", () => {
      state.workflowSteps.splice(index, 1);
      renderOptions();
    });
    row.append(heading, toolSelect, removeButton, options, hint);
    steps.append(row);
  });
  elements.options.append(steps);

  const addButton = document.createElement("button");
  addButton.type = "button";
  addButton.className = "text-button";
  addButton.textContent = "Add workflow step";
  addButton.disabled = state.workflowSteps.length >= 10;
  addButton.addEventListener("click", () => {
    if (state.workflowSteps.length >= 10) return;
    state.workflowSteps.push({ tool: "compress", options: "{}" });
    renderOptions();
  });
  elements.options.append(addButton);
}

function renderPresetManager() {
  const presets = savedWorkflowPresets();
  if (presetStorageError) {
    const error = document.createElement("p");
    error.className = "option-callout";
    error.textContent = presetStorageError;
    elements.options.append(error);
  }
  const intro = document.createElement("p");
  intro.className = "option-callout";
  intro.textContent = "Presets contain operation settings only. They are stored in this browser and never include PDF files or server job history.";
  elements.options.append(intro);
  if (presets.length === 0) {
    const empty = document.createElement("p");
    empty.className = "option-callout";
    empty.textContent = "No presets saved yet. Build a workflow to create one.";
    elements.options.append(empty);
    return;
  }
  presets.forEach((preset) => {
    const row = document.createElement("div");
    row.className = "workflow-preset-row";
    const details = document.createElement("span");
    details.textContent = `${preset.name} · ${preset.steps.length} steps`;
    const loadButton = document.createElement("button");
    loadButton.type = "button";
    loadButton.className = "text-button";
    loadButton.textContent = "Edit or run";
    loadButton.addEventListener("click", () => {
      try {
        validateWorkflowSteps(preset.steps);
        state.workflowSteps = copyWorkflowSteps(preset.steps);
        setTool("workflow-builder");
      } catch (error) {
        setNotice(`This preset cannot be loaded: ${error.message}`, "error");
      }
    });
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "text-button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => {
      storeWorkflowPresets(savedWorkflowPresets().filter((item) => item.name !== preset.name));
      renderOptions();
    });
    row.append(details, loadButton, deleteButton);
    elements.options.append(row);
  });
}

function processingConnectionError(error) {
  if (error instanceof TypeError && /fetch/i.test(error.message)) {
    return "Could not connect to the processing server. This build is not deployed to a cloud host yet. Start the server with `python -m app` and open its URL, or deploy the app and API together on the same origin.";
  }
  return error.message || "Processing failed. Check the processing server and try again.";
}

function setTool(tool) {
  if (!toolDetails[tool]) return;
  const previousTool = state.tool;
  state.tool = tool;
  state.pageCount = null;
  state.formFields = [];
  const details = toolDetails[tool];
  elements.runRow.hidden = Boolean(details.localOnly);
  const extensions = details.extensions || ["pdf"];
  if (previousTool !== tool && state.files.length > 0) {
    state.files = state.files.filter((file) => {
      const extension = file.name.split(".").pop().toLowerCase();
      return extensions.includes(extension);
    });
    if (details.maxFiles) state.files = state.files.slice(0, details.maxFiles);
  }
  document.querySelectorAll("[data-tool]").forEach((button) => {
    const active = button.dataset.tool === tool;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  elements.toolTitle.textContent = details.title;
  elements.breadcrumb.textContent = details.title;
  elements.fileInput.accept = details.accept;
  elements.fileInput.multiple = details.multiple;
  elements.dropTitle.textContent = details.multiple ? "Drop files here" : "Drop a file here";
  elements.dropSubtitle.textContent = "or browse your computer";
  elements.fileLimit.textContent = details.limit;
  elements.settingsNote.textContent = details.note;
  const ocrNeedsSetup = ["ocr-text", "ocr-searchable", "ocr-languages"].includes(tool) && !state.ocrAvailable;
  const statusTag = document.querySelector("#tool-status");
  statusTag.classList.toggle("needs-setup", ocrNeedsSetup);
  statusTag.classList.toggle("local-only", Boolean(details.localOnly));
  statusTag.lastChild.textContent = details.localOnly
    ? "BROWSER ONLY"
    : ocrNeedsSetup
      ? "TESSERACT NEEDED"
      : "READY TO RUN";
  elements.privacyNote.lastChild.textContent = details.localOnly
    ? "Browser-only preset settings"
    : "Upload to processing server";
  elements.privacyMessage.textContent = details.localOnly
    ? "This tool reads and updates workflow settings in this browser only. It does not upload documents or store server job history."
    : "Files are uploaded to the configured server and buffered temporarily for the request. This app does not keep a job archive; the server operator may retain infrastructure logs or backups.";
  elements.introDescription.textContent = details.localOnly
    ? "Manage saved workflow settings in this browser. Presets never contain your documents or outputs."
    : "Choose a task and upload your files to the configured processing server. Get the finished copy back without managing a complicated PDF suite.";
  elements.introStamp.hidden = Boolean(details.localOnly);
  elements.outputColumn.hidden = Boolean(details.localOnly);
  renderOptions();
  renderFiles();
  clearOutput();
  updateRunButton();
  setNotice("");
}

function clearOutput() {
  const previousUrl = elements.download.dataset.objectUrl;
  if (previousUrl) URL.revokeObjectURL(previousUrl);
  delete elements.download.dataset.objectUrl;
  elements.download.removeAttribute("href");
  elements.outputPlaceholder.hidden = false;
  elements.outputResult.hidden = true;
}

function renderOptions() {
  elements.options.replaceChildren();
  if (state.tool === "workflow-builder") {
    renderWorkflowBuilder();
    return;
  }
  if (state.tool === "presets") {
    renderPresetManager();
    return;
  }
  if (toolDetails[state.tool].noFile) {
    const note = document.createElement("p");
    note.className = "option-callout";
    note.textContent = "This checks the languages currently installed on the processing server. It does not install language packs.";
    elements.options.append(note);
    return;
  }
  const addField = (labelText, id, type, value, options = {}) => {
    const wrapper = document.createElement("div");
    wrapper.className = "field";
    const label = document.createElement("label");
    label.htmlFor = id;
    label.textContent = labelText;
    const input = document.createElement(type === "select" ? "select" : type === "textarea" ? "textarea" : "input");
    input.id = id;
    input.name = id;
    if (type === "select") {
      options.choices.forEach(([choiceValue, choiceLabel]) => {
        const option = document.createElement("option");
        option.value = choiceValue;
        option.textContent = choiceLabel;
        input.append(option);
      });
      input.value = value;
    } else {
      if (type !== "textarea") input.type = type;
      input.value = value;
      if (options.placeholder) input.placeholder = options.placeholder;
      if (options.inputMode) input.inputMode = options.inputMode;
    }
    wrapper.append(label, input);
    if (options.hint) {
      const hint = document.createElement("div");
      hint.className = "field-hint";
      hint.textContent = options.hint;
      wrapper.append(hint);
    }
    elements.options.append(wrapper);
  };

  if (["split", "extract", "delete-pages", "duplicate-pages", "insert-blank"].includes(state.tool)) {
    const config = {
      split: ["Page groups", "1-3; 5; 7-8", "Each semicolon creates a separate PDF in a ZIP."],
      extract: ["Pages to extract", "1-3, 5", "Use commas and ranges; multiple ranges are combined in the entered order."],
      "delete-pages": ["Pages to delete", "2, 4-5", "The operation is stopped if it would remove every page."],
      "duplicate-pages": ["Pages to duplicate", "1, 3", "Each selected page is duplicated directly after its original."],
      "insert-blank": ["Insert after pages", "1, 3", "A blank page matching each selected page size is inserted after it."],
    }[state.tool];
    addField(config[0], "ranges", "text", config[1], { placeholder: config[1], hint: config[2] });
  } else if (state.tool === "rotate") {
    addField("Turn pages", "rotation", "select", "90", {
      choices: [["90", "90° clockwise"], ["180", "180°"], ["270", "90° counter-clockwise"]],
    });
  } else if (state.tool === "reorder") {
    addField("New page order", "order", "text", "", {
      placeholder: state.pageCount ? Array.from({ length: state.pageCount }, (_, index) => index + 1).join(",") : "3, 1, 2",
      inputMode: "numeric",
      hint: state.pageCount ? `Use each page from 1 to ${state.pageCount} exactly once.` : "Choose a PDF to see its page count.",
    });
  } else if (["ocr-text", "ocr-searchable"].includes(state.tool)) {
    addField("OCR language", "language", "select", "eng", {
      choices: [["eng", "English"], ["hin", "Hindi"], ["eng+hin", "English + Hindi"]],
      hint: "The matching Tesseract language data must be installed on the processing server.",
    });
  } else if (state.tool === "page-labels") {
    addField("Label prefix", "prefix", "text", "", { placeholder: "Appendix " });
    addField("Number style", "label_style", "select", "D", {
      choices: [["D", "1, 2, 3"], ["R", "I, II, III"], ["r", "i, ii, iii"], ["A", "A, B, C"], ["a", "a, b, c"], ["", "No numbering"]],
    });
    addField("Start at", "first_number", "number", "1", { hint: "First page number for the PDF page labels." });
  } else if (state.tool === "crop-pages") {
    addField("Inset from each edge (points)", "margin", "number", "24", { hint: "72 points equal 1 inch; the visible page will be cropped on every side." });
  } else if (state.tool === "resize-pages") {
    addField("New page size", "page_size", "select", "a4", {
      choices: [["a4", "A4 portrait"], ["letter", "US Letter portrait"], ["legal", "US Legal portrait"]],
    });
  } else if (state.tool === "pdf-to-images" || state.tool === "grayscale" || state.tool === "compress-images") {
    const choices = state.tool === "grayscale"
      ? [["72", "72 DPI · smaller"], ["100", "100 DPI"], ["120", "120 DPI"], ["150", "150 DPI · sharper"]]
      : state.tool === "compress-images"
        ? [["96", "96 DPI · smallest"], ["120", "120 DPI"], ["150", "150 DPI"], ["200", "200 DPI · sharper"]]
        : [["72", "72 DPI · smaller"], ["120", "120 DPI"], ["150", "150 DPI"], ["200", "200 DPI · sharper"]];
    addField(state.tool === "compress-images" ? "Maximum image resolution" : "Render resolution", "dpi", "select", state.tool === "grayscale" ? "100" : state.tool === "compress-images" ? "150" : "120", { choices });
    if (state.tool === "compress-images") {
      addField("JPEG quality", "quality", "select", "70", {
        choices: [["50", "50 · smaller"], ["60", "60"], ["70", "70 · balanced"], ["80", "80"], ["90", "90 · sharper"]],
      });
    }
  } else if (state.tool === "watermark") {
    addField("Watermark text", "watermark", "text", "", { placeholder: "CONFIDENTIAL" });
  } else if (state.tool === "stamp") {
    addField("Stamp text", "watermark", "text", "APPROVED", { placeholder: "APPROVED" });
    addField("Stamp position", "stamp_position", "select", "center", {
      choices: [["center", "Center"], ["top-left", "Top left"], ["top-right", "Top right"], ["bottom-left", "Bottom left"], ["bottom-right", "Bottom right"]],
    });
  } else if (state.tool === "headers-footers") {
    addField("Header text", "header", "text", "", { placeholder: "Optional header" });
    addField("Footer text", "footer", "text", "", { placeholder: "Optional footer" });
    addField("Include page number", "page_number", "select", "true", {
      choices: [["true", "Yes"], ["false", "No"]],
    });
  } else if (state.tool === "page-numbers") {
    addField("Start numbering at", "first_number", "number", "1");
  } else if (state.tool === "edit-metadata") {
    addField("Title", "title", "text", "");
    addField("Author", "author", "text", "");
    addField("Subject", "subject", "text", "");
    addField("Keywords", "keywords", "text", "");
  } else if (["highlight", "redact"].includes(state.tool)) {
    addField("Exact text to find", "phrase", "text", "", { hint: "Search is case-sensitive and matches selectable PDF text." });
  } else if (state.tool === "annotate") {
    addField("Comment", "comment", "text", "", { hint: "Added as a comment annotation on the first page." });
  } else if (state.tool === "draw") {
    addField("Left (points)", "x", "number", "50");
    addField("Top (points)", "y", "number", "50");
    addField("Width (points)", "width", "number", "100");
    addField("Height (points)", "height", "number", "60");
  } else if (["encrypt", "permissions"].includes(state.tool)) {
    addField("Permissions profile", "profile", "select", "copy", {
      choices: [["view", "View only"], ["print", "Allow printing"], ["copy", "Allow copying and printing"], ["edit", "Allow editing"]],
    });
    addField("User password", "user_password", "password", "", { hint: "At least 8 characters; required to open the protected PDF." });
    addField("Owner password", "owner_password", "password", "", { hint: "At least 8 characters and different from the user password." });
  } else if (state.tool === "decrypt") {
    addField("Current PDF password", "password", "password", "");
  } else if (state.tool === "fill-forms") {
    const names = state.formFields.map((field) => `${field.name} (${field.type})`).join(", ");
    addField("Field values", "field_values", "textarea", "", {
      placeholder: "full_name=Ada Lovelace",
      hint: names ? `Available fields: ${names}` : "Choose a PDF to list its form fields. Enter one field name=value pair per line.",
    });
  } else if (state.tool === "batch") {
    addField("Batch steps (JSON)", "batch_steps", "textarea", "", {
      placeholder: '[{"tool":"rotate","options":{"rotation":90}},{"tool":"watermark","options":{"watermark":"DRAFT"}}]',
      hint: "Use 1–10 PDF-to-PDF tools. Each step may contain only its supported options.",
    });
  } else if (["sign", "certify"].includes(state.tool)) {
    addField("Certificate password", "certificate_password", "password", "");
    addField("Signature field name", "signature_field", "text", "Signature1", { hint: "Use letters, numbers, dots, underscores, or hyphens." });
    addField("Reason", "signature_reason", "text", "", { placeholder: "Approved by document owner" });
  } else {
    const note = document.createElement("p");
    note.className = "option-callout";
    const copy = {
      merge: "Place files in the order you want them combined.",
      "image-to-pdf": "Images are added in the order shown above. Page size follows each image.",
      compress: "The optimizer recompresses PDF structures. Some files may not get smaller.",
      "pdf-to-text": "Text-based PDFs work best. Scanned documents need OCR to recognize image text.",
      "grayscale": "Pages become images. Text selection and vector graphics will not be preserved.",
      deduplicate: "Pages are compared by their rendered appearance at a modest resolution.",
    };
    note.textContent = copy[state.tool] || "";
    elements.options.append(note);
  }
}

function renderFiles() {
  elements.fileList.replaceChildren();
  elements.fileArea.hidden = state.files.length === 0 || toolDetails[state.tool].noFile;
  elements.dropzone.hidden = state.files.length > 0 || toolDetails[state.tool].noFile;
  state.files.forEach((file, index) => {
    const row = document.createElement("li");
    row.className = "file-row";
    const badge = document.createElement("span");
    badge.className = "file-badge";
    const extension = file.name.split(".").pop().toLowerCase();
    badge.textContent = ["p12", "pfx"].includes(extension) ? extension.toUpperCase() : toolDetails[state.tool].fileType;
    const details = document.createElement("span");
    details.className = "file-details";
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    const size = document.createElement("span");
    size.className = "file-size";
    size.textContent = formatBytes(file.size);
    details.append(name, size);
    const actions = document.createElement("span");
    actions.className = "file-actions";
    if (state.tool === "merge" || state.tool === "image-to-pdf" || ["compare-text", "compare-pages", "visual-diff"].includes(state.tool)) {
      actions.append(
        fileAction("↑", "Move file up", index === 0, () => moveFile(index, -1)),
        fileAction("↓", "Move file down", index === state.files.length - 1, () => moveFile(index, 1)),
      );
    }
    actions.append(fileAction("×", `Remove ${file.name}`, false, () => removeFile(index), true));
    row.append(badge, details, actions);
    elements.fileList.append(row);
  });
  updateRunButton();
  renderOptions();
  inspectSelectedPdf();
}

function fileAction(text, label, disabled, callback, remove = false) {
  const button = document.createElement("button");
  button.className = `file-action${remove ? " remove" : ""}`;
  button.type = "button";
  button.textContent = text;
  button.setAttribute("aria-label", label);
  button.disabled = disabled;
  button.addEventListener("click", callback);
  return button;
}

function moveFile(index, offset) {
  const target = index + offset;
  if (target < 0 || target >= state.files.length) return;
  [state.files[index], state.files[target]] = [state.files[target], state.files[index]];
  renderFiles();
}

function removeFile(index) {
  state.files.splice(index, 1);
  state.pageCount = null;
  renderFiles();
  if (state.files.length === 0) setNotice("");
}

function updateRunButton() {
  const count = state.files.length;
  const noFile = toolDetails[state.tool].noFile;
  const isComparison = ["compare-text", "compare-pages", "visual-diff"].includes(state.tool);
  const isSignatureTask = toolDetails[state.tool].requiresCertificate;
  const hasCertificatePair = count === 2
    && state.files.filter((file) => file.name.toLowerCase().endsWith(".pdf")).length === 1
    && state.files.filter((file) => /\.(p12|pfx)$/i.test(file.name)).length === 1;
  const enoughFiles = noFile || (isSignatureTask
    ? hasCertificatePair
    : state.tool === "merge" || isComparison
      ? count >= 2
      : count === 1 || state.tool === "image-to-pdf" && count > 0);
  elements.runButton.disabled = state.busy || !enoughFiles;
  elements.runLabel.textContent = state.busy
    ? "Working on your file…"
    : noFile
      ? toolDetails[state.tool].action
    : isSignatureTask && count < 2
      ? "Add one PDF and one certificate"
    : isSignatureTask && !hasCertificatePair
      ? "Choose one PDF and one PFX/P12"
    : count === 0
      ? "Choose files to continue"
      : state.tool === "merge" && count < 2
        ? "Add one more PDF"
        : isComparison && count < 2
          ? "Add a second PDF"
        : toolDetails[state.tool].action;
}

function addFiles(fileList) {
  const incoming = Array.from(fileList || []);
  if (incoming.length === 0) return;
  const details = toolDetails[state.tool];
  const allowedExtensions = details.extensions || ["pdf"];
  if (!details.multiple && incoming.length > 1) {
    setNotice(`${details.title} uses one file at a time. The first file was added.`, "error");
  }
  const selected = details.multiple ? incoming : incoming.slice(0, 1);
  const currentSize = state.files.reduce((sum, file) => sum + file.size, 0);
  const accepted = selected.filter((file) => {
    const extension = file.name.split(".").pop().toLowerCase();
    const valid = allowedExtensions.includes(extension);
    if (!valid) setNotice(`${file.name}: this task does not support that file type.`, "error");
    return valid;
  });
  const addedSize = accepted.reduce((sum, file) => sum + file.size, 0);
  if (currentSize + addedSize > 80 * 1024 * 1024) {
    setNotice("Selected files exceed the 80 MiB server limit. Remove a file and try again.", "error");
    return;
  }
  const combined = details.multiple ? [...state.files, ...accepted] : accepted;
  if (details.maxFiles && combined.length > details.maxFiles) {
    setNotice(`${details.title} uses exactly ${details.maxFiles} files. Remove extras and try again.`, "error");
    return;
  }
  state.files = combined;
  state.pageCount = null;
  clearOutput();
  renderFiles();
  if (accepted.length > 0) setNotice("");
}

async function inspectSelectedPdf() {
  const wantsPageCount = ["split", "reorder", "extract", "delete-pages", "duplicate-pages", "insert-blank"].includes(state.tool);
  const wantsFormFields = state.tool === "fill-forms";
  if ((!wantsPageCount && !wantsFormFields) || state.files.length !== 1 || !state.files[0].name.toLowerCase().endsWith(".pdf")) {
    elements.pageHint.textContent = "";
    return;
  }
  const inspectedTool = state.tool;
  const inspectedFile = state.files[0];
  const form = new FormData();
  form.append("file", inspectedFile);
  try {
    const response = await fetch("/api/inspect", { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not read the PDF.");
    if (state.tool === inspectedTool && state.files.length === 1 && state.files[0] === inspectedFile) {
      if (wantsPageCount) {
        state.pageCount = result.page_count;
        elements.pageHint.textContent = `${result.page_count} ${result.page_count === 1 ? "page" : "pages"}`;
      } else {
        state.formFields = result.fields || [];
        elements.pageHint.textContent = state.formFields.length
          ? `${state.formFields.length} form ${state.formFields.length === 1 ? "field" : "fields"} found`
          : "No fillable fields found in this PDF";
      }
      renderOptions();
    }
  } catch (error) {
    elements.pageHint.textContent = "";
    setNotice(processingConnectionError(error), "error");
  }
}

async function runTool() {
  if (elements.runButton.disabled) return;
  let workflowSteps;
  if (state.tool === "workflow-builder") {
    try {
      workflowSteps = validateWorkflowSteps(state.workflowSteps);
    } catch (error) {
      setNotice(error.message, "error");
      return;
    }
  }
  state.busy = true;
  updateRunButton();
  setNotice(toolDetails[state.tool].requiresCertificate
    ? "Uploading the PDF and certificate for this signature request. The app does not keep a job or certificate archive."
    : toolDetails[state.tool].noFile
    ? "Checking OCR language data installed on the processing server."
    : "Uploading to the configured processing server. Files are held temporarily while the task runs.");
  const form = new FormData();
  state.files.forEach((file) => form.append("files", file, file.name));
  [
    "ranges", "rotation", "order", "language", "prefix", "label_style", "first_number",
    "margin", "page_size", "dpi", "quality", "watermark", "stamp_position", "header", "footer", "page_number",
    "title", "author", "subject", "keywords",
    "profile", "user_password", "owner_password", "password", "phrase", "comment",
    "field_values",
    "batch_steps",
    "certificate_password", "signature_field", "signature_reason",
    "x", "y", "width", "height",
  ].forEach((name) => {
    const field = document.getElementById(name);
    if (field) form.append(name, field.value);
  });
  if (workflowSteps) form.set("batch_steps", JSON.stringify(workflowSteps));
  try {
    const response = toolDetails[state.tool].noFile
      ? await fetch("/api/ocr-languages")
      : await fetch(`/api/process/${state.tool === "workflow-builder" ? "batch" : state.tool}`, { method: "POST", body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `The task failed with status ${response.status}.`);
    }
    const blob = await response.blob();
    const outputUrl = URL.createObjectURL(blob);
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/i);
    const filename = match ? match[1] : "processed-document";
    const inputBytes = Number(response.headers.get("x-input-bytes") || 0);
    const outputBytes = Number(response.headers.get("x-output-bytes") || blob.size);
    const previousUrl = elements.download.dataset.objectUrl;
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    elements.download.dataset.objectUrl = outputUrl;
    elements.download.href = outputUrl;
    elements.download.download = filename;
    elements.resultName.textContent = filename;
    elements.resultIcon.textContent = filename.split(".").pop().toUpperCase();
    elements.resultMeta.textContent = state.tool === "compress"
      ? `${formatBytes(inputBytes)} → ${formatBytes(outputBytes)}`
      : `${formatBytes(outputBytes)} · ready to download`;
    elements.outputPlaceholder.hidden = true;
    elements.outputResult.hidden = false;
    const sizeNote = state.tool === "compress" && outputBytes >= inputBytes
      ? "This file did not get smaller; the original is likely already optimized."
      : "Your processed file is ready to download from the server response.";
    setNotice(sizeNote, "success");
  } catch (error) {
    setNotice(processingConnectionError(error), "error");
  } finally {
    state.busy = false;
    updateRunButton();
  }
}

function renderCatalog() {
  const query = elements.catalogSearch.value.trim().toLowerCase();
  const categories = ["All", ...new Set(state.catalog.map((item) => item.category))];
  elements.catalogFilters.replaceChildren();
  categories.forEach((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `filter-button${state.category === category ? " is-active" : ""}`;
    button.textContent = category;
    button.setAttribute("aria-pressed", String(state.category === category));
    button.addEventListener("click", () => {
      state.category = category;
      renderCatalog();
    });
    elements.catalogFilters.append(button);
  });
  const visible = state.catalog.filter((item) => {
    const matchesText = `${item.name} ${item.category}`.toLowerCase().includes(query);
    return matchesText && (state.category === "All" || item.category === state.category);
  });
  const grouped = new Map();
  visible.forEach((item) => {
    if (!grouped.has(item.category)) grouped.set(item.category, []);
    grouped.get(item.category).push(item);
  });
  elements.catalogGroups.replaceChildren();
  if (visible.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-search";
    empty.textContent = "No tools match that search. Try another word or clear the category filter.";
    elements.catalogGroups.append(empty);
    return;
  }
  grouped.forEach((items, category) => {
    const section = document.createElement("section");
    section.className = "catalog-group";
    const heading = document.createElement("h3");
    heading.textContent = category;
    const list = document.createElement("div");
    list.className = "catalog-group-list";
    items.forEach((item) => {
        const row = toolDetails[item.id] ? document.createElement("button") : document.createElement("div");
      row.className = "catalog-item";
        if (toolDetails[item.id]) {
          row.type = "button";
          row.title = item.status === "setup-required"
            ? "Open this tool to see the required server setup."
            : `Open ${item.name}`;
          row.addEventListener("click", () => {
            showCatalog(false);
            setTool(item.id);
          });
        }
      const name = document.createElement("span");
      name.textContent = item.name;
      const status = document.createElement("span");
      status.className = `catalog-item-status ${item.status}`;
      status.textContent = item.status === "ready" ? "Ready" : item.status === "setup-required" ? "Setup needed" : "Planned";
      row.append(name, status);
      list.append(row);
    });
    section.append(heading, list);
    elements.catalogGroups.append(section);
  });
}

function showCatalog(show) {
  elements.catalogView.hidden = !show;
  elements.workbench.hidden = show;
  document.querySelector(".page-intro").hidden = show;
  if (show) renderCatalog();
}

async function initialize() {
  renderOptions();
  const statusLabel = document.querySelector("#engine-status");
  try {
    const [statusResponse, catalogResponse] = await Promise.all([fetch("/api/status"), fetch("/api/tools")]);
    if (!statusResponse.ok || !catalogResponse.ok) throw new Error("The processing server is not responding.");
    const status = await statusResponse.json();
    state.ocrAvailable = status.ocr_available;
    state.workflowTools = status.workflow_tools;
    state.workflowOptions = status.workflow_options;
    state.catalog = await catalogResponse.json();
    const readyCount = state.catalog.filter((tool) => tool.status === "ready").length;
    const setupCount = state.catalog.filter((tool) => tool.status === "setup-required").length;
    const plannedCount = state.catalog.filter((tool) => tool.status === "planned").length;
    statusLabel.textContent = status.ocr_available ? "PDF + OCR engines ready" : "PDF engine ready · OCR setup needed";
    if (["ocr-text", "ocr-searchable", "ocr-languages"].includes(state.tool) && !status.ocr_available) setTool(state.tool);
    document.querySelector("#catalog-count").textContent = `${state.catalog.length}`;
    elements.catalogSummary.textContent = `${readyCount} ready · ${setupCount} need server setup · ${plannedCount} planned.`;
    document.querySelector("#footer-count").textContent = `${readyCount} ready · ${setupCount} need setup · ${plannedCount} planned`;
    document.querySelector("#ocr-nav").title = status.ocr_available
      ? "OCR is ready on this computer."
        : "Install Tesseract and language data on the processing server to use OCR.";
    renderCatalog();
  } catch {
    statusLabel.textContent = "Processing server unavailable";
    setNotice("The processing server could not be reached. Check the server address and deployment status, then reload this page.", "error");
  }
}

elements.nav.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tool]");
  if (!button) return;
  showCatalog(false);
  setTool(button.dataset.tool);
});
elements.dropzone.addEventListener("click", () => elements.fileInput.click());
elements.dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    elements.fileInput.click();
  }
});
elements.dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  elements.dropzone.classList.add("is-dragging");
});
elements.dropzone.addEventListener("dragleave", () => elements.dropzone.classList.remove("is-dragging"));
elements.dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  elements.dropzone.classList.remove("is-dragging");
  addFiles(event.dataTransfer.files);
});
elements.fileInput.addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});
document.querySelector("#add-files").addEventListener("click", () => elements.fileInput.click());
elements.runButton.addEventListener("click", runTool);
elements.catalogToggle.addEventListener("click", () => showCatalog(elements.catalogView.hidden));
elements.catalogSearch.addEventListener("input", renderCatalog);
window.addEventListener("beforeunload", () => {
  const outputUrl = elements.download.dataset.objectUrl;
  if (outputUrl) URL.revokeObjectURL(outputUrl);
});

setTool("merge");
initialize();
