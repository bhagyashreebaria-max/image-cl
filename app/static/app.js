const MAX_UPLOAD_BYTES = 80 * 1024 * 1024;
const MAX_SEQUENCE_PAGES = 60;

const state = {
  docs: [],
  pages: [],
  initialOrder: [],
  recipe: [],
  mode: "compose",
  sequenceReady: true,
  busy: false,
  draggedPage: "",
  outputURL: "",
};

const el = {
  fileInput: document.querySelector("#file-input"),
  intake: document.querySelector("#intake-zone"),
  documentList: document.querySelector("#document-list"),
  documentCount: document.querySelector("#document-count"),
  pageSequence: document.querySelector("#page-sequence"),
  emptyStage: document.querySelector("#empty-stage"),
  pageTotal: document.querySelector("#page-total"),
  pageSelection: document.querySelector("#page-selection"),
  selectionCount: document.querySelector("#selection-count"),
  rotateSelected: document.querySelector("#rotate-selected"),
  insertBlank: document.querySelector("#insert-blank"),
  removeSelected: document.querySelector("#remove-selected"),
  compileButton: document.querySelector("#compile-button"),
  compileLabel: document.querySelector("#compile-label"),
  compileResult: document.querySelector("#compile-result"),
  resultName: document.querySelector("#result-name"),
  download: document.querySelector("#download-link"),
  notice: document.querySelector("#notice"),
  tabs: [...document.querySelectorAll(".mode-tab")],
  panels: [...document.querySelectorAll("[data-panel]")],
  recipeList: document.querySelector("#recipe-list"),
  recipeCount: document.querySelector("#recipe-count"),
  recipeSummary: document.querySelector("#recipe-summary"),
  recipeEmpty: document.querySelector("#recipe-empty"),
  clearRecipe: document.querySelector("#clear-recipe"),
  serviceLight: document.querySelector("#service-light"),
  serviceLabel: document.querySelector("#service-label"),
  inspectButton: document.querySelector("#inspect-button"),
  inspection: document.querySelector("#inspection-strip"),
  inspectionTitle: document.querySelector("#inspection-title"),
  inspectionVersion: document.querySelector("#inspection-version"),
  inspectionDetails: document.querySelector("#inspection-details"),
  surfaceTitle: document.querySelector("#surface-title"),
  surfaceCopy: document.querySelector("#surface-copy"),
  activeMode: document.querySelector("#active-mode-label"),
};

function setNotice(message, type = "info") {
  el.notice.textContent = message;
  el.notice.className = `surface-message${type === "error" ? " is-error" : ""}${type === "success" ? " is-success" : ""}`;
  el.notice.hidden = !message;
}

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

async function responseError(response) {
  try {
    const body = await response.json();
    return body.detail || `The engine returned HTTP ${response.status}.`;
  } catch {
    return `The engine returned HTTP ${response.status}.`;
  }
}

function newId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function inspectFile(file) {
  const body = new FormData();
  body.append("file", file, file.name);
  const response = await fetch("/api/v1/engine/inspect", { method: "POST", body });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}

async function addFiles(fileList) {
  if (state.busy) return;
  const incoming = [...fileList].filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  if (!incoming.length) {
    setNotice("Choose PDF files. Other document formats are not accepted by this Studio.", "error");
    return;
  }
  const currentBytes = state.docs.reduce((total, doc) => total + doc.file.size, 0);
  if (incoming.reduce((total, file) => total + file.size, currentBytes) > MAX_UPLOAD_BYTES) {
    setNotice("The selected documents exceed the 80 MiB total request limit.", "error");
    return;
  }
  setNotice("Reading document structure and preparing page proofs…");
  el.fileInput.disabled = true;
  el.intake.classList.add("is-disabled");
  const added = [];
  for (const file of incoming) {
    try {
      const report = await inspectFile(file);
      added.push({ id: newId(), file, report });
    } catch (error) {
      setNotice(`${file.name}: ${error.message}`, "error");
    }
  }
  state.docs.push(...added);
  rebuildSequence();
  renderDocuments();
  renderPages();
  el.fileInput.disabled = false;
  el.intake.classList.remove("is-disabled");
  if (added.length && !el.notice.classList.contains("is-error")) {
    setNotice(`${added.length} document${added.length === 1 ? "" : "s"} ready in this workspace.`, "success");
  }
  el.fileInput.value = "";
}

function rebuildSequence() {
  const totalPages = state.docs.reduce((total, doc) => total + doc.report.page_count, 0);
  state.sequenceReady = totalPages <= MAX_SEQUENCE_PAGES;
  const pages = [];
  let offset = 0;
  for (const doc of state.docs) {
    for (const page of doc.report.pages || []) {
      if (pages.length >= MAX_SEQUENCE_PAGES) break;
      pages.push({
        id: `${doc.id}:${page.number}`,
        docId: doc.id,
        pageNumber: page.number,
        globalNumber: offset + page.number,
        thumbnail: page.thumbnail,
        textCharacters: page.text_characters,
        rotation: page.rotation,
        selected: false,
        removed: false,
        turns: 0,
        blankAfter: false,
      });
    }
    offset += doc.report.page_count;
  }
  state.pages = pages;
  state.initialOrder = pages.map((page) => page.id);
  if (state.docs.length && !state.sequenceReady) {
    setNotice(`Page editing is limited to ${MAX_SEQUENCE_PAGES} pages per run. Existing pages remain intact; Transform and Protect still apply to the full input.`, "info");
  }
  updateCounts();
}

function selectedPages() {
  return state.pages.filter((page) => page.selected && !page.removed);
}

function updateCounts() {
  const total = state.docs.reduce((sum, doc) => sum + doc.report.page_count, 0);
  const selected = selectedPages().length;
  el.documentCount.textContent = String(state.docs.length);
  el.pageTotal.textContent = `${total} ${total === 1 ? "page" : "pages"}`;
  el.pageSelection.textContent = selected ? `${selected} selected` : "No pages selected";
  el.selectionCount.textContent = `${selected} ${selected === 1 ? "page" : "pages"}`;
  for (const button of [el.rotateSelected, el.insertBlank, el.removeSelected]) {
    button.disabled = !selected || !state.sequenceReady || state.busy;
  }
  el.inspectButton.disabled = !state.docs.length || state.busy;
  el.compileButton.disabled = !state.docs.length || state.busy;
  el.fileInput.disabled = state.busy;
  el.intake.classList.toggle("is-disabled", state.busy);
  el.compileLabel.textContent = state.busy ? "Compiling document…" : "Compile document";
}

function renderDocuments() {
  el.documentList.replaceChildren();
  for (const [index, doc] of state.docs.entries()) {
    const item = document.createElement("li");
    item.className = "document-row";

    const badge = document.createElement("span");
    badge.className = "document-badge";
    badge.textContent = "PDF";
    badge.setAttribute("aria-hidden", "true");

    const details = document.createElement("div");
    details.className = "document-details";
    const name = document.createElement("strong");
    name.textContent = doc.file.name;
    const meta = document.createElement("span");
    meta.textContent = `${doc.report.page_count} pages · ${formatBytes(doc.file.size)}`;
    details.append(name, meta);

    const remove = document.createElement("button");
    remove.className = "remove-document";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${doc.file.name}`);
    remove.disabled = state.busy;
    remove.addEventListener("click", () => {
      state.docs.splice(index, 1);
      rebuildSequence();
      renderDocuments();
      renderPages();
      setNotice("Document removed from this workspace.", "success");
    });

    item.append(badge, details, remove);
    el.documentList.append(item);
  }
}

function movePage(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= state.pages.length) return;
  [state.pages[index], state.pages[next]] = [state.pages[next], state.pages[index]];
  renderPages();
}

function renderPages() {
  el.pageSequence.replaceChildren();
  el.emptyStage.hidden = state.pages.length > 0;
  for (const [index, page] of state.pages.entries()) {
    const doc = state.docs.find((candidate) => candidate.id === page.docId);
    const item = document.createElement("li");
    item.className = `page-proof${page.selected ? " is-selected" : ""}${page.removed ? " is-removed" : ""}`;
    item.draggable = state.sequenceReady && !state.busy;
    item.dataset.pageId = page.id;
    item.addEventListener("dragstart", (event) => {
      state.draggedPage = page.id;
      event.dataTransfer.effectAllowed = "move";
    });
    item.addEventListener("dragover", (event) => {
      if (state.sequenceReady) event.preventDefault();
    });
    item.addEventListener("drop", (event) => {
      event.preventDefault();
      const from = state.pages.findIndex((entry) => entry.id === state.draggedPage);
      const to = state.pages.findIndex((entry) => entry.id === page.id);
      if (from < 0 || to < 0 || from === to) return;
      const [moved] = state.pages.splice(from, 1);
      state.pages.splice(to, 0, moved);
      state.draggedPage = "";
      renderPages();
    });

    const proofHead = document.createElement("div");
    proofHead.className = "proof-head";
    const pageLabel = document.createElement("span");
    pageLabel.textContent = `PAGE ${String(page.pageNumber).padStart(2, "0")}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = page.selected;
    checkbox.disabled = !state.sequenceReady || state.busy || page.removed;
    checkbox.setAttribute("aria-label", `Select page ${page.pageNumber} of ${doc?.file.name || "document"}`);
    checkbox.addEventListener("change", () => {
      page.selected = checkbox.checked;
      updateCounts();
      item.classList.toggle("is-selected", page.selected);
    });
    proofHead.append(pageLabel, checkbox);

    const art = document.createElement("div");
    art.className = "proof-art";
    if (page.thumbnail) {
      const image = document.createElement("img");
      image.src = page.thumbnail;
      image.alt = `Preview of page ${page.pageNumber}`;
      image.loading = "lazy";
      art.append(image);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "proof-placeholder";
      placeholder.textContent = "PREVIEW";
      art.append(placeholder);
    }
    if (page.turns) {
      art.style.transform = `rotate(${page.turns * 90}deg)`;
      art.classList.add("is-turned");
    }
    if (page.removed) {
      const removedMark = document.createElement("span");
      removedMark.className = "removed-mark";
      removedMark.textContent = "REMOVED";
      art.append(removedMark);
    }
    if (page.blankAfter && !page.removed) {
      const blankMark = document.createElement("span");
      blankMark.className = "blank-insert-mark";
      blankMark.textContent = "BLANK PAGE AFTER";
      item.append(blankMark);
    }

    const footer = document.createElement("div");
    footer.className = "proof-footer";
    const source = document.createElement("span");
    source.textContent = doc?.file.name || "Source";
    source.title = source.textContent;
    const controls = document.createElement("div");
    controls.className = "proof-controls";

    const earlier = document.createElement("button");
    earlier.type = "button";
    earlier.textContent = "←";
    earlier.setAttribute("aria-label", `Move page ${page.pageNumber} earlier`);
    earlier.disabled = !state.sequenceReady || state.busy || index === 0;
    earlier.addEventListener("click", () => movePage(index, -1));
    const later = document.createElement("button");
    later.type = "button";
    later.textContent = "→";
    later.setAttribute("aria-label", `Move page ${page.pageNumber} later`);
    later.disabled = !state.sequenceReady || state.busy || index === state.pages.length - 1;
    later.addEventListener("click", () => movePage(index, 1));
    const rotate = document.createElement("button");
    rotate.type = "button";
    rotate.textContent = "↻";
    rotate.setAttribute("aria-label", `Rotate page ${page.pageNumber} 90 degrees`);
    rotate.disabled = !state.sequenceReady || state.busy || page.removed;
    rotate.addEventListener("click", () => {
      page.turns = (page.turns + 1) % 4;
      renderPages();
    });
    controls.append(earlier, later, rotate);
    footer.append(source, controls);
    item.append(proofHead, art, footer);
    el.pageSequence.append(item);
  }
  renderRecipe();
  updateCounts();
}

function setMode(mode) {
  state.mode = mode;
  for (const tab of el.tabs) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  for (const panel of el.panels) panel.hidden = panel.dataset.panel !== mode;
  const labels = {
    compose: ["COMPOSE", "Arrange the pages.", "Move proofs into order. Turn, remove, or insert pages before you compile."],
    transform: ["TRANSFORM", "Change the document.", "Queue an optimization or watermark to run after page composition."],
    protect: ["PROTECT", "Prepare a safer copy.", "Apply supported document security and sanitization steps in sequence."],
    inspect: ["INSPECT", "Read the structure.", "Inspect page geometry, fonts, image inventory, metadata, and form fields."],
  };
  [el.activeMode.textContent, el.surfaceTitle.textContent, el.surfaceCopy.textContent] = labels[mode];
}

function composeSteps() {
  if (!state.sequenceReady || !state.pages.length) return [];
  const steps = [];
  const orderChanged = state.pages.some((page, index) => page.id !== state.initialOrder[index]);
  const originalIndex = new Map();
  let offset = 0;
  for (const doc of state.docs) {
    for (let pageNumber = 1; pageNumber <= doc.report.page_count; pageNumber += 1) {
      originalIndex.set(`${doc.id}:${pageNumber}`, offset + pageNumber);
    }
    offset += doc.report.page_count;
  }
  if (orderChanged) {
    steps.push({
      primitive: "compose",
      operation: "reorder",
      options: { order: state.pages.map((page) => originalIndex.get(page.id)).join(",") },
    });
  }
  const removedPositions = state.pages
    .map((page, index) => page.removed ? index + 1 : 0)
    .filter(Boolean);
  if (removedPositions.length) {
    steps.push({
      primitive: "compose",
      operation: "delete",
      options: { ranges: removedPositions.join(",") },
    });
  }
  const remaining = state.pages.filter((page) => !page.removed);
  for (let turn = 1; turn <= 3; turn += 1) {
    const pages = remaining
      .map((page, index) => page.turns >= turn ? index + 1 : 0)
      .filter(Boolean);
    if (pages.length) {
      steps.push({ primitive: "compose", operation: "rotate-pages", options: { pages, rotation: 90 } });
    }
  }
  const blanks = remaining
    .map((page, index) => page.blankAfter ? index + 1 : 0)
    .filter(Boolean);
  if (blanks.length) {
    steps.push({ primitive: "compose", operation: "blank", options: { ranges: blanks.join(",") } });
  }
  return steps;
}

function renderRecipe() {
  el.recipeList.replaceChildren();
  const compositionCount = composeSteps().length;
  const operationCount = compositionCount + state.recipe.length;
  el.recipeCount.textContent = String(operationCount);
  el.recipeEmpty.hidden = operationCount > 0;
  el.clearRecipe.hidden = state.recipe.length === 0;
  const summary = [];
  if (compositionCount) summary.push(`${compositionCount} page operation${compositionCount === 1 ? "" : "s"} staged`);
  if (state.recipe.length) summary.push(`${state.recipe.length} action${state.recipe.length === 1 ? "" : "s"} queued after composition`);
  el.recipeSummary.textContent = summary.length ? summary.join(" · ") : "No extra operations queued";
  state.recipe.forEach((step, index) => {
    const item = document.createElement("li");
    item.className = "recipe-item";
    const text = document.createElement("span");
    text.textContent = `${step.primitive} · ${step.operation}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${step.operation} from run sequence`);
    remove.addEventListener("click", () => {
      state.recipe.splice(index, 1);
      renderRecipe();
    });
    item.append(text, remove);
    el.recipeList.append(item);
  });
}

function addRecipe(primitive, operation, options = {}) {
  if (buildRecipe().length >= 10) {
    setNotice("A run can contain at most 10 operations, including page composition.", "error");
    return;
  }
  state.recipe.push({ primitive, operation, options });
  renderRecipe();
  setNotice(`${operation} added to the run sequence.`, "success");
}

function buildRecipe() {
  return [...composeSteps(), ...state.recipe];
}

async function compileDocument() {
  if (state.busy || !state.docs.length) return;
  if (!state.sequenceReady && composeSteps().length) {
    setNotice("Page edits require no more than 60 total pages so the complete sequence can be represented safely.", "error");
    return;
  }
  const recipe = buildRecipe();
  if (recipe.length > 10) {
    setNotice("A run can contain at most 10 operations. Remove an action and try again.", "error");
    return;
  }
  const body = new FormData();
  for (const doc of state.docs) body.append("files", doc.file, doc.file.name);
  const usePipeline = recipe.length > 0;
  if (usePipeline) body.append("recipe", JSON.stringify(recipe));
  else body.append("steps", "[]");

  state.busy = true;
  el.compileButton.disabled = true;
  el.compileLabel.textContent = "Compiling document…";
  setNotice("Applying the sequence and preparing your output.");
  try {
    const endpoint = usePipeline ? "/api/v1/engine/pipeline" : "/api/v1/engine/compose";
    const response = await fetch(endpoint, { method: "POST", body });
    if (!response.ok) throw new Error(await responseError(response));
    const output = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const filename = disposition.match(/filename="([^"]+)"/i)?.[1] || "prism-document.pdf";
    if (state.outputURL) URL.revokeObjectURL(state.outputURL);
    state.outputURL = URL.createObjectURL(output);
    el.download.href = state.outputURL;
    el.download.download = filename;
    el.resultName.textContent = `${filename} · ${formatBytes(output.size)}`;
    el.compileResult.hidden = false;
    state.recipe = [];
    for (const selector of ["#user-password", "#owner-password", "#current-password"]) {
      const input = document.querySelector(selector);
      if (input) input.value = "";
    }
    renderRecipe();
    setNotice("Finished copy is ready to download. Source documents were not changed.", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.busy = false;
    el.fileInput.disabled = false;
    el.intake.classList.remove("is-disabled");
    renderDocuments();
    renderPages();
  }
}

function showInspection(report) {
  el.inspectionTitle.textContent = report.filename;
  el.inspectionVersion.textContent = `${report.pdf_version} · ${report.encrypted ? "encrypted" : "not encrypted"}`;
  el.inspectionDetails.replaceChildren();
  const entries = [
    ["Pages", String(report.page_count)],
    ["Fonts", report.fonts.length ? report.fonts.join(", ") : "None detected"],
    ["Images", String(report.image_count)],
    ["Form fields", report.fields.length ? report.fields.map((field) => `${field.name} (${field.type})`).join(", ") : "None detected"],
    ["Title", report.metadata.title || "Not set"],
  ];
  for (const [label, value] of entries) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    el.inspectionDetails.append(term, detail);
  }
  el.inspection.hidden = false;
}

async function inspectFirstDocument() {
  const doc = state.docs[0];
  if (!doc || state.busy) return;
  state.busy = true;
  el.inspectButton.disabled = true;
  setNotice("Inspecting document structure.");
  try {
    const report = await inspectFile(doc.file);
    doc.report = report;
    showInspection(report);
    renderPages();
    setNotice("Inspection complete.", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.busy = false;
    renderDocuments();
    renderPages();
  }
}

function updateProtectionFields() {
  const operation = document.querySelector("#protect-operation").value;
  const encryption = operation === "encrypt";
  const decryption = operation === "decrypt";
  const redaction = operation === "redact";
  document.querySelector("#password-field").hidden = !encryption;
  document.querySelector("#owner-password-field").hidden = !encryption;
  document.querySelector("#current-password-field").hidden = !decryption;
  document.querySelector("#redact-field").hidden = !redaction;
  document.querySelector("#protect-warning").hidden = !redaction;
}

function queueProtection() {
  const operation = document.querySelector("#protect-operation").value;
  const options = {};
  if (operation === "encrypt") {
    options.user_password = document.querySelector("#user-password").value;
    options.owner_password = document.querySelector("#owner-password").value;
    options.profile = "copy";
    if (options.user_password.length < 8 || options.owner_password.length < 8) {
      setNotice("Encryption requires user and owner passwords of at least 8 characters.", "error");
      return;
    }
  }
  if (operation === "decrypt") {
    options.password = document.querySelector("#current-password").value;
    if (!options.password) {
      setNotice("Enter the current PDF password before queuing decryption.", "error");
      return;
    }
  }
  if (operation === "redact") {
    options.phrase = document.querySelector("#redact-text").value.trim();
    if (!options.phrase) {
      setNotice("Enter the exact text to redact.", "error");
      return;
    }
  }
  addRecipe("protect", operation, options);
}

function queueTransform() {
  const operation = document.querySelector("#transform-operation").value;
  const options = {};
  if (operation === "watermark") {
    options.watermark = document.querySelector("#watermark-text").value.trim();
    if (!options.watermark) {
      setNotice("Enter watermark text before adding the transform.", "error");
      return;
    }
  }
  addRecipe("transform", operation, options);
}

document.querySelector("#transform-operation").addEventListener("change", (event) => {
  document.querySelector("#watermark-field").hidden = event.target.value !== "watermark";
});
document.querySelector("#protect-operation").addEventListener("change", updateProtectionFields);
el.fileInput.addEventListener("change", () => addFiles(el.fileInput.files));
el.intake.addEventListener("dragover", (event) => {
  event.preventDefault();
  el.intake.classList.add("is-dragging");
});
el.intake.addEventListener("dragleave", () => el.intake.classList.remove("is-dragging"));
el.intake.addEventListener("drop", (event) => {
  event.preventDefault();
  el.intake.classList.remove("is-dragging");
  addFiles(event.dataTransfer.files);
});
document.querySelector("#rotate-selected").addEventListener("click", () => {
  for (const page of selectedPages()) page.turns = (page.turns + 1) % 4;
  renderPages();
});
document.querySelector("#insert-blank").addEventListener("click", () => {
  for (const page of selectedPages()) page.blankAfter = !page.blankAfter;
  renderPages();
  setNotice("Blank-page insertion toggled after the selected pages.", "success");
});
document.querySelector("#remove-selected").addEventListener("click", () => {
  for (const page of selectedPages()) {
    page.removed = true;
    page.selected = false;
  }
  renderPages();
});
document.querySelector("#queue-transform").addEventListener("click", queueTransform);
document.querySelector("#queue-protect").addEventListener("click", queueProtection);
document.querySelector("#inspect-button").addEventListener("click", inspectFirstDocument);
document.querySelector("#compile-button").addEventListener("click", compileDocument);
document.querySelector("#clear-recipe").addEventListener("click", () => {
  state.recipe = [];
  renderRecipe();
});
for (const tab of el.tabs) tab.addEventListener("click", () => setMode(tab.dataset.mode));
document.querySelector(".mode-switch").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const current = el.tabs.findIndex((tab) => tab.dataset.mode === state.mode);
  const delta = event.key === "ArrowRight" ? 1 : -1;
  const next = el.tabs[(current + delta + el.tabs.length) % el.tabs.length];
  setMode(next.dataset.mode);
  next.focus();
});

fetch("/api/v1/engine")
  .then((response) => {
    if (!response.ok) throw new Error("Engine unavailable");
    return response.json();
  })
  .then(() => {
    el.serviceLight.classList.add("is-ready");
    el.serviceLabel.textContent = "Engine ready";
  })
  .catch(() => {
    el.serviceLight.classList.add("is-error");
    el.serviceLabel.textContent = "Engine unavailable";
  });

renderRecipe();
renderDocuments();
renderPages();
