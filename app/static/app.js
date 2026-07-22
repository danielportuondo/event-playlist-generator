const $ = (id) => document.getElementById(id);

let presets = [];
let lastUris = [];
let lastRows = [];
let isVisitor = false;

async function init() {
  const session = await fetch("/api/session").then((r) => r.json());
  isVisitor = !session.authenticated;
  if (isVisitor) {
    if (session.visitor_live) {
      $("login-banner-text").textContent =
        "You're browsing as a visitor — playlists generate live on Spotify.";
      $("login-banner-link").textContent = "Log in with an invited account to save";
    }
    $("login-banner").classList.remove("hidden");
  }
  if (typeof session.spotify_calls_today === "number") {
    $("call-count").textContent = ` · ${session.spotify_calls_today} Spotify calls today`;
  }

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
    link.textContent = "open it in Spotify";
    $("save-result").append(`Saved ${data.track_count} tracks — `, link);
  } catch (err) {
    $("save-result").textContent = `Save failed: ${err.message}`;
    $("save-btn").disabled = false;
  }
  $("save-result").classList.remove("hidden");
}

/* ---------- exports ---------- */

function trackUrl(uri) {
  return `https://open.spotify.com/track/${uri.split(":").pop()}`;
}

async function copyToClipboard(text, message) {
  const feedback = $("copy-feedback");
  try {
    await navigator.clipboard.writeText(text);
    feedback.textContent = message;
  } catch {
    feedback.textContent = "Couldn't access the clipboard — select and copy from the list below.";
  }
}

function copyLinks() {
  const links = lastRows.filter((r) => r.spotify_uri).map((r) => trackUrl(r.spotify_uri));
  copyToClipboard(
    links.join("\n"),
    `Copied ${links.length} links — paste into a new playlist in Spotify's desktop app.`
  );
}

function copyList() {
  const lines = lastRows.map(
    (r) => `${r.resolved_title || r.title} — ${r.resolved_artist || r.artist}`
  );
  copyToClipboard(lines.join("\n"), `Copied ${lines.length} tracks as text.`);
}

/* ---------- arc visualization ---------- */

const SVG_NS = "http://www.w3.org/2000/svg";
const BAND_COLORS = ["#e8a25c", "#c4685a", "#8a5a76", "#4d4a78", "#1a2140"];

function lerpColor(a, b, t) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const mix = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${mix.join(",")})`;
}

function bandColor(i, n) {
  if (n <= 1) return BAND_COLORS[0];
  const t = (i / (n - 1)) * (BAND_COLORS.length - 1);
  const lo = Math.min(Math.floor(t), BAND_COLORS.length - 2);
  return lerpColor(BAND_COLORS[lo], BAND_COLORS[lo + 1], t - lo);
}

function phaseGroups(rows) {
  const groups = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.phase === row.phase) last.rows.push(row);
    else groups.push({ phase: row.phase, rows: [row] });
  }
  return groups;
}

function el(name, attrs, parent) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// Catmull-Rom spline through the target-energy points, as a cubic bezier path
function splinePath(pts) {
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(i - 1, 0)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(i + 2, pts.length - 1)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C ${c1[0]} ${c1[1]}, ${c2[0]} ${c2[1]}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}

function renderArc(rows) {
  const W = 1000, H = 260;
  const pad = { top: 40, right: 14, bottom: 16, left: 14 };
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const maxE = Math.max(100, ...rows.map((r) => Math.max(r.target_energy, r.actual_energy)));

  const x = (i) => pad.left + ((i + 0.5) / rows.length) * innerW;
  const y = (e) => pad.top + (1 - e / maxE) * innerH;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  svg.setAttribute("aria-label", "Playlist energy across the event's phases");

  const defs = el("defs", {}, svg);
  const grad = el("linearGradient", { id: "arc-grad", x1: 0, y1: 0, x2: 1, y2: 0 }, defs);
  el("stop", { offset: "0%", "stop-color": "#e8a25c" }, grad);
  el("stop", { offset: "100%", "stop-color": "#c4685a" }, grad);

  const groups = phaseGroups(rows);
  let start = 0;
  groups.forEach((g, gi) => {
    const x0 = pad.left + (start / rows.length) * innerW;
    const w = (g.rows.length / rows.length) * innerW;
    el("rect", {
      class: "arc-band", x: x0, y: pad.top, width: w, height: innerH,
      fill: bandColor(gi, groups.length), opacity: 0.16,
    }, svg);
    const label = el("text", { class: "arc-band-label", x: x0 + w / 2, y: 24, "text-anchor": "middle" }, svg);
    label.textContent = g.phase;
    start += g.rows.length;
  });

  const targetPts = rows.map((r, i) => [x(i), y(r.target_energy)]);
  el("path", { class: "arc-line", d: splinePath(targetPts), pathLength: 1 }, svg);

  rows.forEach((r, i) => {
    const dot = el("circle", {
      class: "arc-dot", cx: x(i), cy: y(r.actual_energy), r: 4.5,
      "data-i": i, tabindex: 0,
    }, svg);
    const title = el("title", {}, dot);
    title.textContent = `${r.resolved_title || r.title} — energy ${r.actual_energy} (target ${Math.round(r.target_energy)})`;
    for (const evt of ["mouseenter", "focus"]) dot.addEventListener(evt, () => highlight(i, true));
    for (const evt of ["mouseleave", "blur"]) dot.addEventListener(evt, () => highlight(i, false));
  });

  const mount = $("arc-mount");
  mount.innerHTML = "";
  mount.appendChild(svg);
}

function highlight(i, on) {
  for (const node of document.querySelectorAll(`[data-i="${i}"]`)) {
    node.classList.toggle("active", on);
  }
}

/* ---------- track list ---------- */

function renderTrackList(rows) {
  const list = $("track-list");
  list.innerHTML = "";
  const groups = phaseGroups(rows);

  groups.forEach((g, gi) => {
    const section = document.createElement("section");
    section.className = "phase-group";

    const head = document.createElement("div");
    head.className = "phase-head";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = bandColor(gi, groups.length);
    const h3 = document.createElement("h3");
    h3.textContent = g.phase;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${g.rows.length} ${g.rows.length === 1 ? "track" : "tracks"}`;
    head.append(swatch, h3, count);
    section.appendChild(head);

    for (const row of g.rows) {
      const i = row.slot_index;
      const div = document.createElement("div");
      div.className = "track-row";
      div.dataset.i = i;

      const no = document.createElement("span");
      no.className = "track-no";
      no.textContent = String(i + 1).padStart(2, "0");

      const name = document.createElement("div");
      name.className = "track-name";
      if (row.spotify_uri) {
        const link = document.createElement("a");
        link.className = "track-link";
        link.href = trackUrl(row.spotify_uri);
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = row.resolved_title || row.title;
        name.appendChild(link);
      } else {
        name.textContent = row.resolved_title || row.title;
      }
      const artist = document.createElement("span");
      artist.className = "artist";
      artist.textContent = ` — ${row.resolved_artist || row.artist}`;
      name.appendChild(artist);

      const meter = document.createElement("div");
      meter.className = "meter";
      const fill = document.createElement("span");
      fill.className = "fill";
      fill.style.width = `${Math.min(row.actual_energy, 100)}%`;
      const tick = document.createElement("span");
      tick.className = "tick";
      tick.style.left = `${Math.min(row.target_energy, 100)}%`;
      meter.append(fill, tick);

      const rationale = document.createElement("div");
      rationale.className = "track-rationale";
      rationale.textContent = row.rationale;

      div.append(no, name, meter, rationale);
      div.addEventListener("mouseenter", () => highlight(i, true));
      div.addEventListener("mouseleave", () => highlight(i, false));
      section.appendChild(div);
    }
    list.appendChild(section);
  });
}

function renderResults(data) {
  const res = data.resolution;
  $("summary").textContent =
    `${data.rows.length} tracks · resolved ${res.resolved}/${res.total} ` +
    `(${Math.round(res.rate * 100)}%) · sequencing cost ${data.total_cost.toFixed(1)}`;

  const warning = $("warning");
  warning.textContent = data.warning || "";
  warning.classList.toggle("hidden", !data.warning);

  renderArc(data.rows);
  renderTrackList(data.rows);

  lastRows = data.rows;
  lastUris = data.rows.map((row) => row.spotify_uri);
  const preset = presets.find((p) => p.id === $("event-select").value);
  $("playlist-name").value = preset ? `${preset.label} mix` : "Event mix";
  $("save-btn").disabled = false;
  $("save-result").classList.add("hidden");
  $("copy-feedback").textContent = "";
  $("save-area").classList.toggle("hidden", isVisitor);
  $("visitor-note").classList.toggle("hidden", !isVisitor);
  $("results").classList.remove("hidden");
}

$("brief-form").addEventListener("submit", generate);
$("save-btn").addEventListener("click", save);
$("copy-links-btn").addEventListener("click", copyLinks);
$("copy-list-btn").addEventListener("click", copyList);
init();
