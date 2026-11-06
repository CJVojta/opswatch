"use strict";

const byId = id => document.getElementById(id);
const formatTime = timestamp => new Date(timestamp * 1000).toLocaleString();
const stateLabels = {up: "Operational", down: "Down", unknown: "Waiting"};
const monitorGrid = byId("monitors");
const form = byId("add-form");
const historyContent = byId("history-content");
let refreshVersion = 0;

function element(tag, className, content) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (content !== undefined) result.textContent = content;
  return result;
}

function showError(id, message = "") {
  const output = byId(id);
  output.hidden = !message;
  output.textContent = message;
}

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
  return body;
}

function reading(value, label) {
  const result = element("span");
  result.append(element("strong", "", value), label);
  return result;
}

function monitorCard(monitor, demo) {
  const card = element("article", "card");
  const heading = element("div", "card-heading");
  heading.append(element("h3", "", monitor.name),
    element("span", `badge ${monitor.state}`, stateLabels[monitor.state]));
  const stats = element("div", "readings");
  stats.append(reading(monitor.uptime == null ? "—" : `${monitor.uptime}%`, "uptime · 24h"),
    reading(monitor.avg_ms == null ? "—" : `${monitor.avg_ms} ms`, "avg. latency · 24h"));
  const spark = element("div", "spark");
  const bars = Array.from({length: 24 - monitor.recent.length}, () => element("span"));
  for (const check of monitor.recent.slice().reverse()) {
    const bar = element("span", check.ok ? "up" : "down");
    bar.title = `${formatTime(check.checked_at)} · ${check.detail}`;
    bars.push(bar);
  }
  spark.append(...bars);
  spark.setAttribute("aria-hidden", "true");
  const controls = element("div", "actions");
  const history = element("button", "secondary", "Check history");
  history.setAttribute("aria-label", `Check history for ${monitor.name}`);
  history.addEventListener("click", () => showHistory(monitor));
  controls.append(history);
  if (!demo) {
    const remove = element("button", "secondary", "Remove");
    remove.setAttribute("aria-label", `Remove ${monitor.name}`);
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Remove "${monitor.name}" and its check and incident history?`)) return;
      remove.disabled = true;
      try {
        await request(`/api/monitors/${monitor.id}`, {method: "DELETE"});
        await refresh(true);
        byId("open-form").focus();
      } catch (error) {
        showError("error", error.message);
      } finally {
        remove.disabled = false;
      }
    });
    controls.append(remove);
  }
  card.append(heading, element("p", "muted url", monitor.url), stats, spark, controls);
  return card;
}

function incidentRow(incident) {
  const row = element("li", "incident");
  const details = element("div");
  const times = incident.resolved_at == null
    ? `Opened ${formatTime(incident.opened_at)}`
    : `${formatTime(incident.opened_at)} → ${formatTime(incident.resolved_at)}`;
  details.append(element("strong", "", incident.name),
    element("p", "muted", `${incident.reason} · ${times}`));
  row.append(details, incident.resolved_at == null
    ? element("span", "badge down", "Active")
    : element("span", "badge up", "Resolved"));
  return row;
}

async function refresh(force = false) {
  const version = ++refreshVersion;
  try {
    const {monitors, incidents, demo} = await request("/api/overview");
    if (version !== refreshVersion) return;
    if (!force && monitorGrid.contains(document.activeElement)) return;
    byId("mode").textContent = demo ? "DEMO DATA" : "LIVE MONITORING";
    byId("open-form").hidden = demo;
    byId("total").textContent = monitors.length;
    byId("up").textContent = monitors.filter(monitor => monitor.state === "up").length;
    byId("open").textContent = monitors.filter(monitor => monitor.state === "down").length;
    byId("samples").textContent = monitors.reduce((sum, monitor) => sum + (monitor.samples ?? 0), 0);
    byId("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    monitorGrid.replaceChildren(...(monitors.length ? monitors.map(monitor => monitorCard(monitor, demo)) :
      [element("p", "empty", "Add a monitor to start collecting checks.")]));
    byId("incidents").replaceChildren(...(incidents.length ? incidents.map(incidentRow) :
      [element("li", "empty", "No incidents recorded.")]));
    showError("error");
  } catch (error) {
    if (version === refreshVersion) showError("error", `Could not refresh dashboard. ${error.message}`);
  }
}

async function showHistory(monitor) {
  const dialog = byId("history");
  byId("history-title").textContent = `${monitor.name} · checks`;
  const result = element("div", "table-wrap", "Loading…");
  historyContent.replaceChildren(result);
  dialog.showModal();
  try {
    const checks = await request(`/api/monitors/${monitor.id}/checks`);
    if (!checks.length) {
      result.textContent = "No checks recorded yet.";
      return;
    }
    const table = element("table");
    const header = table.createTHead().insertRow();
    for (const label of ["Time", "Result", "Latency", "Detail"]) {
      const heading = element("th", "", label);
      heading.scope = "col";
      header.append(heading);
    }
    const body = table.createTBody();
    for (const check of checks) {
      const row = body.insertRow();
      for (const value of [formatTime(check.checked_at), check.ok ? "Passed" : "Failed",
        `${check.latency_ms} ms`, check.detail]) {
        row.insertCell().textContent = value;
      }
    }
    result.replaceChildren(table);
  } catch (error) {
    result.textContent = error.message;
  }
}

byId("open-form").addEventListener("click", () => {
  showError("form-error");
  byId("monitor-form").showModal();
});
for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => byId(button.dataset.close).close());
}
form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector('button:not([type])');
  const data = Object.fromEntries(new FormData(form));
  data.interval = Number(data.interval);
  data.timeout = Number(data.timeout);
  button.disabled = true;
  showError("form-error");
  try {
    await request("/api/monitors", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)
    });
    form.reset();
    byId("monitor-form").close();
    await refresh(true);
  } catch (error) {
    showError("form-error", error.message);
  } finally {
    button.disabled = false;
  }
});

refresh();
setInterval(refresh, 5000);
