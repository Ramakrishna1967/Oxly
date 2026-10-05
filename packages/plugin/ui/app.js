// Phase 0 placeholder UI. Pings the API health endpoint (same-origin in dev).
fetch("/api/v1/health")
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
  .then((j) => {
    document.getElementById("health").textContent = "ok: " + JSON.stringify(j).slice(0, 120);
  })
  .catch((e) => {
    document.getElementById("health").textContent = "unreachable (" + e.message + ")";
  });
