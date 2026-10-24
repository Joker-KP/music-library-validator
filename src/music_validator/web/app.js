"use strict";

const $ = id => document.getElementById(id);
const names = {configured: "Configured audit", integrity: "Integrity analysis", identity: "Audio-identity analysis"};
const titles = {overview: "Overview", albums: "Albums & issues", integrity: "Audio integrity", identity: "Audio matches"};
const activeStates = new Set(["starting", "running", "cancelling"]);
const state = {view: "overview", latest: {}, pinned: {}, job: null, external: false, filters: {}, pages: {}, revision: 0, resetting: false, resetId: null, epoch: 0};

function node(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) element.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}
function fail(error) { $("error").textContent = error.message; $("error").hidden = false; }
function safe(fn) { return async (...args) => { try { await fn(...args); } catch (error) { fail(error); } }; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache: "no-store"} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Music-Validator": "1"}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
const pretty = value => String(value ?? "").replaceAll("_", " ");
const date = value => value ? new Date(value).toLocaleString() : "Never";
function duration(value) {
  if (value === null || value === undefined) return "estimating";
  value = Math.max(0, Math.round(value));
  return value >= 3600 ? `${Math.floor(value / 3600)}h ${Math.floor(value % 3600 / 60)}m` : value >= 60 ? `${Math.floor(value / 60)}m ${value % 60}s` : `${value}s`;
}
function badge(value) { return node("span", {class: `badge ${/^[a-z_]+$/.test(value) ? value : ""}`}, pretty(value)); }
function empty(message) { return node("p", {class: "empty"}, message); }
function panel(title, ...content) { return node("section", {class: "panel"}, node("h2", {}, title), ...content); }
function table(columns, rows) {
  return node("div", {class: "table-wrap"}, node("table", {},
    node("thead", {}, node("tr", {}, columns.map(col => node("th", {scope: "col"}, col[0])))),
    node("tbody", {}, rows.map(row => node("tr", {}, columns.map(col => node("td", {class: col[2] || ""}, col[1](row))))))));
}
function pager(data, key) {
  const end = Math.min(data.page * data.page_size, data.matching);
  return node("div", {class: "pagination"},
    node("span", {}, `${data.matching ? (data.page - 1) * data.page_size + 1 : 0}–${end} of ${data.matching} matching · ${data.total} total`),
    node("button", {class: "secondary", disabled: data.page <= 1 ? "" : null, onclick: safe(() => { state.pages[key] = data.page - 1; return render(); })}, "Previous"),
    node("button", {class: "secondary", disabled: end >= data.matching ? "" : null, onclick: safe(() => { state.pages[key] = data.page + 1; return render(); })}, "Next"));
}
function filters() { return state.filters[state.view] ||= {}; }
function setFilter(key, value) {
  filters()[key] = value;
  state.pages = {};
  if (key !== "album") delete filters().album;
  return render();
}
function choice(label, key, options) {
  const select = node("select", {id: `filter-${key}`, onchange: safe(event => setFilter(key, event.target.value))},
    options.map(([value, text]) => node("option", {value, selected: (filters()[key] || "") === value ? "" : null}, text)));
  return node("label", {}, label, select);
}
let searchTimer;
function toolbar(...items) {
  const search = node("input", {id: "report-search", type: "search", placeholder: "Search paths, tags or diagnostics…", value: filters().q || "", oninput: event => {
    clearTimeout(searchTimer);
    const value = event.target.value;
    searchTimer = setTimeout(safe(() => setFilter("q", value)), 300);
  }});
  return node("div", {class: "toolbar"}, node("label", {class: "search"}, "Search", search), ...items);
}
async function query(mode, resource, extra = {}, key = resource) {
  const current = {...filters()};
  if (resource !== "albums") { delete current.sort; delete current.descending; }
  const params = new URLSearchParams({...current, generation: state.pinned[mode].generation, page: state.pages[key] || 1, ...extra});
  return api(`/api/reports/${mode}/${resource}?${params}`);
}
function exportsFor(mode, catalog = false) {
  const generation = state.pinned[mode].generation;
  const files = catalog ? [["catalog.html", "Catalog HTML"], ["catalog.csv", "Catalog CSV"], ["catalog.json", "Catalog JSON"]]
    : [["index.html", "Standalone HTML"], ["issues.csv", "All issues CSV"], ["report.json", "Full JSON"]];
  return node("div", {class: "exports"}, files.map(([file, label]) => node("a", {href: `/reports/${mode}/${generation}/${file}`, download: file}, label)));
}
function reportHeading(mode) {
  const summary = state.pinned[mode];
  return node("div", {class: "section-tools"},
    node("div", {}, node("p", {class: "eyebrow"}, "PUBLISHED RESULTS"), node("h2", {}, names[mode]),
      node("p", {class: "muted"}, `${date(summary.generated_at)} · ${summary.tracks} tracks · ${summary.issues} issues`)), exportsFor(mode));
}
function issueFilters(mode) {
  return [choice("Severity", "severity", [["", "All severities"], ...["error", "warning", "info"].map(x => [x, x])]),
    choice("Rule", "rule", [["", "All rules"], ...Object.keys(state.pinned[mode].rules).map(x => [x, x])])];
}
function issuesTable(data) {
  if (!data.items.length) return empty("No matching issues. Adjust filters to inspect other findings.");
  return table([["Track", r => r.path, "path"], ["Rule", r => r.rule], ["Severity", r => badge(r.severity)], ["Diagnostics", r => r.message, "message"]], data.items);
}
function settings(details) {
  const pairs = [];
  function walk(value, prefix = "") {
    for (const [key, item] of Object.entries(value || {})) {
      const name = prefix + pretty(key);
      if (item && typeof item === "object" && !Array.isArray(item)) walk(item, name + " / ");
      else pairs.push(node("dt", {}, name), node("dd", {}, Array.isArray(item) ? item.join(", ") : String(item)));
    }
  }
  walk(details);
  return node("dl", {class: "settings"}, pairs);
}
function overview() {
  const normal = state.pinned.configured;
  const content = [];
  if (normal?.available) {
    content.push(node("div", {class: "cards"}, [[normal.albums, "Albums"], [normal.tracks, "Tracks scanned"], [normal.albums_with_issues, "Albums with issues"], [normal.affected_tracks, "Tracks with issues"]]
      .map(([value, label]) => node("div", {class: "stat"}, node("strong", {}, value), node("span", {}, label)))));
  }
  content.push(panel("Latest published analyses", node("p", {class: "muted"}, "Each analysis publishes independently. These timestamps can differ."),
    node("div", {class: "report-grid"}, Object.keys(names).map(mode => {
      const report = state.pinned[mode];
      const target = mode === "configured" ? "albums" : mode;
      return node("article", {class: "report-card"}, node("h3", {}, names[mode]),
        node("p", {class: "muted"}, report?.available ? date(report.generated_at) : "No published report"),
        report?.available ? node("p", {}, `${report.issues} issues · ${report.affected_tracks} affected tracks`) : null,
        report?.available ? node("a", {href: `#${target}`}, "Explore results →") : node("p", {class: "muted"}, "Start this analysis above to create its first report."));
    }))));
  if (normal?.available) {
    content.push(panel("Audit findings", node("p", {class: "muted"}, `${normal.issues} issues; a track may have several. Format/bitrate quality and stream integrity are separate assessments.`),
      node("div", {class: "rule-list"}, ["error", "warning", "info"].map(severity => node("button", {onclick: () => { location.hash = `albums?severity=${severity}`; }}, `${severity}: ${normal.severities[severity] || 0}`))),
      node("h3", {}, "Browse by rule"), node("div", {class: "rule-list"}, Object.entries(normal.rules).map(([rule, count]) => node("button", {onclick: () => { location.hash = `albums?rule=${encodeURIComponent(rule)}`; }}, rule, node("span", {}, count)))),
      normal.issues ? null : empty("No validation issues in the configured audit."), exportsFor("configured")));
  }
  return content;
}
async function albums() {
  const data = await query("configured", "albums", {sort: filters().sort || "album_dir"});
  const checkbox = node("input", {type: "checkbox", checked: filters().issues_only === "true" ? "" : null,
    onchange: safe(event => setFilter("issues_only", String(event.target.checked)))});
  const content = [panel("Album catalog", reportHeading("configured"),
    toolbar(...issueFilters("configured"), choice("Sort", "sort", [["", "Directory"], ["album_artist", "Artist"], ["year", "Year"], ["album", "Album"], ["quality", "Quality label"], ["folder_size_mb", "Folder size"], ["total_tracks", "Track count"], ["number_of_issues", "Issue count"]]),
      choice("Order", "descending", [["", "Ascending"], ["true", "Descending"]]), node("label", {class: "inline-check"}, checkbox, "With issues only")),
    node("p", {class: "muted"}, "One row per directory album, including all collapsed disc folders. Quality summarizes the lowest track format/bitrate; it is not an integrity verdict."),
    data.items.length ? table([["Artist", r => r.album_artist], ["Year", r => r.year], ["Album", r => node("button", {class: "table-button", onclick: safe(() => setFilter("album", r.album_dir))}, r.album)], ["Quality", r => r.quality], ["MB", r => r.folder_size_mb, "numeric"], ["Tracks", r => r.total_tracks, "numeric"], ["Issues", r => r.number_of_issues || "0", "numeric"], ["Parent folder", r => r.parent_folder]], data.items) : empty("No matching albums."),
    pager(data, "albums"), exportsFor("configured", true))];
  const issues = await query("configured", "issues", {}, "album-issues");
  content.push(panel(filters().album ? "Selected album issues" : "Matching track issues",
    filters().album ? node("p", {class: "path"}, filters().album, " ", node("button", {class: "secondary", onclick: safe(() => setFilter("album", ""))}, "Clear selection")) : null,
    issuesTable(issues), pager(issues, "album-issues")));
  return content;
}
async function integrity() {
  const data = await query("integrity", "results");
  const summary = state.pinned.integrity;
  const content = [panel("Integrity coverage", reportHeading("integrity"), settings(summary.analysis.statistics),
    node("p", {class: "muted"}, "Passed, warning, failed, incomplete and unchecked outcomes describe different levels of coverage. Cached results retain their original check time."),
    node("details", {}, node("summary", {}, "Effective analysis settings"), settings(summary.analysis.settings))),
    panel("Track / tool results", toolbar(choice("Status", "status", [["", "All statuses"], ...["passed", "warning", "failed", "incomplete", "not_checked"].map(x => [x, pretty(x)])]),
      choice("Execution", "source", [["", "All results"], ["cached", "Cached"], ["executed", "Executed"], ["not_checked", "Not checked"]])),
      data.items.length ? table([["Track", r => r.path, "path"], ["Tool", r => r.tool || "—"], ["Result", r => badge(r.status)], ["Execution", r => pretty(r.source)], ["Last checked", r => date(r.checked_at)], ["Diagnostics", r => (r.findings || []).map(x => x.message).join("\n"), "message"]], data.items) : empty("No matching check results."), pager(data, "results"))];
  const issues = await query("integrity", "issues", {}, "integrity-issues");
  content.push(panel("Integrity findings", issuesTable(issues), pager(issues, "integrity-issues")));
  return content;
}
async function identity() {
  const groups = await query("identity", "groups", {page_size: "20"});
  const results = await query("identity", "results");
  const summary = state.pinned.identity;
  const checkbox = node("input", {type: "checkbox", checked: filters().conflicts_only === "true" ? "" : null,
    onchange: safe(event => setFilter("conflicts_only", String(event.target.checked)))});
  return [panel("Audio-identity coverage", reportHeading("identity"), settings(summary.analysis.statistics),
    node("p", {class: "muted"}, "Groups are local fingerprint matches, including consistent copies. Similarity and coverage are matching evidence, not probabilities. These labels are not permanent recording IDs."),
    node("details", {}, node("summary", {}, "Effective matching settings"), settings(summary.analysis.settings))),
    panel("Matched recordings", toolbar(node("label", {class: "inline-check"}, checkbox, "Groups with conflicts only")),
      groups.items.length ? groups.items.map(group => {
        const columns = [["Track", r => r.path, "path"], ...summary.analysis.settings.fields.map(field => [field, r => node("span", {class: group.conflicting_fields.includes(field) ? "conflict-value" : ""}, r.values[field] || "〈missing〉")])];
        return node("details", {class: "group"}, node("summary", {}, node("strong", {}, `Group ${group.id}`), node("small", {}, `${group.tracks.length} copies · ${group.conflicting_fields.length ? "conflicts: " + group.conflicting_fields.join(", ") : "consistent compared tags"}`)),
          node("p", {class: "muted"}, `Minimum pair similarity: ${group.similarity_min.toFixed(4)} · minimum pair coverage: ${group.coverage_min.toFixed(4)}`), table(columns, group.tracks));
      }) : empty("No matching audio groups. See track outcomes for unmatched or excluded files."), pager(groups, "groups")),
    panel("Track outcomes", node("div", {class: "toolbar"}, choice("Status", "status", [["", "All statuses"], ...["matched", "unmatched", "fingerprint_error", "metadata_error", "skipped_integrity"].map(x => [x, pretty(x)])]), choice("Execution", "source", [["", "All results"], ["cached", "Cached"], ["executed", "Executed"], ["not_checked", "Not checked"]])),
      results.items.length ? table([["Track", r => r.path, "path"], ["Status", r => badge(r.status)], ["Execution", r => pretty(r.source)], ["Details", r => r.reason || "", "message"]], results.items) : empty("No matching track outcomes."), pager(results, "results"))];
}
function updateNewReport() {
  const modes = state.view === "overview" ? Object.keys(names) : [state.view === "albums" ? "configured" : state.view];
  $("new-report").hidden = !modes.some(mode => state.latest[mode]?.generation !== state.pinned[mode]?.generation);
}
async function render() {
  const revision = ++state.revision;
  const view = state.view;
  const mode = view === "albums" ? "configured" : view;
  const focused = document.activeElement?.id;
  const selection = focused === "report-search" ? document.activeElement.selectionStart : null;
  let content;
  try {
    if (view !== "overview" && !state.pinned[mode]?.available) content = [panel(titles[view], empty("No published report yet. Start this analysis above. Existing reports appear here after successful publication."))];
    else content = view === "overview" ? overview() : await ({albums, integrity, identity}[view])();
  } catch (error) { if (revision === state.revision) throw error; else return; }
  if (revision !== state.revision) return;
  $("results").replaceChildren(...content);
  $("view-title").textContent = titles[view];
  document.title = `${titles[view]} · Music Library Validator`;
  document.querySelectorAll("[data-view]").forEach(link => link.setAttribute("aria-current", link.dataset.view === view ? "page" : "false"));
  if (focused && $(focused)) { $(focused).focus(); if (selection !== null) $(focused).setSelectionRange(selection, selection); }
  updateNewReport();
}
function route() {
  clearTimeout(searchTimer);
  const [view, params] = location.hash.slice(1).split("?");
  state.view = Object.hasOwn(titles, view) ? view : "overview";
  state.filters[state.view] = Object.fromEntries(new URLSearchParams(params || ""));
  state.pages = {};
  state.pinned = {...state.latest};
  return render();
}
function modeDescription() {
  const mode = $("mode").value;
  $("mode-description").textContent = {
    configured: "Validate tags, filenames, artwork and album relationships using YAML, plus any enabled audio checks. Unchanged metadata is revalidated from cache.",
    integrity: "Run the configured full-stream integrity tools only. Tag parsing and audio matching are skipped.",
    identity: "Match recordings by local fingerprints and compare configured tags. Other metadata rules and integrity tools are skipped."
  }[mode];
  $("integrity-option").hidden = mode === "identity";
  $("identity-option").hidden = mode === "integrity";
  if (mode === "identity") $("recheck-integrity").checked = false;
  if (mode === "integrity") $("recheck-identity").checked = false;
}
function renderJob() {
  const job = state.job;
  const active = job && activeStates.has(job.status);
  $("job-status").replaceWith(Object.assign(badge(state.resetting ? "archiving" : state.external ? "external_scan" : job?.status || "ready"), {id: "job-status"}));
  for (const id of ["mode", "full-scan", "recheck-integrity", "recheck-identity", "start"]) $(id).disabled = Boolean(active || state.external || state.resetting);
  $("reset-confirmation").disabled = Boolean(active || state.external || state.resetting);
  $("reset").disabled = Boolean(active || state.external || state.resetting || $("reset-confirmation").value !== "FRESH START");
  $("refresh").disabled = $("load-new").disabled = state.resetting;
  $("job").hidden = !job && !state.external;
  $("cancel").hidden = !active;
  $("cancel").disabled = job?.status === "cancelling";
  $("retry").hidden = !job || active || state.external || state.resetting;
  $("job-results").hidden = !job?.reports_published || active || state.resetting;
  if (!job && !state.external) return;
  const progress = job?.progress;
  $("job-phase").textContent = state.external ? "Another scan is using this library" : `${names[job.options.mode]} · ${progress?.phase || "Starting worker"}`;
  if (progress?.total !== null && progress?.total !== undefined) {
    $("job-progress").max = Math.max(1, progress.total);
    $("job-progress").value = progress.status === "completed" ? Math.max(1, progress.total) : Math.min(progress.completed, Math.max(1, progress.total) * .99);
  } else $("job-progress").removeAttribute("value");
  $("job-progress").hidden = !active;
  $("job-counts").textContent = progress ? `${progress.completed}${progress.total !== null ? " / " + progress.total : ""} completed in this phase · ${duration(progress.elapsed_seconds)} elapsed · phase ETA ${duration(progress.eta_seconds)}` : "";
  const messages = {
    starting: "Preparing the worker.", running: "Latest published results remain available below.",
    cancelling: "Stopping the worker and audio tools. Waiting for cleanup and lock release…",
    completed: "Analysis completed with no issues. Results have been published.",
    completed_with_findings: `Analysis completed with ${job?.issue_count} findings. Results have been published.`,
    failed: job?.error || "The analysis failed. See the job log.", cancelled: "Analysis cancelled."
  };
  let message = state.external ? "Start is unavailable until the CLI or other server scan releases its locks. This server cannot cancel or track external jobs." : messages[job.status];
  if (job && ["failed", "cancelled"].includes(job.status)) message += job.cache_committed ? " The cache committed; inspect the latest published report below. Publication may not have completed." : " Uncommitted scan changes were rolled back; previous reports are preserved.";
  $("job-message").textContent = message;
  $("job-times").textContent = job ? `Started ${date(job.started_at)}${job.finished_at ? " · Finished " + date(job.finished_at) : " · Job elapsed " + duration((Date.now() - new Date(job.started_at)) / 1000)}` : "";
  $("job-log").textContent = job?.logs.join("\n") || "No log output.";
}
async function start(options) {
  $("error").hidden = true;
  $("start").disabled = true;
  try { state.job = await api("/api/jobs", options); renderJob(); }
  finally { if (!state.job || !activeStates.has(state.job.status)) $("start").disabled = state.external; }
}
$("scan-form").addEventListener("submit", safe(async event => {
  event.preventDefault();
  await start({mode: $("mode").value, full_scan: $("full-scan").checked, recheck_integrity: $("recheck-integrity").checked, recheck_identity: $("recheck-identity").checked});
}));
$("cancel").addEventListener("click", safe(async () => { state.job = await api(`/api/jobs/${state.job.id}/cancel`, {}); renderJob(); }));
$("retry").addEventListener("click", safe(() => start(state.job.options)));
$("job-results").addEventListener("click", safe(async () => {
  const target = state.job.options.mode === "configured" ? "albums" : state.job.options.mode;
  await loadLatest();
  if (location.hash === `#${target}`) await route();
  else location.hash = target;
}));
$("mode").addEventListener("change", modeDescription);
$("reset-confirmation").addEventListener("input", renderJob);
$("reset-form").addEventListener("submit", safe(async event => {
  event.preventDefault();
  if (state.resetting || $("reset-confirmation").value !== "FRESH START") return;
  state.resetting = true;
  clearTimeout(searchTimer);
  state.epoch++;
  state.revision++;
  $("error").hidden = true;
  $("reset-status").textContent = "Archiving the active cache and reports…";
  renderJob();
  let archived = false;
  try {
    const receipt = await api("/api/maintenance/fresh-start", {confirmation: "FRESH START"});
    archived = true;
    state.resetId = receipt.id;
    state.job = null;
    state.external = false;
    state.filters = {};
    state.pages = {};
    state.latest = state.pinned = {};
    $("reset-confirmation").value = "";
    const archives = [receipt.data_archive, receipt.reports_archive].filter(Boolean);
    $("reset-status").textContent = archives.length ? `Fresh start complete. Archives: ${archives.join(" · ")}. Start an analysis when ready.` : "Fresh start complete. There was no active cache or generated report to archive. Start an analysis when ready.";
    $("results").replaceChildren(empty("No published reports. Start an analysis above."));
    $("new-report").hidden = true;
    await loadLatest();
  } catch (error) {
    if (!archived) $("reset-status").textContent = "Fresh start did not complete. See the error above.";
    throw error;
  } finally {
    state.resetting = false;
    state.epoch++;
    renderJob();
  }
}));
async function loadLatest() {
  const epoch = state.epoch;
  const reports = await api("/api/reports");
  if (epoch !== state.epoch) return;
  state.latest = reports; state.pinned = {...reports}; state.pages = {};
  $("error").hidden = true;
  await render();
}
$("refresh").addEventListener("click", safe(loadLatest));
$("load-new").addEventListener("click", safe(loadLatest));
window.addEventListener("hashchange", safe(route));
async function poll() {
  const epoch = state.epoch;
  try {
    const [jobs, reports] = await Promise.all([api("/api/jobs"), api("/api/reports")]);
    if (epoch === state.epoch && !state.resetting) {
      const reset = jobs.reset && jobs.reset.id !== state.resetId;
      const removed = Object.keys(names).some(mode => state.pinned[mode]?.available && !reports[mode]?.available);
      state.resetId = jobs.reset?.id || null;
      state.job = jobs.job; state.external = jobs.external_busy; state.latest = reports;
      $("connection").hidden = true;
      renderJob();
      if (!Object.keys(state.pinned).length) await route();
      else if (reset || removed) {
        state.pinned = {...reports}; state.filters = {}; state.pages = {};
        $("reset-confirmation").value = "";
        renderJob();
        await render();
      } else updateNewReport();
    }
  } catch (error) {
    $("connection").textContent = `Server connection unavailable: ${error.message}. Displayed results may be older. Retrying…`;
    $("connection").hidden = false;
  }
  setTimeout(poll, 2000);
}
modeDescription();
poll();
