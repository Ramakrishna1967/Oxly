// Copyright 2026 Oxly Contributors
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from "react";
import apiClient from "../lib/api";
import { useProject } from "../components/ProjectSwitcher";
import type { Trace, HealthServices } from "../lib/types";

//  Shared style tokens 
const S = {
  card: { background: "#0d0d0d", border: "1px solid #1e1e1e", borderRadius: 8 } as React.CSSProperties,
  label: { fontSize: 11, color: "#555", letterSpacing: "0.06em", textTransform: "uppercase" as const, fontWeight: 500 },
  h2: { fontSize: 15, fontWeight: 600, color: "#fff", marginBottom: 4 } as React.CSSProperties,
  muted: { fontSize: 12, color: "#555" } as React.CSSProperties,
};

//  Sparkline 
const Sparkline: React.FC<{ path: string }> = ({ path }) => (
  <svg width="100%" height="30" viewBox="0 0 120 30" preserveAspectRatio="none">
    <path d={path} fill="none" stroke="rgba(255,255,255,0.5)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const SPARKLINES = {
  rising: "M0 28 C20 22 40 18 60 12 C80 6 100 4 120 2",
  falling: "M0 4 C20 8 40 14 60 18 C80 22 100 26 120 28",
  flat: "M0 15 C20 12 40 18 60 15 C80 12 100 18 120 15",
  wavy: "M0 18 C15 8 30 28 45 18 C60 8 75 28 90 18 C105 8 115 14 120 15",
};

//  Stat Card 
const StatCard: React.FC<{
  label: string; value: string; change: string; up: boolean; spark: string;
}> = ({ label, value, change, up, spark }) => (
  <div style={{ ...S.card, padding: "20px 18px 14px", display: "flex", flexDirection: "column", gap: 4 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
      <span style={S.label}>{label}</span>
      <span style={{ fontSize: 11, color: up ? "#22c55e" : "#ef4444", display: "flex", alignItems: "center", gap: 2 }}>
        {up ? "" : ""} {change}
      </span>
    </div>
    <div style={{ fontSize: 32, fontWeight: 700, color: "#fff", lineHeight: 1.2 }}>{value}</div>
    <div style={{ marginTop: 8 }}><Sparkline path={spark} /></div>
  </div>
);

//  Bar Chart for latency 
const LatencyBars: React.FC<{ data: number[] }> = ({ data }) => {
  const max = Math.max(...data, 100);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 80 }}>
      {data.map((h, i) => {
          const height = (h / max) * 100;
          return <div key={i} style={{ flex: 1, height: `${height}%`, background: h > (max * 0.7) ? "#ef4444" : "#2a2a2a", borderRadius: "2px 2px 0 0", minWidth: 0 }} />;
      })}
    </div>
  );
};

//  Dashboard Page 
const Dashboard: React.FC = () => {
  const { currentProject } = useProject();
  const [traces, setTraces] = useState<Trace[]>([]);
  const [stats, setStats] = useState({
    total: 0,
    avgLatency: 0,
    errorRate: 0,
    totalCost: 0
  });

  useEffect(() => {
    if (!currentProject) return;

    const fetchData = async () => {
      try {
        const response = await apiClient.get("/traces", {
          params: { project_id: currentProject.id, page_size: 15 }
        });
        const items: Trace[] = response.data.items || [];
        setTraces(items);

        // Simple calc for stats
        const total = response.data.total || items.length;
        const avgLat = items.length > 0 ? items.reduce((acc: number, t: Trace) => acc + (t.duration_ms || 0), 0) / items.length : 0;
        const errors = items.filter((t: Trace) => t.status === "ERROR").length;
        const errRate = items.length > 0 ? (errors / items.length) * 100 : 0;

        setStats({
          total,
          avgLatency: avgLat, // Fix: avgLat is already in ms, then UI converts to s
          errorRate: errRate,
          totalCost: 0.00 // Placeholder for cost engine integration
        });
      } catch (err) {
        console.error("Dashboard data fetch failed:", err);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, [currentProject]);

  // System Health state (keys must match GET /health services)
  const [health, setHealth] = useState<HealthServices>({
    api: "pending",
    sqlite: "pending",
    queue: "pending",
    retention: "pending"
  });

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const res = await apiClient.get("/health");
        setHealth(res.data.services);
      } catch (err) {
          console.error("Health check failed:", err);
        setHealth({
           api: "down",
           sqlite: "down",
           queue: "down",
           retention: "down"
        });
      }
    };
    fetchHealth();
    const hInterval = setInterval(fetchHealth, 15000);
    return () => clearInterval(hInterval);
  }, []);

  return (
    <div style={{ padding: "28px 28px", maxWidth: 1100 }}>
      {/* KPI row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <StatCard label="Total Traces" value={stats.total.toLocaleString()} change="N/A" up={true} spark={SPARKLINES.rising} />
        <StatCard label="Avg Latency" value={`${(stats.avgLatency / 1000).toFixed(2)}s`} change="N/A" up={true} spark={SPARKLINES.wavy} />
        <StatCard label="Error Rate" value={`${stats.errorRate.toFixed(1)}%`} change="N/A" up={false} spark={SPARKLINES.falling} />
        <StatCard label="Total Cost" value={`$${stats.totalCost.toFixed(2)}`} change="N/A" up={false} spark={SPARKLINES.flat} />
      </div>

      {/* Main content row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 12 }}>
        {/* Live Traces */}
        <div style={{ ...S.card }}>
          <div style={{ padding: "14px 16px", borderBottom: "1px solid #1a1a1a", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 8px #22c55e" }} />
              <span style={{ fontSize: 13, fontWeight: 600, color: "#fff" }}>Live Trace Feed</span>
            </div>
            <span style={{ fontSize: 11, color: "#555" }}>{currentProject?.name || "No Project"}</span>
          </div>
          <div style={{ minHeight: 400 }}>
            {traces.length === 0 ? (
              <div style={{ padding: 40, textAlign: "center", color: "#444", fontSize: 13 }}>No traces found for this project. Run a simulation to see data.</div>
            ) : (
              traces.map((item, i) => (
                <div key={item.trace_id} style={{
                  display: "flex", alignItems: "center", padding: "12px 16px",
                  borderBottom: i < traces.length - 1 ? "1px solid #111" : "none",
                  gap: 12,
                }}>
                  <div style={{
                    width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                    background: item.status === "OK" ? "#22c55e" : item.status === "ERROR" ? "#ef4444" : "#f59e0b",
                  }} />
                  <span style={{ fontSize: 12, fontFamily: "monospace", color: "#777", width: 85, flexShrink: 0 }}>{item.trace_id.slice(0, 8)}</span>
                  <span style={{ fontSize: 13, color: "#ccc", flex: 1 }}>{item.name || "Agent Run"}</span>
                  <span style={{ fontSize: 12, color: "#555", fontFamily: "monospace" }}>{(item.duration_ms / 1000).toFixed(2)}s</span>
                  <span style={{ fontSize: 11, color: "#444", fontFamily: "monospace" }}>
                    {new Date(item.start_time / 1e6).toLocaleTimeString([], { hour12: false })}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Right sidebar panel */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Latency chart */}
          <div style={{ ...S.card, padding: 16 }}>
            <div style={S.h2}>Request Latency</div>
            <div style={{ ...S.muted, marginBottom: 12 }}>Last {traces.length} traces</div>
            <LatencyBars data={traces.map(t => t.duration_ms || 0)} />
            <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 6 }}>
              <span style={{ fontSize: 10, color: "#444" }}>oldest</span>
              <span style={{ fontSize: 10, color: "#444" }}>latest</span>
            </div>
          </div>

          {/* System status */}
          <div style={{ ...S.card, padding: 16 }}>
            <div style={S.h2}>System Status</div>
            {[
              { id: "api", label: "API Server" },
              { id: "sqlite", label: "SQLite DB" },
              { id: "queue", label: "Ingest Queue" },
              { id: "retention", label: "Retention Sweep" },
            ].map(item => {
              const status = health[item.id] || "operational";
              const isOk = status === "operational";
              const color = isOk ? "#22c55e" : "#ef4444";
              return (
                <div key={item.label} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBlock: 8, borderBottom: "1px solid #111" }}>
                  <span style={{ fontSize: 12, color: "#888" }}>{item.label}</span>
                  <span style={{ fontSize: 11, color, display: "flex", alignItems: "center", gap: 4, textTransform: "capitalize" }}>
                    <div style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />
                    {status}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Quick start snippet */}
          <div style={{ ...S.card, padding: 16 }}>
            <div style={S.h2}>Quick Start</div>
            <pre style={{ fontSize: 11, color: "#888", fontFamily: "monospace", lineHeight: 1.7, margin: 0, whiteSpace: "pre-wrap" }}>
              <span style={{ color: "#a78bfa" }}>from</span> oxly <span style={{ color: "#a78bfa" }}>import</span> observe{"\n\n"}
              <span style={{ color: "#555" }}># Wrap any function</span>{"\n"}
              @observe{"\n"}
              <span style={{ color: "#a78bfa" }}>def</span> <span style={{ color: "#93c5fd" }}>my_agent</span>(q):{"\n"}
              {"  "}<span style={{ color: "#a78bfa" }}>return</span> llm.chat(q)
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
