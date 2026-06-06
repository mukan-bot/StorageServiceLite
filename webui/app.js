/**
 * @typedef {{ offset: number, size: number, created_at?: number }} ChunkMeta
 * @typedef {{ ulid: string, name: string, chunks: ChunkMeta[], deleted: boolean }} StorageItem
 * @typedef {{ file: File, buffer: ArrayBuffer, version: number, indexOffset: number, index: Record<string, { name?: string, chunks?: ChunkMeta[] }>, items: StorageItem[] }} ParsedStorage
 */

const HEADER_SIZE = 18;
const MAGIC = [0x53, 0x53, 0x4f, 0x42, 0x4a, 0x00, 0x01, 0x00];
const TEXT_PREVIEW_LIMIT = 120_000;
const HEX_PREVIEW_LIMIT = 8_192;
const IMAGE_SIGNATURES = [
  { mime: "image/png", bytes: [0x89, 0x50, 0x4e, 0x47] },
  { mime: "image/jpeg", bytes: [0xff, 0xd8, 0xff] },
  { mime: "image/gif", bytes: [0x47, 0x49, 0x46, 0x38] },
  { mime: "image/webp", bytes: [0x52, 0x49, 0x46, 0x46], extra: (data) => ascii(data, 8, 12) === "WEBP" },
  { mime: "image/svg+xml", bytes: [0x3c, 0x73, 0x76, 0x67] },
];

/** @type {ParsedStorage | null} */
let parsedStorage = null;
/** @type {StorageItem | null} */
let selectedItem = null;
let selectedHistory = 1;
let previewMode = "auto";
let activeObjectUrl = null;
let s3SdkModule = null;

const elements = {
  fileInput: document.querySelector("#file-input"),
  dropZone: document.querySelector("#drop-zone"),
  fileSummary: document.querySelector("#file-summary"),
  message: document.querySelector("#message"),
  itemCount: document.querySelector("#item-count"),
  itemList: document.querySelector("#item-list"),
  searchInput: document.querySelector("#search-input"),
  showDeleted: document.querySelector("#show-deleted"),
  detailEmpty: document.querySelector("#detail-empty"),
  detail: document.querySelector("#detail"),
  detailName: document.querySelector("#detail-name"),
  detailUlid: document.querySelector("#detail-ulid"),
  detailHistoryCount: document.querySelector("#detail-history-count"),
  detailSize: document.querySelector("#detail-size"),
  historySelect: document.querySelector("#history-select"),
  chunkCreated: document.querySelector("#chunk-created"),
  chunkOffset: document.querySelector("#chunk-offset"),
  chunkSize: document.querySelector("#chunk-size"),
  preview: document.querySelector("#preview"),
  downloadButton: document.querySelector("#download-button"),
  s3Endpoint: document.querySelector("#s3-endpoint"),
  s3Region: document.querySelector("#s3-region"),
  s3Bucket: document.querySelector("#s3-bucket"),
  s3ObjectKey: document.querySelector("#s3-object-key"),
  s3AccessKey: document.querySelector("#s3-access-key"),
  s3SecretKey: document.querySelector("#s3-secret-key"),
  s3SessionToken: document.querySelector("#s3-session-token"),
  s3UploadTarget: document.querySelector("#s3-upload-target"),
  s3ForcePathStyle: document.querySelector("#s3-force-path-style"),
  s3UploadButton: document.querySelector("#s3-upload-button"),
  s3UploadStatus: document.querySelector("#s3-upload-status"),
  itemTemplate: document.querySelector("#item-template"),
  previewButtons: document.querySelectorAll("[data-preview]"),
};

if (!elements.fileInput || !elements.dropZone || !elements.itemTemplate) {
  throw new Error("Web UIの初期化に失敗しました。HTML構造を確認してください。");
}

elements.fileInput.addEventListener("change", (event) => {
  const [file] = event.target.files ?? [];
  if (file) {
    void loadFile(file);
  }
});

for (const eventName of ["dragenter", "dragover"]) {
  elements.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropZone.classList.add("dragging");
  });
}

for (const eventName of ["dragleave", "drop"]) {
  elements.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropZone.classList.remove("dragging");
  });
}

elements.dropZone.addEventListener("drop", (event) => {
  const [file] = event.dataTransfer?.files ?? [];
  if (file) {
    void loadFile(file);
  }
});

elements.searchInput.addEventListener("input", renderItemList);
elements.showDeleted.addEventListener("change", renderItemList);
elements.historySelect.addEventListener("change", () => {
  selectedHistory = Number(elements.historySelect.value);
  renderSelectedChunk();
});
elements.downloadButton.addEventListener("click", downloadSelectedChunk);
elements.s3UploadButton?.addEventListener("click", () => {
  void uploadToS3Compatible();
});

[
  elements.s3Endpoint,
  elements.s3Region,
  elements.s3Bucket,
  elements.s3ObjectKey,
  elements.s3AccessKey,
  elements.s3SecretKey,
  elements.s3SessionToken,
  elements.s3UploadTarget,
  elements.s3ForcePathStyle,
].forEach((field) => {
  field?.addEventListener("input", updateS3UploadButtonState);
  field?.addEventListener("change", updateS3UploadButtonState);
});

elements.previewButtons.forEach((button) => {
  button.addEventListener("click", () => {
    previewMode = button.dataset.preview ?? "auto";
    elements.previewButtons.forEach((target) => target.classList.toggle("active", target === button));
    renderSelectedChunk();
  });
});

/**
 * @param {File} file
 */
async function loadFile(file) {
  clearMessage();
  resetSelection();

  try {
    const buffer = await file.arrayBuffer();
    parsedStorage = parseStorageFile(file, buffer);
    elements.fileSummary.classList.remove("hidden");
    elements.fileSummary.innerHTML = `
      <strong>${escapeHtml(file.name)}</strong><br />
      ${formatBytes(file.size)} / format v${parsedStorage.version} / index offset ${parsedStorage.indexOffset.toLocaleString()}
    `;
    elements.searchInput.disabled = false;
    elements.showDeleted.disabled = false;
    renderItemList();
    showMessage("読み込みが完了しました。アイテムを選択すると内容を確認できます。", false);
    suggestDefaultS3ObjectKey();
    updateS3UploadButtonState();
  } catch (error) {
    parsedStorage = null;
    renderItemList();
    elements.fileSummary.classList.add("hidden");
    showMessage(error instanceof Error ? error.message : String(error), true);
    updateS3UploadButtonState();
  }
}

/**
 * @param {File} file
 * @param {ArrayBuffer} buffer
 * @returns {ParsedStorage}
 */
function parseStorageFile(file, buffer) {
  if (buffer.byteLength < HEADER_SIZE) {
    throw new Error("ファイルが短すぎるため、StorageServiceLiteのヘッダーを読み取れません。");
  }

  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < MAGIC.length; index += 1) {
    if (bytes[index] !== MAGIC[index]) {
      throw new Error("StorageServiceLite形式ではありません。マジックバイトが一致しません。");
    }
  }

  const view = new DataView(buffer);
  const version = view.getUint16(8, true);
  const indexOffset = readUint64AsNumber(view, 10);
  if (!Number.isSafeInteger(indexOffset) || indexOffset < HEADER_SIZE || indexOffset > buffer.byteLength) {
    throw new Error(`インデックスオフセットが不正です: ${indexOffset}`);
  }

  const indexText = new TextDecoder("utf-8", { fatal: true }).decode(bytes.slice(indexOffset));
  const index = indexText.trim() ? JSON.parse(indexText) : {};
  const items = Object.entries(index).map(([ulid, entry]) => {
    const chunks = Array.isArray(entry?.chunks) ? entry.chunks.map(normalizeChunk) : [];
    return {
      ulid,
      name: typeof entry?.name === "string" ? entry.name : ulid,
      chunks,
      deleted: chunks.length === 0,
    };
  });

  for (const item of items) {
    for (const chunk of item.chunks) {
      validateChunk(buffer, item.ulid, chunk);
    }
  }

  items.sort((a, b) => a.ulid.localeCompare(b.ulid));
  return { file, buffer, version, indexOffset, index, items };
}

/**
 * @param {unknown} chunk
 * @returns {ChunkMeta}
 */
function normalizeChunk(chunk) {
  if (!chunk || typeof chunk !== "object") {
    throw new Error("インデックス内のチャンクメタデータが不正です。");
  }
  return {
    offset: Number(chunk.offset),
    size: Number(chunk.size),
    created_at: Number(chunk.created_at),
  };
}

/**
 * @param {ArrayBuffer} buffer
 * @param {string} ulid
 * @param {ChunkMeta} chunk
 */
function validateChunk(buffer, ulid, chunk) {
  const end = chunk.offset + 8 + chunk.size;
  if (!Number.isSafeInteger(chunk.offset) || !Number.isSafeInteger(chunk.size) || chunk.offset < HEADER_SIZE || chunk.size < 0 || end > buffer.byteLength) {
    throw new Error(`ULID ${ulid} のチャンク位置またはサイズが不正です。`);
  }

  const actualSize = readUint64AsNumber(new DataView(buffer), chunk.offset);
  if (actualSize !== chunk.size) {
    throw new Error(`ULID ${ulid} のチャンクサイズがインデックスと一致しません。`);
  }
}

function renderItemList() {
  elements.itemList.replaceChildren();
  const items = getFilteredItems();
  const total = parsedStorage?.items.filter((item) => !item.deleted).length ?? 0;
  const deleted = parsedStorage?.items.filter((item) => item.deleted).length ?? 0;
  elements.itemCount.textContent = `${total} items${deleted ? ` / ${deleted} deleted` : ""}`;

  if (!parsedStorage) {
    elements.itemList.className = "item-list empty-state";
    elements.itemList.innerHTML = "<li>ストレージファイルを読み込むと一覧が表示されます。</li>";
    elements.searchInput.disabled = true;
    elements.showDeleted.disabled = true;
    return;
  }

  if (items.length === 0) {
    elements.itemList.className = "item-list empty-state";
    elements.itemList.innerHTML = "<li>条件に一致するアイテムはありません。</li>";
    return;
  }

  elements.itemList.className = "item-list";
  for (const item of items) {
    const fragment = elements.itemTemplate.content.cloneNode(true);
    const button = fragment.querySelector(".item-card");
    fragment.querySelector(".item-name").textContent = item.name;
    fragment.querySelector(".item-ulid").textContent = item.ulid;
    fragment.querySelector(".item-meta").textContent = item.deleted
      ? "削除済み / 0 histories"
      : `${item.chunks.length} histories / latest ${formatBytes(item.chunks.at(-1)?.size ?? 0)}`;
    button.classList.toggle("deleted", item.deleted);
    button.classList.toggle("active", selectedItem?.ulid === item.ulid);
    button.addEventListener("click", () => selectItem(item));
    elements.itemList.appendChild(fragment);
  }
}

/** @returns {StorageItem[]} */
function getFilteredItems() {
  if (!parsedStorage) {
    return [];
  }
  const query = elements.searchInput.value.trim().toLowerCase();
  const includeDeleted = elements.showDeleted.checked;
  return parsedStorage.items.filter((item) => {
    if (item.deleted && !includeDeleted) {
      return false;
    }
    if (!query) {
      return true;
    }
    return item.ulid.toLowerCase().includes(query) || item.name.toLowerCase().includes(query);
  });
}

/** @param {StorageItem} item */
function selectItem(item) {
  selectedItem = item;
  selectedHistory = 1;
  renderItemList();
  renderDetails();
  suggestDefaultS3ObjectKey();
  updateS3UploadButtonState();
}

function renderDetails() {
  if (!selectedItem) {
    resetSelection();
    return;
  }

  elements.detailEmpty.classList.add("hidden");
  elements.detail.classList.remove("hidden");
  elements.detailName.textContent = selectedItem.name;
  elements.detailUlid.textContent = selectedItem.ulid;
  elements.detailHistoryCount.textContent = String(selectedItem.chunks.length);
  elements.detailSize.textContent = selectedItem.deleted ? "-" : formatBytes(selectedItem.chunks.at(-1)?.size ?? 0);

  elements.historySelect.replaceChildren();
  if (selectedItem.deleted) {
    const option = new Option("削除済み: 有効なヒストリーはありません", "0");
    elements.historySelect.appendChild(option);
    elements.historySelect.disabled = true;
    elements.downloadButton.disabled = true;
    elements.preview.textContent = "このアイテムは削除済みです。";
    updateS3UploadButtonState();
    return;
  }

  selectedItem.chunks.slice().reverse().forEach((chunk, index) => {
    const historyNumber = index + 1;
    const option = new Option(
      `history=${historyNumber} / ${formatBytes(chunk.size)} / ${formatDate(chunk.created_at)}`,
      String(historyNumber),
    );
    elements.historySelect.appendChild(option);
  });
  elements.historySelect.disabled = false;
  renderSelectedChunk();
  updateS3UploadButtonState();
}

function renderSelectedChunk() {
  if (!parsedStorage || !selectedItem || selectedItem.deleted) {
    return;
  }
  const chunk = getSelectedChunk();
  if (!chunk) {
    return;
  }

  elements.chunkCreated.textContent = formatDate(chunk.created_at);
  elements.chunkOffset.textContent = String(chunk.offset);
  elements.chunkSize.textContent = formatBytes(chunk.size);
  elements.downloadButton.disabled = false;
  renderPreview(readChunkBytes(parsedStorage.buffer, chunk));
}

/** @returns {ChunkMeta | null} */
function getSelectedChunk() {
  if (!selectedItem || selectedHistory < 1 || selectedHistory > selectedItem.chunks.length) {
    return null;
  }
  return selectedItem.chunks[selectedItem.chunks.length - selectedHistory];
}

/** @param {Uint8Array} bytes */
function renderPreview(bytes) {
  releaseObjectUrl();
  elements.preview.replaceChildren();

  const mode = previewMode === "auto" ? inferPreviewMode(bytes) : previewMode;
  if (mode === "image") {
    const mime = detectImageMime(bytes) ?? "application/octet-stream";
    activeObjectUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const image = document.createElement("img");
    image.src = activeObjectUrl;
    image.alt = "選択中チャンクの画像プレビュー";
    elements.preview.appendChild(image);
    return;
  }

  if (mode === "text") {
    const limited = bytes.slice(0, TEXT_PREVIEW_LIMIT);
    elements.preview.textContent = new TextDecoder("utf-8", { fatal: false }).decode(limited);
    if (bytes.length > TEXT_PREVIEW_LIMIT) {
      elements.preview.textContent += `\n\n... ${formatBytes(bytes.length - TEXT_PREVIEW_LIMIT)} は省略されました。`;
    }
    return;
  }

  elements.preview.textContent = toHexDump(bytes, HEX_PREVIEW_LIMIT);
}

/** @param {Uint8Array} bytes */
function inferPreviewMode(bytes) {
  if (detectImageMime(bytes)) {
    return "image";
  }
  return isLikelyText(bytes) ? "text" : "hex";
}

/** @param {Uint8Array} bytes */
function isLikelyText(bytes) {
  const sample = bytes.slice(0, Math.min(bytes.length, 4096));
  if (sample.length === 0) {
    return true;
  }
  let control = 0;
  for (const byte of sample) {
    const isWhitespace = byte === 9 || byte === 10 || byte === 13;
    if (byte < 32 && !isWhitespace) {
      control += 1;
    }
  }
  return control / sample.length < 0.05;
}

/** @param {Uint8Array} bytes */
function detectImageMime(bytes) {
  const signature = IMAGE_SIGNATURES.find((candidate) => {
    const matched = candidate.bytes.every((byte, index) => bytes[index] === byte);
    return matched && (!candidate.extra || candidate.extra(bytes));
  });
  return signature?.mime ?? null;
}

function downloadSelectedChunk() {
  if (!parsedStorage || !selectedItem) {
    return;
  }
  const chunk = getSelectedChunk();
  if (!chunk) {
    return;
  }
  const bytes = readChunkBytes(parsedStorage.buffer, chunk);
  const url = URL.createObjectURL(new Blob([bytes]));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${sanitizeFilename(selectedItem.name || selectedItem.ulid)}-history-${selectedHistory}.bin`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function resetSelection() {
  selectedItem = null;
  selectedHistory = 1;
  releaseObjectUrl();
  elements.detailEmpty.classList.remove("hidden");
  elements.detail.classList.add("hidden");
  elements.downloadButton.disabled = true;
  updateS3UploadButtonState();
}

function suggestDefaultS3ObjectKey() {
  if (!elements.s3ObjectKey || elements.s3ObjectKey.value.trim()) {
    return;
  }

  if (!parsedStorage?.file) {
    return;
  }

  const stamp = new Date().toISOString().replace(/[:]/g, "-").replace(/\..+/, "");
  const safeName = sanitizeFilename(parsedStorage.file.name);
  elements.s3ObjectKey.value = `storageservicelite/${stamp}/${safeName}`;
}

function updateS3UploadButtonState() {
  if (!elements.s3UploadButton) {
    return;
  }
  const isReady =
    Boolean(parsedStorage) &&
    hasValue(elements.s3Endpoint) &&
    hasValue(elements.s3Region) &&
    hasValue(elements.s3Bucket) &&
    hasValue(elements.s3ObjectKey) &&
    hasValue(elements.s3AccessKey) &&
    hasValue(elements.s3SecretKey) &&
    isValidUploadTarget();
  elements.s3UploadButton.disabled = !isReady;
}

function isValidUploadTarget() {
  const target = elements.s3UploadTarget?.value ?? "storage-file";
  if (target === "storage-file") {
    return Boolean(parsedStorage?.file);
  }
  if (target === "selected-history") {
    return Boolean(parsedStorage && selectedItem && !selectedItem.deleted && getSelectedChunk());
  }
  return false;
}

/** @param {HTMLInputElement | HTMLSelectElement | null} element */
function hasValue(element) {
  return Boolean(element?.value?.trim());
}

async function uploadToS3Compatible() {
  if (!parsedStorage) {
    setS3UploadStatus("先に .ssobj を読み込んでください。", true);
    return;
  }

  const endpoint = elements.s3Endpoint?.value.trim() ?? "";
  const region = elements.s3Region?.value.trim() ?? "";
  const bucket = elements.s3Bucket?.value.trim() ?? "";
  const key = elements.s3ObjectKey?.value.trim() ?? "";
  const accessKeyId = elements.s3AccessKey?.value.trim() ?? "";
  const secretAccessKey = elements.s3SecretKey?.value.trim() ?? "";
  const sessionToken = elements.s3SessionToken?.value.trim() ?? "";
  const forcePathStyle = Boolean(elements.s3ForcePathStyle?.checked);
  const target = elements.s3UploadTarget?.value ?? "storage-file";

  if (!endpoint || !region || !bucket || !key || !accessKeyId || !secretAccessKey) {
    setS3UploadStatus("必須項目をすべて入力してください。", true);
    return;
  }

  if (target === "selected-history" && (!selectedItem || selectedItem.deleted || !getSelectedChunk())) {
    setS3UploadStatus("選択中ヒストリーをアップロードするには、有効なアイテムを選択してください。", true);
    return;
  }

  const source = buildUploadSource(target);
  if (!source) {
    setS3UploadStatus("アップロード対象のデータを準備できませんでした。", true);
    return;
  }

  setS3UploadButtonBusy(true);
  setS3UploadStatus("S3互換APIへアップロード中...", false);

  try {
    const sdk = await loadS3Sdk();
    const client = new sdk.S3Client({
      region,
      endpoint,
      forcePathStyle,
      credentials: {
        accessKeyId,
        secretAccessKey,
        ...(sessionToken ? { sessionToken } : {}),
      },
    });

    const command = new sdk.PutObjectCommand({
      Bucket: bucket,
      Key: key,
      Body: source.body,
      ContentType: source.contentType,
      Metadata: {
        source: source.source,
      },
    });

    await client.send(command);
    setS3UploadStatus(`アップロード成功: s3://${bucket}/${key} (${formatBytes(source.size)})`, false);
  } catch (error) {
    const details = error instanceof Error ? error.message : String(error);
    setS3UploadStatus(`アップロード失敗: ${details}`, true);
  } finally {
    setS3UploadButtonBusy(false);
    updateS3UploadButtonState();
  }
}

/** @param {"storage-file" | "selected-history"} target */
function buildUploadSource(target) {
  if (!parsedStorage) {
    return null;
  }

  if (target === "storage-file") {
    return {
      source: "storage-file",
      body: parsedStorage.file,
      size: parsedStorage.file.size,
      contentType: "application/octet-stream",
    };
  }

  const chunk = getSelectedChunk();
  if (!selectedItem || !chunk) {
    return null;
  }
  const bytes = readChunkBytes(parsedStorage.buffer, chunk);
  const contentType = detectImageMime(bytes) ?? (isLikelyText(bytes) ? "text/plain; charset=utf-8" : "application/octet-stream");
  return {
    source: "selected-history",
    body: new Blob([bytes], { type: contentType }),
    size: bytes.byteLength,
    contentType,
  };
}

function setS3UploadButtonBusy(isBusy) {
  if (!elements.s3UploadButton) {
    return;
  }
  elements.s3UploadButton.disabled = isBusy;
  elements.s3UploadButton.textContent = isBusy ? "アップロード中..." : "S3へアップロード";
}

/** @param {string} text
 * @param {boolean} isError
 */
function setS3UploadStatus(text, isError) {
  if (!elements.s3UploadStatus) {
    return;
  }
  elements.s3UploadStatus.classList.toggle("error", isError);
  elements.s3UploadStatus.textContent = text;
}

async function loadS3Sdk() {
  if (!s3SdkModule) {
    s3SdkModule = await import("https://cdn.jsdelivr.net/npm/@aws-sdk/client-s3@3.817.0/+esm");
  }
  return s3SdkModule;
}

function clearMessage() {
  elements.message.className = "message hidden";
  elements.message.textContent = "";
}

/**
 * @param {string} text
 * @param {boolean} error
 */
function showMessage(text, error) {
  elements.message.className = `message${error ? " error" : ""}`;
  elements.message.textContent = text;
}

function releaseObjectUrl() {
  if (activeObjectUrl) {
    URL.revokeObjectURL(activeObjectUrl);
    activeObjectUrl = null;
  }
}

/**
 * @param {ArrayBuffer} buffer
 * @param {ChunkMeta} chunk
 */
function readChunkBytes(buffer, chunk) {
  return new Uint8Array(buffer, chunk.offset + 8, chunk.size);
}

/**
 * @param {DataView} view
 * @param {number} offset
 */
function readUint64AsNumber(view, offset) {
  const value = view.getBigUint64(offset, true);
  const numberValue = Number(value);
  if (!Number.isSafeInteger(numberValue)) {
    throw new Error(`64bit整数がJavaScriptの安全な整数範囲を超えています: ${value}`);
  }
  return numberValue;
}

/**
 * @param {Uint8Array} bytes
 * @param {number} limit
 */
function toHexDump(bytes, limit) {
  const limited = bytes.slice(0, limit);
  const lines = [];
  for (let offset = 0; offset < limited.length; offset += 16) {
    const row = limited.slice(offset, offset + 16);
    const hex = Array.from(row, (byte) => byte.toString(16).padStart(2, "0")).join(" ").padEnd(47, " ");
    const chars = Array.from(row, (byte) => (byte >= 32 && byte <= 126 ? String.fromCharCode(byte) : ".")).join("");
    lines.push(`${offset.toString(16).padStart(8, "0")}  ${hex}  |${chars}|`);
  }
  if (bytes.length > limit) {
    lines.push(`... ${formatBytes(bytes.length - limit)} は省略されました。`);
  }
  return lines.join("\n") || "(empty)";
}

/**
 * @param {Uint8Array} bytes
 * @param {number} start
 * @param {number} end
 */
function ascii(bytes, start, end) {
  return String.fromCharCode(...bytes.slice(start, end));
}

/** @param {number | undefined} unixSeconds */
function formatDate(unixSeconds) {
  if (!Number.isFinite(unixSeconds)) {
    return "-";
  }
  return new Date(Number(unixSeconds) * 1000).toLocaleString("ja-JP", {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}

/** @param {number} bytes */
function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) {
    return "-";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toLocaleString("ja-JP", { maximumFractionDigits: value >= 10 ? 1 : 2 })} ${units[unitIndex]}`;
}

/** @param {string} value */
function sanitizeFilename(value) {
  return value.replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80) || "storageservicelite-item";
}

/** @param {string} value */
function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}
