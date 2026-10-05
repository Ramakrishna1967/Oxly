// Copyright 2026 Oxly Contributors
// SPDX-License-Identifier: Apache-2.0

/**
 * TypeScript interfaces matching backend schemas
 */

export type SpanStatus = "OK" | "ERROR" | "UNSET";

export interface SpanEvent {
  name: string;
  timestamp: number;
  attributes: Record<string, string>;
}

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  start_time: number;
  end_time: number;
  duration_ms: number;
  status: SpanStatus;
  service_name: string;
  attributes: Record<string, string>;
  events: SpanEvent[];
  project_id?: string;
}

export interface Trace {
  trace_id: string;
  project_id: string;
  name?: string;
  start_time: number;
  end_time: number | null;
  duration_ms: number;
  status: SpanStatus;
  span_count: number;
}

export interface TraceDetail {
  trace_id: string;
  project_id: string;
  start_time: number;
  end_time: number | null;
  duration_ms: number | null;
  status: SpanStatus;
  spans: Span[];
}

export type SecurityAlertSeverity = "low" | "medium" | "high" | "critical";

export interface SecurityAlert {
  id: string;
  trace_id: string;
  span_id: string;
  project_id: string;
  severity: SecurityAlertSeverity;
  alert_type: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface User {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export interface ModelCost {
  model: string;
  total_cost: number;
  total_tokens?: number;
}

export interface AnalyticsData {
  timestamp: string;
  total_cost: number;
  total_tokens?: number;
  [key: string]: string | number | undefined; // Required for optional properties
}

export interface HealthServices {
  api: string;
  sqlite: string;
  queue: string;
  retention: string;
  [key: string]: string; // Allow indexing
}

export interface AnalyticsResponse {
  data: AnalyticsData[];
  total_cost: number;
  interval: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}
