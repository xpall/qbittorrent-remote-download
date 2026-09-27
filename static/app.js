"use strict";

const MEDIA_LABELS = { movies: "Movies", shows: "Shows" };

const STATE_LABELS = {
  metaDL: "Fetching metadata",
  allocating: "Allocating space",
  checkingDL: "Checking files",
  checkingUP: "Checking files",
  downloading: "Downloading",
  forcedDL: "Downloading",
  stalledDL: "Stalled",
  pausedDL: "Paused",
  queuedDL: "Queued",
  uploading: "Complete — seeding",
  forcedUP: "Complete — seeding",
  stalledUP: "Complete — seeding",
  pausedUP: "Complete — paused",
  queuedUP: "Complete — queued",
  moving: "Moving files",
  missingFiles: "Files missing",
  error: "Error",
  unknown: "Unknown",
};

const JOB_LABELS = {
  pending: "Naming…",
  waiting: "Naming…",
  named: "Named",
  error: "Naming failed",
};

const state = {
  mediaTypes: ["movies", "shows"],
  maxBatch: 10,
  submitting: false,
};

const $ = (selector, root = document) => root.querySelector(selector);

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1000) return `${value} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let result = value / 1000;
  let unit = units[0];
  for (const candidate of units) {
    unit = candidate;
    if (result < 1000) break;
    result /= 1000;
  }
  return `${result >= 100 ? result.toFixed(0) : result.toFixed(1)} ${unit}`;
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0 || value >= 86400) return "—";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${Math.floor(value)}s`;
}

function showBanner(message, isError = false) {
  const banner = $("#banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
  banner.classList.toggle("error", isError);
}

function hideBanner() {
  $("#banner").classList.add("hidden");
}

/* -- entry rows -------------------------------------------------------- */

function createEntry(prefill = {}) {
  const row = $("#entry-template").content.firstElementChild.cloneNode(true);
  const type = state.mediaTypes.includes(prefill.media_type)
    ? prefill.media_type
    : state.mediaTypes[0];
  row.dataset.mediaType = type;

  for (const button of row.querySelectorAll(".seg")) {
    const buttonType = button.dataset.type;
    if (!state.mediaTypes.includes(buttonType)) {
      button.remove();
      continue;
    }
    button.classList.toggle("active", buttonType === type);
    button.addEventListener("click", () => {
      row.dataset.mediaType = buttonType;
      for (const other of row.querySelectorAll(".seg")) {
        other.classList.toggle("active", other === button);
      }
    });
  }

  row.querySelector(".remove").addEventListener("click", () => {
    row.remove();
    if (!$("#entries .entry")) addEntry();
    updateAddRowButton();
  });

  if (prefill.magnet) $(".magnet", row).value = prefill.magnet;
  if (prefill.folder_name) $(".folder", row).value = prefill.folder_name;
  if (prefill.file_name) $(".file", row).value = prefill.file_name;
  return row;
}

function addEntry(prefill = {}) {
  $("#entries").append(createEntry(prefill));
  updateAddRowButton();
}

function updateAddRowButton() {
  const count = document.querySelectorAll("#entries .entry").length;
  $("#add-row").disabled = count >= state.maxBatch;
}

function showEntryError(row, message) {
  const node = $(".entry-error", row);
  if (!message) {
    node.textContent = "";
    node.classList.add("hidden");
    return;
  }
  node.textContent = message;
  node.classList.remove("hidden");
}

function collectEntries() {
  const entries = [];
  const rows = [];
  for (const row of document.querySelectorAll("#entries .entry")) {
    const magnet = $(".magnet", row).value.trim();
    const folderName = $(".folder", row).value.trim();
    const fileName = $(".file", row).value.trim();
    if (!magnet && !folderName && !fileName) continue; // blank row
    entries.push({
      media_type: row.dataset.mediaType,
      magnet,
      folder_name: folderName,
      file_name: fileName,
    });
    rows.push(row);
  }
  return { entries, rows };
}

function showResults(message, isProblem = false) {
  const node = $("#results");
  node.textContent = message;
  node.classList.remove("hidden");
  node.classList.toggle("problems", isProblem);
}

function validateEntries(entries, rows) {
  let valid = true;
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const row = rows[index];
    if (!entry.magnet) {
      showEntryError(row, "Paste a magnet link.");
      valid = false;
    } else if (!/^magnet:\?/i.test(entry.magnet)) {
      showEntryError(row, "That does not look like a magnet link.");
      valid = false;
    } else if (!entry.folder_name) {
      showEntryError(row, "Enter a folder name.");
      valid = false;
    }
  }
  return valid;
}

/* -- submit ------------------------------------------------------------ */

async function onSubmit(event) {
  event.preventDefault();
  if (state.submitting) return;

  const { entries, rows } = collectEntries();
  rows.forEach((row) => showEntryError(row, ""));

  if (!entries.length) {
    showResults("Fill in at least one entry first.", true);
    return;
  }
  if (!validateEntries(entries, rows)) {
    showResults("Fix the highlighted entries first.", true);
    return;
  }
  if (entries.length > state.maxBatch) {
    showResults(`At most ${state.maxBatch} entries per submission.`, true);
    return;
  }

  state.submitting = true;
  const submit = $("#submit");
  submit.disabled = true;

  try {
    const response = await fetch("/api/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries }),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        if (payload.detail) detail = payload.detail;
      } catch (error) {
        /* keep the HTTP status */
      }
      throw new Error(detail);
    }

    const data = await response.json();
    let added = 0;
    let failed = 0;
    for (const result of data.results || []) {
      const row = rows[result.index];
      if (result.ok) {
        added += 1;
        if (row) row.remove();
      } else {
        failed += 1;
        if (row) showEntryError(row, result.error || "Failed to add.");
      }
    }

    if (added > 0 && failed === 0) showResults(`${added} download${added === 1 ? "" : "s"} added.`);
    else if (added > 0) showResults(`${added} added, ${failed} failed.`, true);
    else showResults(`Nothing was added (${failed} failed).`, true);

    if (!$("#entries .entry")) addEntry();
    updateAddRowButton();
    await refreshDownloads();
  } catch (error) {
    showResults(`Request failed: ${error.message}`, true);
  } finally {
    state.submitting = false;
    submit.disabled = false;
  }
}

/* -- downloads list ---------------------------------------------------- */

function renderDownload(item) {
  const { job, download } = item;
  const node = $("#download-template").content.firstElementChild.cloneNode(true);

  $(".title", node).textContent = job.folder_name;
  $(".badge", node).textContent = MEDIA_LABELS[job.media_type] || job.media_type;

  const percent = download ? Math.max(0, Math.min(1, Number(download.progress) || 0)) : 0;
  $(".progress-fill", node).style.width = `${(percent * 100).toFixed(1)}%`;
  $(".percent", node).textContent = download ? `${Math.floor(percent * 100)}%` : "";

  let speed = "";
  let eta = "";
  if (download && percent < 1) {
    if (Number(download.dlspeed) > 0) {
      speed = `${formatBytes(download.dlspeed)}/s`;
      eta = `ETA ${formatEta(download.eta)}`;
    } else {
      speed = "No peers yet";
    }
  }
  $(".speed", node).textContent = speed;
  $(".eta", node).textContent = eta;

  const parts = [];
  if (download) parts.push(STATE_LABELS[download.state] || download.state || "Unknown");
  else parts.push("Not found in qBittorrent");
  parts.push(JOB_LABELS[job.status] || job.status);
  if (job.message) parts.push(job.message);
  $(".status", node).textContent = parts.join(" · ");

  if (download && percent >= 1) node.classList.add("complete");
  if (job.status === "error") node.classList.add("error");
  return node;
}

function renderDownloads(downloads) {
  const container = $("#downloads");
  container.textContent = "";

  if (!downloads.length) {
    const empty = document.createElement("p");
    empty.className = "muted empty";
    empty.textContent = "Nothing added yet.";
    container.append(empty);
    return;
  }
  for (const item of downloads) container.append(renderDownload(item));
}

async function refreshDownloads() {
  try {
    const response = await fetch("/api/downloads");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderDownloads(data.downloads || []);
    $("#updated").textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    $("#updated").textContent = "";
  }
}

/* -- health ------------------------------------------------------------ */

async function refreshHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const problems = data.problems || [];
    if (!data.qbittorrent?.ok) {
      showBanner(
        `Can't reach qBittorrent: ${data.qbittorrent?.error || "unknown error"}`,
        true,
      );
    } else if (problems.length) {
      showBanner(problems.join(" "), false);
    } else {
      hideBanner();
    }
  } catch (error) {
    showBanner(`App API unreachable: ${error.message}`, true);
  }
}

/* -- init -------------------------------------------------------------- */

async function tick() {
  if (document.hidden) return;
  await Promise.all([refreshDownloads(), refreshHealth()]);
}

async function init() {
  try {
    const response = await fetch("/api/config");
    if (response.ok) {
      const data = await response.json();
      if (Array.isArray(data.media_types) && data.media_types.length) {
        state.mediaTypes = data.media_types;
      }
      if (Number(data.max_batch) > 0) state.maxBatch = Number(data.max_batch);
    }
  } catch (error) {
    /* defaults are fine */
  }

  $("#add-row").addEventListener("click", () => addEntry());
  $("#add-form").addEventListener("submit", onSubmit);
  $("#refresh").addEventListener("click", tick);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) tick();
  });

  addEntry();
  await tick();
  setInterval(tick, 4000);
}

init();
