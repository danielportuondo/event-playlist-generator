const $ = (id) => document.getElementById(id);

let presets = [];
let lastUris = [];

async function init() {
  const session = await fetch("/api/session").then((r) => r.json());
  if (!session.authenticated) $("login-banner").classList.remove("hidden");

  presets = await fetch("/api/presets").then((r) => r.json());
  const select = $("event-select");
  for (const p of presets) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label;
    select.appendChild(opt);
  }
  select.addEventListener("change", applyPresetDefaults);
  applyPresetDefaults();
}

function applyPresetDefaults() {
  const preset = presets.find((p) => p.id === $("event-select").value);
  if (!preset) return;
  $("duration").value = preset.default_duration_min;
  $("event-description").textContent = preset.description;
}

function collectSeeds() {
  const seeds = [];
  for (const n of [1, 2]) {
    const title = $(`seed${n}-title`).value.trim();
    const artist = $(`seed${n}-artist`).value.trim();
    if (title && artist) seeds.push({ title, artist });
  }
  return seeds;
}

async function generate(event) {
  event.preventDefault();
  $("error").classList.add("hidden");
  $("results").classList.add("hidden");
  $("generate-btn").disabled = true;
  $("status").textContent = "Generating… this takes up to a minute (LLM + Spotify search).";
  $("status").classList.remove("hidden");

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event_id: $("event-select").value,
        seeds: collectSeeds(),
        duration_min: Number($("duration").value) || null,
        vibe: $("vibe").value.trim(),
        discovery_mode: $("discovery").checked,
        allow_explicit: $("explicit").checked,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    renderResults(data);
  } catch (err) {
    $("error").textContent = err.message;
    $("error").classList.remove("hidden");
  } finally {
    $("generate-btn").disabled = false;
    $("status").classList.add("hidden");
  }
}

async function save() {
  const name = $("playlist-name").value.trim();
  if (!name || !lastUris.length) return;

  $("save-btn").disabled = true;
  $("save-result").classList.add("hidden");
  try {
    const response = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, uris: lastUris }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    $("save-result").innerHTML = "";
    const link = document.createElement("a");
    link.href = data.playlist_url;
    link.target = "_blank";
    link.textContent = name;
    $("save-result").append(`Saved ${data.track_count} tracks to `, link);
  } catch (err) {
    $("save-result").textContent = `Save failed: ${err.message}`;
    $("save-btn").disabled = false;
  }
  $("save-result").classList.remove("hidden");
}

function renderResults(data) {
  const res = data.resolution;
  $("summary").textContent =
    `${data.rows.length} tracks · resolved ${res.resolved}/${res.total} ` +
    `(${Math.round(res.rate * 100)}%) · sequencing cost ${data.total_cost.toFixed(1)}`;

  const warning = $("warning");
  warning.textContent = data.warning || "";
  warning.classList.toggle("hidden", !data.warning);

  const body = $("results-body");
  body.innerHTML = "";
  for (const row of data.rows) {
    const tr = document.createElement("tr");
    const track = `${row.resolved_title || row.title} — ${row.resolved_artist || row.artist}`;
    const cells = [
      row.slot_index + 1,
      row.phase,
      Math.round(row.target_energy),
      row.actual_energy,
      track,
      row.rationale,
    ];
    for (const value of cells) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }

  lastUris = data.rows.map((row) => row.spotify_uri);
  const preset = presets.find((p) => p.id === $("event-select").value);
  $("playlist-name").value = preset ? `${preset.label} mix` : "Event mix";
  $("save-btn").disabled = false;
  $("save-result").classList.add("hidden");
  $("results").classList.remove("hidden");
}

$("brief-form").addEventListener("submit", generate);
$("save-btn").addEventListener("click", save);
init();
