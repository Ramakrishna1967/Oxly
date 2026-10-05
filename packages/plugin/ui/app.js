// Phase 5 status UI. Pings same-origin health + manifest + openapi (dev serves all).
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

fetch("/plugin/openapi.json")
  .then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  })
  .then(function (j) {
    setText("openapi", j.openapi + " (" + j.info.title + ")");
  })
  .catch(function (e) {
    setText("openapi", "unreachable (" + e.message + ")");
  });

// Live MCP check: initialize handshake only (no tools/list POST from browser
// to keep the UI read-only and CSP-safe). Reports endpoint reachability.
fetch("/mcp", {
  method: "POST",
  headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
  body: JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "plugin-ui", version: "5" },
    },
  }),
})
  .then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    setText("mcp-live", "reachable (initialize ok)");
  })
  .catch(function (e) {
    setText("mcp-live", "unreachable (" + e.message + ")");
  });
