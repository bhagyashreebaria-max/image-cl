const state = {
  tool: "merge",
  files: [],
  pageCount: null,
  catalog: [],
  formFields: [],
  category: "All",
  busy: false,
  structureCount: 0,
  workflowTools: [],
  workflowOptions: {},
  workflowSteps: [{ tool: "compress", options: "{}" }],
};

const PRESET_STORAGE_KEY = "papertrail-workflow-presets-v1";
const JOB_HISTORY_STORAGE_KEY = "papertrail-job-history-v1";
const MAX_JOB_HISTORY = 50;
const MAX_WATCH_RESULTS = 10;
const WATCH_FOLDER_TOOLS = new Set([
  "compress", "deduplicate", "linearize", "repair", "remove-attachments",
  "remove-hidden", "remove-metadata", "reverse-pages", "validate",
]);
let presetStorageError = "";
let folderWatch = { active: false, results: [] };
let jobHistoryError = "";

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
  "tag-pdf": {
    ...pdfTool("Add basic structure tags", "Add page-level tags", "Adds one paragraph tag per text page and sets the document language. This is not PDF/UA conformance tagging."),
  },
  "reading-order": pdfTool("Set tagged page reading order", "Save reading order", "Reorders basic page-level structure tags for assistive reading; the visible page order does not change."),
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
  "watch-folder": {
    title: "Watch a folder for new PDFs",
    accept: "",
    extensions: [],
    multiple: false,
    noFile: true,
    localOnly: true,
    browserManaged: true,
    limit: "Choose a folder in this browser",
    note: "New and changed PDFs in the selected folder are processed while this page stays open",
    action: "Start watching",
    fileType: "INFO",
  },
  history: {
    title: "Browse local job history",
    accept: "",
    extensions: [],
    multiple: false,
    noFile: true,
    localOnly: true,
    limit: "No document upload required",
    note: "History stores file names and job details in this browser only",
    action: "View history",
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

function loadJobHistory() {
  try {
    const stored = JSON.parse(localStorage.getItem(JOB_HISTORY_STORAGE_KEY) || "[]");
    const valid = Array.isArray(stored) && stored.every((entry) =>
      entry
      && typeof entry.id === "string"
      && typeof entry.toolId === "string"
      && typeof entry.toolName === "string"
      && Array.isArray(entry.inputNames)
      && entry.inputNames.every((name) => typeof name === "string")
      && typeof entry.outputName === "string"
      && typeof entry.completedAt === "string"
      && Number.isFinite(entry.inputBytes)
      && Number.isFinite(entry.outputBytes)
    );
    if (!valid) throw new Error("stored data has an unexpected format");
    jobHistoryError = "";
    return stored.slice(0, MAX_JOB_HISTORY);
  } catch (error) {
    jobHistoryError = `Local job history could not be read: ${error.message}`;
    return [];
  }
}

function recordJobHistory({ toolId, toolName, inputNames, outputName, inputBytes, outputBytes }) {
  const entries = loadJobHistory();
  if (jobHistoryError) return false;
  const entry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    toolId,
    toolName,
    inputNames: inputNames.slice(0, 10).map((name) => name.slice(0, 256)),
    outputName: outputName.slice(0, 256),
    completedAt: new Date().toISOString(),
    inputBytes,
    outputBytes,
  };
  try {
    localStorage.setItem(JOB_HISTORY_STORAGE_KEY, JSON.stringify([entry, ...entries].slice(0, MAX_JOB_HISTORY)));
    return true;
  } catch (error) {
    jobHistoryError = `Local job history could not be saved: ${error.message}`;
    return false;
  }
}

function renderJobHistory() {
  const entries = loadJobHistory();
  const intro = document.createElement("p");
  intro.className = "option-callout";
  intro.textContent = jobHistoryError
    ? jobHistoryError
    : "The most recent 50 completed jobs are listed here. Only tool names, file names, timestamps, and sizes are saved; PDF contents and outputs are not.";
  elements.options.append(intro);

  const clearButton = document.createElement("button");
  clearButton.type = "button";
  clearButton.className = "text-button";
  clearButton.textContent = "Clear local history";
  clearButton.disabled = entries.length === 0 && !jobHistoryError;
  clearButton.addEventListener("click", () => {
    try {
      localStorage.removeItem(JOB_HISTORY_STORAGE_KEY);
      jobHistoryError = "";
      renderOptions();
      setNotice("Local job history cleared. PDF files were never stored.", "success");
    } catch (error) {
      jobHistoryError = `Local job history could not be cleared: ${error.message}`;
      setNotice(jobHistoryError, "error");
    }
  });
  elements.options.append(clearButton);

  if (entries.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-search";
    empty.textContent = jobHistoryError ? "History is unavailable in this browser." : "No completed jobs yet.";
    elements.options.append(empty);
    return;
  }

  const list = document.createElement("ol");
  list.className = "job-history-list";
  entries.forEach((entry) => {
    const row = document.createElement("li");
    row.className = "job-history-entry";
    const heading = document.createElement("strong");
    heading.textContent = entry.toolName;
    const files = document.createElement("span");
    files.textContent = `${entry.inputNames.join(", ")} → ${entry.outputName}`;
    const meta = document.createElement("span");
    const date = new Date(entry.completedAt);
    meta.textContent = `${Number.isNaN(date.getTime()) ? "Unknown date" : date.toLocaleString()} · ${formatBytes(entry.inputBytes)} → ${formatBytes(entry.outputBytes)}`;
    row.append(heading, files, meta);
    list.append(row);
  });
  elements.options.append(list);
}

function renderFolderWatcher() {
  const intro = document.createElement("p");
  intro.className = "option-callout";
  intro.textContent = "Choose one operation, then select a folder. This browser checks PDFs directly inside that folder every 3 seconds, skips files already there, and processes files that are added or changed while the page is open.";
  elements.options.append(intro);

  const field = document.createElement("div");
  field.className = "field";
  const label = document.createElement("label");
  label.htmlFor = "watch-operation";
  label.textContent = "Operation for new PDFs";
  const select = document.createElement("select");
  select.id = "watch-operation";
  select.disabled = folderWatch.active;
  const operations = state.catalog.filter((tool) => WATCH_FOLDER_TOOLS.has(tool.id) && tool.status === "ready");
  operations.forEach((tool) => {
    const option = document.createElement("option");
    option.value = tool.id;
    option.textContent = tool.name;
    select.append(option);
  });
  select.value = folderWatch.active ? folderWatch.toolId : "compress";
  field.append(label, select);
  elements.options.append(field);

  const controls = document.createElement("div");
  controls.className = "watch-folder-controls";
  const start = document.createElement("button");
  start.type = "button";
  start.className = "text-button";
  start.id = "watch-start";
  start.textContent = "Choose folder and start";
  start.hidden = folderWatch.active;
  start.disabled = typeof window.showDirectoryPicker !== "function" || operations.length === 0;
  start.addEventListener("click", () => startFolderWatch(select.value));
  const stop = document.createElement("button");
  stop.type = "button";
  stop.className = "text-button";
  stop.id = "watch-stop";
  stop.textContent = "Stop watching";
  stop.hidden = !folderWatch.active;
  stop.addEventListener("click", () => {
    stopFolderWatch(false);
    renderOptions();
    setNotice("Folder watching stopped. Existing result links remain available until you leave this tool.", "success");
  });
  controls.append(start, stop);
  elements.options.append(controls);

  const status = document.createElement("p");
  status.className = "watch-folder-status";
  status.id = "watch-status";
  status.textContent = typeof window.showDirectoryPicker !== "function"
    ? "Folder watching requires a browser with the File System Access API and a secure application origin."
    : folderWatch.active
      ? `Watching “${folderWatch.handle.name}”. The selected folder is accessible only while this page stays open.`
      : "No folder is being watched.";
  elements.options.append(status);

  const results = document.createElement("ol");
  results.className = "watch-folder-results";
  results.id = "watch-results";
  folderWatch.results.forEach((result) => {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = result.url;
    link.download = result.outputName;
    link.textContent = `${result.inputName} → ${result.outputName}`;
    row.append(link);
    results.append(row);
  });
  elements.options.append(results);
}

async function readWatchedFolder(handle) {
  const files = new Map();
  for await (const [name, entry] of handle.entries()) {
    if (entry.kind !== "file" || !name.toLowerCase().endsWith(".pdf")) continue;
    const file = await entry.getFile();
    files.set(name, { file, signature: `${file.size}:${file.lastModified}` });
  }
  return files;
}

async function startFolderWatch(toolId) {
  if (typeof window.showDirectoryPicker !== "function") {
    setNotice("This browser does not support folder watching. Use a current Chromium-based browser on a secure origin.", "error");
    return;
  }
  try {
    const handle = await window.showDirectoryPicker({ mode: "read" });
    const initialFiles = await readWatchedFolder(handle);
    folderWatch = {
      active: true,
      handle,
      toolId,
      seen: new Map(Array.from(initialFiles, ([name, item]) => [name, item.signature])),
      pending: new Map(),
      timer: null,
      processing: false,
      results: [],
    };
    folderWatch.timer = window.setInterval(pollWatchedFolder, 3000);
    renderOptions();
    setNotice(`Watching “${handle.name}”. Existing PDFs were skipped; new and changed PDFs will use ${state.catalog.find((tool) => tool.id === toolId)?.name || toolId}.`, "success");
  } catch (error) {
    if (error.name === "AbortError") return;
    setNotice(`Could not start folder watching: ${error.message}`, "error");
  }
}

function stopFolderWatch(clearResults) {
  if (folderWatch.timer) window.clearInterval(folderWatch.timer);
  folderWatch.active = false;
  folderWatch.timer = null;
  if (clearResults) {
    folderWatch.results.forEach((result) => URL.revokeObjectURL(result.url));
    folderWatch.results = [];
  }
}

async function pollWatchedFolder() {
  if (!folderWatch.active || folderWatch.processing) return;
  folderWatch.processing = true;
  try {
    const currentFiles = await readWatchedFolder(folderWatch.handle);
    for (const name of folderWatch.seen.keys()) {
      if (!currentFiles.has(name)) {
        folderWatch.seen.delete(name);
        folderWatch.pending.delete(name);
      }
    }
    const changedFiles = [];
    currentFiles.forEach((item, name) => {
      if (folderWatch.seen.get(name) !== item.signature) {
        folderWatch.seen.set(name, item.signature);
        folderWatch.pending.set(name, { signature: item.signature, stableScans: 1 });
        return;
      }
      const pending = folderWatch.pending.get(name);
      if (pending && pending.signature === item.signature) {
        pending.stableScans += 1;
        if (pending.stableScans >= 2) {
          changedFiles.push({ name, ...item });
          folderWatch.pending.delete(name);
        }
      }
    });
    for (const item of changedFiles) {
      if (!folderWatch.active) break;
      await processWatchedPdf(item.name, item.file);
    }
  } catch (error) {
    stopFolderWatch(false);
    setNotice(`Folder watching stopped because the selected folder is no longer available: ${error.message}`, "error");
    renderOptions();
  } finally {
    folderWatch.processing = false;
  }
}

async function processWatchedPdf(inputName, file) {
  if (file.size > 80 * 1024 * 1024) {
    setNotice(`${inputName} exceeds the 80 MiB server limit and was skipped.`, "error");
    return;
  }
  const form = new FormData();
  form.append("files", file, file.name);
  try {
    const response = await fetch(`/api/process/${folderWatch.toolId}`, { method: "POST", body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `The task failed with status ${response.status}.`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/i);
    const outputName = match ? match[1] : "processed-document";
    const outputBytes = Number(response.headers.get("x-output-bytes") || blob.size);
    const toolId = folderWatch.toolId;
    const toolName = state.catalog.find((tool) => tool.id === toolId)?.name || toolId;
    const saved = recordJobHistory({
      toolId,
      toolName,
      inputNames: [inputName],
      outputName,
      inputBytes: file.size,
      outputBytes,
    });
    if (folderWatch.active && state.tool === "watch-folder") {
      const url = URL.createObjectURL(blob);
      folderWatch.results.push({ inputName, outputName, url });
      while (folderWatch.results.length > MAX_WATCH_RESULTS) {
        URL.revokeObjectURL(folderWatch.results.shift().url);
      }
      renderOptions();
      setNotice(saved
        ? `${inputName} was processed. Its download link is ready below.`
        : `${inputName} was processed, but its local history entry could not be saved.`, saved ? "success" : "error");
    } else if (!saved) {
      setNotice(`A watched PDF was processed, but its local history entry could not be saved: ${jobHistoryError}`, "error");
    }
  } catch (error) {
    setNotice(`${inputName} could not be processed: ${processingConnectionError(error)} It will not be retried until the file changes or is added again.`, "error");
  }
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
  if (previousTool === "watch-folder" && tool !== previousTool) stopFolderWatch(true);
  state.tool = tool;
  state.pageCount = null;
  state.structureCount = 0;
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
  const browserManaged = Boolean(details.browserManaged);
  const statusTag = document.querySelector("#tool-status");
  statusTag.classList.remove("needs-setup");
  statusTag.classList.toggle("local-only", Boolean(details.localOnly));
  statusTag.lastChild.textContent = browserManaged
    ? "BROWSER WATCH"
    : details.localOnly
      ? "BROWSER ONLY"
      : "READY TO RUN";
  elements.privacyNote.lastChild.textContent = browserManaged
    ? "Folder access stays in this browser"
    : details.localOnly
      ? "Browser-only settings and history"
    : "Upload to processing server";
  elements.privacyMessage.textContent = browserManaged
    ? "The selected folder is read by this browser only. New or changed PDFs are uploaded to the configured processing server; the app stores job metadata, not documents or outputs."
    : details.localOnly
      ? "This tool reads and updates settings or job metadata in this browser only. PDF files and outputs are not stored in history."
    : "Files are uploaded to the configured server and buffered temporarily for the request. This app does not keep a job archive; the server operator may retain infrastructure logs or backups.";
  elements.introDescription.textContent = browserManaged
    ? "Watch a folder selected in this browser and process new or changed PDFs with one safe, repeatable operation."
    : tool === "history"
      ? "Review recent processing activity saved in this browser. History never contains the PDF documents or output files."
    : details.localOnly
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
  if (state.tool === "watch-folder") {
    renderFolderWatcher();
    return;
  }
  if (state.tool === "history") {
    renderJobHistory();
    return;
  }
  if (state.tool === "workflow-builder") {
    renderWorkflowBuilder();
    return;
  }
  if (state.tool === "presets") {
    renderPresetManager();
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
  } else if (state.tool === "tag-pdf") {
    addField("Document language", "document_language", "text", "en-US", {
      placeholder: "en-US",
      hint: "Use a BCP 47 language tag that matches the document, such as en-US or hi.",
    });
  } else if (state.tool === "reading-order") {
    const count = state.structureCount;
    addField("Structure order", "structure_order", "text", count ? Array.from({ length: count }, (_, index) => index + 1).join(", ") : "", {
      placeholder: count ? Array.from({ length: count }, (_, index) => index + 1).join(", ") : "Choose a PDF with basic page-level tags",
      inputMode: "numeric",
      hint: count
        ? `Reorder all ${count} tagged page paragraphs exactly once. This changes assistive reading order, not visible page order.`
        : "This tool edits page-level tags created by Add basic structure tags; untagged or complex tagged PDFs are not rewritten.",
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
  state.structureCount = 0;
  renderFiles();
  if (state.files.length === 0) setNotice("");
}

function updateRunButton() {
  const count = state.files.length;
  const noFile = toolDetails[state.tool].noFile;
  const readingOrderUnavailable = state.tool === "reading-order" && !state.structureCount;
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
  elements.runButton.disabled = state.busy || !enoughFiles || readingOrderUnavailable;
  elements.runLabel.textContent = state.busy
    ? "Working on your file…"
    : readingOrderUnavailable
      ? "Choose a PDF with page-level tags"
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
  state.structureCount = 0;
  clearOutput();
  renderFiles();
  if (accepted.length > 0) setNotice("");
}

async function inspectSelectedPdf() {
  const wantsPageCount = ["split", "reorder", "extract", "delete-pages", "duplicate-pages", "insert-blank"].includes(state.tool);
  const wantsFormFields = state.tool === "fill-forms";
  const wantsReadingOrder = state.tool === "reading-order";
  if ((!wantsPageCount && !wantsFormFields && !wantsReadingOrder) || state.files.length !== 1 || !state.files[0].name.toLowerCase().endsWith(".pdf")) {
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
      } else if (wantsReadingOrder) {
        state.structureCount = result.reading_order_count || 0;
        elements.pageHint.textContent = state.structureCount
          ? `${state.structureCount} page-level tags found`
          : "No supported page-level tags found";
      } else {
        state.formFields = result.fields || [];
        elements.pageHint.textContent = state.formFields.length
          ? `${state.formFields.length} form ${state.formFields.length === 1 ? "field" : "fields"} found`
          : "No fillable fields found in this PDF";
      }
      renderOptions();
      updateRunButton();
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
    : "Uploading to the configured processing server. Files are held temporarily while the task runs.");
  const form = new FormData();
  state.files.forEach((file) => form.append("files", file, file.name));
  [
    "ranges", "rotation", "order", "language", "prefix", "label_style", "first_number",
    "document_language", "structure_order",
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
    const response = await fetch(`/api/process/${state.tool === "workflow-builder" ? "batch" : state.tool}`, { method: "POST", body: form });
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
    const historySaved = toolDetails[state.tool].noFile || recordJobHistory({
      toolId: state.tool === "workflow-builder" ? "batch" : state.tool,
      toolName: toolDetails[state.tool].title,
      inputNames: state.files.map((file) => file.name),
      outputName: filename,
      inputBytes,
      outputBytes,
    });
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
    setNotice(historySaved
      ? sizeNote
      : `${sizeNote} Local history could not be saved: ${jobHistoryError}`, historySaved ? "success" : "error");
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
          row.title = `Open ${item.name}`;
          row.addEventListener("click", () => {
            showCatalog(false);
            setTool(item.id);
          });
        }
      const name = document.createElement("span");
      name.textContent = item.name;
      const status = document.createElement("span");
      status.className = `catalog-item-status ${item.status}`;
      status.textContent = item.status === "ready" ? "Ready" : "Planned";
      row.append(name, status);
      list.append(row);
    });
    section.append(heading, list);
    elements.catalogGroups.append(section);
  });
}

function showCatalog(show) {
  if (show && state.tool === "watch-folder" && folderWatch.active) stopFolderWatch(false);
  elements.catalogView.hidden = !show;
  elements.workbench.hidden = show;
  document.querySelector(".page-intro").hidden = show;
  if (show) renderCatalog();
  else if (state.tool === "watch-folder") renderOptions();
}

async function initialize() {
  renderOptions();
  const statusLabel = document.querySelector("#engine-status");
  try {
    const [statusResponse, catalogResponse] = await Promise.all([fetch("/api/status"), fetch("/api/tools")]);
    if (!statusResponse.ok || !catalogResponse.ok) throw new Error("The processing server is not responding.");
    const status = await statusResponse.json();
    state.workflowTools = status.workflow_tools;
    state.workflowOptions = status.workflow_options;
    state.catalog = await catalogResponse.json();
    const readyCount = state.catalog.filter((tool) => tool.status === "ready").length;
    const plannedCount = state.catalog.filter((tool) => tool.status === "planned").length;
    statusLabel.textContent = "PDF processing ready";
    document.querySelector("#catalog-count").textContent = `${state.catalog.length}`;
    elements.catalogSummary.textContent = `${readyCount} ready · ${plannedCount} planned.`;
    document.querySelector("#footer-count").textContent = `${readyCount} ready · ${plannedCount} planned`;
    renderCatalog();
    if (state.tool === "watch-folder" || state.tool === "history") renderOptions();
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
  stopFolderWatch(true);
});

setTool("merge");
initialize();
