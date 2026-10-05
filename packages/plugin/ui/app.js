// Phase 3 status UI. Pings same-origin health + manifest (dev serves both).
function setText(id, value) {
  var el = document.getElementById(id);
  if (el) el.textContent = value;
}

fetch("/api/v1/health")
  .then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  })
  .then(function (j) {
    setText("health", "ok: " + JSON.stringify(j).slice(0, 160));
  })
  .catch(function (e) {
    setText("health", "unreachable (" + e.message + ")");
  });

fetch("/plugin/plugin.json")
  .then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  })
  .then(function (j) {
    setText("manifest", j.name + " phase " + j.phase + " (" + j.mcp.endpoint + ")");
  })
  .catch(function (e) {
    setText("manifest", "unreachable (" + e.message + ")");
  });
