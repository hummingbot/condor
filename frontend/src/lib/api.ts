import { getCsrfToken } from "./csrf";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string>),
  };
  // CSRF (§5.5): every unsafe method carries the per-process token.
  if (UNSAFE_METHODS.has(method)) {
    headers["X-Condor-Token"] = await getCsrfToken();
  }
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// ── Native executors (snapshot API, §6.3) ──

export interface SnapshotLiveOrder {
  venue_order_id: string;
  role: string;
  side: string;
  status: string;
}

export interface SnapshotExecutor {
  id: string;
  type: string;
  status: string;
  instrument: string;
  venue: string;
  agent_slug: string;
  run_id: string;
  origin: string;
  phase: string;
  exposure_quote: number;
  live_orders: SnapshotLiveOrder[];
}

export interface SnapshotTerminalExecutor {
  id: string;
  type: string;
  status: string;
  close_reason: string;
  run_id: string;
}

export interface ExecutorsSnapshot {
  account: Record<string, unknown> | null;
  filters: { agent_slug: string | null; run_id: string | null };
  open_executors: SnapshotExecutor[];
  open_count: number;
  live_order_count: number;
  attributed_exposure_quote: number;
  /** instrument -> net owned base amount (decimal string) */
  owned_inventory: Record<string, string>;
  recent_terminal: SnapshotTerminalExecutor[];
}

// ── Agents ──

export interface RunningInstance {
  agent_id: string;
  session_num: number;
  status: string;
  agent_key: string;
  tick_count: number;
  daily_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  volume: number;
  fees: number;
  open_count: number;
  closed_count: number;
  win_rate: number;
  server_name: string;
  total_amount_quote: number;
  trading_context: string;
  frequency_sec: number;
  execution_mode: "experiment" | "run_once" | "loop";
  risk_limits: Record<string, unknown>;
}

export interface AgentSummary {
  slug: string;
  name: string;
  description: string;
  consultable: boolean;
  when_to_consult: string;
  agent_key: string;
  denomination: string;
  default_config: Record<string, unknown>;
  default_trading_context: string;
  schedule: Record<string, unknown> | null;
  // Agent-level history rollups for summary cards.
  status: string;
  agent_id?: string;
  session_count: number;
  experiment_count: number;
  tick_count: number;
  daily_pnl: number;
  total_pnl: number;
  total_volume: number;
  open_positions: number;
  instances: RunningInstance[];
}

export interface AgentExecutorRow {
  id: string;
  type: string;
  connector: string;
  pair: string;
  side: string;
  status: string;
  close_type: string;
  pnl: number;
  volume: number;
  fees: number;
  entry_price: number;
  current_price: number;
  amount: number;
  timestamp: number;
  close_timestamp: number;
  controller_id: string;
  custom_info?: Record<string, unknown>;
  config?: Record<string, unknown>;
}

export interface AgentPerformance {
  // agent_id === run_id (executors tag controller_id == run_id)
  agent_id: string;
  run_id: string;
  session_num: number; // display_seq from the run stream
  kind: "session" | "experiment" | "scheduled";
  status: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  volume: number;
  fees: number;
  trade_count: number;
  win_rate: number;
  open_count: number;
  closed_count: number;
  executors: AgentExecutorRow[];
}

export interface AgentPerformanceResponse {
  slug: string;
  sessions: AgentPerformance[];
  totals: Record<string, number>;
}

// One run from the RunStore: an append-only JSONL event stream per run at
// agents/{slug}/runs/{run_id}.jsonl. run_id is an opaque ULID.
export type RunKind = "session" | "experiment" | "delegation" | "consult" | "scheduled";

export interface RunMeta {
  run_id: string;
  agent_slug: string;
  kind: RunKind | "";
  display_seq: number;
  started_at: number | null; // epoch seconds
  ended_at: number | null;
  status: string; // running | completed | stopped | error | interrupted | unknown
  reason: string;
  model?: string;
  source_spec_hash?: string;
  resolved_spec_hash?: string;
  event_count?: number;
  task?: string;
  scheduled_for?: string;
  // Live engine info overlaid while the run is in flight.
  live?: Record<string, unknown>;
}

// One event envelope from a run stream.
export interface RunEvent {
  v: number;
  seq: number;
  ts: number; // epoch seconds
  type: string;
  run_id: string;
  tick?: number;
  tool_call_id?: string;
  executor_id?: string;
  payload: Record<string, unknown>;
}

export type RunDetail = RunMeta & { events?: RunEvent[] };

// Agent = identity + brain (AGENT.md, tools, consult capability) that owns
// all operational history (the RunStore + learnings).
export interface AgentDetail {
  slug: string;
  name: string;
  description: string;
  agent_md: string;
  agent_key: string;
  tools: string[];
  when_to_consult: string;
  consultable: boolean;
  server_required: boolean;
  server_name: string;
  risk_limits: Record<string, unknown>;
  denomination: string;
  default_config: Record<string, unknown>;
  default_trading_context: string;
  schedule: Record<string, unknown> | null;
  learnings: string;
  status: string;
  agent_id: string;
  // RunStore metas, newest first (all kinds) — the one history list.
  runs: RunMeta[];
  instances: RunningInstance[];
}

// Delegation = a fire-and-forget background task handed to a detached Agent
// instance (DELEGATE mode). Ephemeral + in-process; status drives the UI.
export interface Delegation {
  task_id: string; // === run_id (RunStore stream id)
  run_id?: string;
  agent: string;
  server_name: string | null;
  task: string;
  status: "running" | "done" | "error" | "stopped";
  result: string;
  error: string;
  started_at?: string;
  ended_at?: string;
}

// ── Routines ──

export interface RoutineFieldInfo {
  type: string;
  default: unknown;
  description: string;
  widget?: "select";
  options_from?: string;
}

export interface RoutineInfo {
  name: string;
  description: string;
  is_continuous: boolean;
  category: string;
  source: string;
  fields: Record<string, RoutineFieldInfo>;
  /** Source-file modification time (epoch seconds), or null if unavailable. */
  last_modified: number | null;
  report_count: number;
}

// Synchronous worker output from POST /routines/run.
export interface RoutineRunResult {
  text: string;
  table_data?: Record<string, unknown>[] | null;
  table_columns?: string[] | null;
  chart_image_b64?: string | null;
  sections?: Record<string, unknown>[] | null;
}

export interface RoutineRunResponse {
  name: string;
  result: RoutineRunResult;
}

// Durable cron schedule (store/schedules.json).
export interface RoutineSchedule {
  schedule_id: string;
  routine: string;
  agent_slug: string;
  config: Record<string, unknown>;
  cron: string;
  tz: string;
  last_fired: string;
  disabled: boolean;
  disabled_reason?: string;
}

// ── Reports ──

export interface ReportSummary {
  id: string;
  title: string;
  filename: string;
  created_at: string;
  source_type: string;
  source_name: string;
  tags: string[];
}

export interface ReportsListResponse {
  reports: ReportSummary[];
  total: number;
}

export interface ReportGroup {
  source_name: string;
  source_type: string;
  latest_report: ReportSummary;
  total_count: number;
  all_tags: string[];
}

// ── Venues (account management, §6.2b) ──

export interface VenueAccount {
  address: string;
  name: string;
  is_default: boolean;
}

export interface VenueEntry {
  default_account: string | null;
  accounts: VenueAccount[];
}

export interface VenuesResponse {
  venues: Record<string, VenueEntry>;
}

// ── Chat ──

export interface ChatAgentOption {
  key: string;
  label: string;
}

export interface ChatOptionsResponse {
  agents: ChatAgentOption[];
  default_agent: string;
}

// ── API functions ──

export const api = {
  // ── Native executors (snapshot + lifecycle) ──

  getExecutorsSnapshot: (params?: { agent_slug?: string; agent_id?: string; venue_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.agent_slug) qs.set("agent_slug", params.agent_slug);
    if (params?.agent_id) qs.set("agent_id", params.agent_id);
    if (params?.venue_id) qs.set("venue_id", params.venue_id);
    const q = qs.toString();
    return apiFetch<ExecutorsSnapshot>(`/api/v1/executors/snapshot${q ? `?${q}` : ""}`);
  },

  stopExecutor: (executorId: string, keepPosition = true) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/executors/${encodeURIComponent(executorId)}/stop`,
      { method: "POST", body: JSON.stringify({ keep_position: keepPosition }) },
    ),

  // ── Agents (identity + brain) ──

  getAgents: () => apiFetch<AgentSummary[]>("/api/v1/agents"),

  getAgent: (slug: string) =>
    apiFetch<AgentDetail>(`/api/v1/agents/${encodeURIComponent(slug)}`),

  createAgent: (data: {
    name: string;
    description?: string;
    instructions?: string;
    agent_key?: string;
    tools?: string[];
    when_to_consult?: string;
  }) =>
    apiFetch<AgentSummary>("/api/v1/agents", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateAgentMd: (slug: string, content: string) =>
    apiFetch<{ updated: boolean }>(`/api/v1/agents/${encodeURIComponent(slug)}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),

  deleteAgent: (slug: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/agents/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }),

  consultAgent: (slug: string, data: { task: string; context?: string }) =>
    apiFetch<{ agent: string; answer: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/consult`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  // ── Delegations (fire-and-forget background agent tasks) ──

  getDelegations: () =>
    apiFetch<{ delegations: Delegation[] }>("/api/v1/agents/delegations"),

  stopDelegation: (taskId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/api/v1/agents/delegations/${encodeURIComponent(taskId)}/stop`,
      { method: "POST" },
    ),

  // ── Agent history: performance, sessions, lifecycle (all agent-level) ──

  getAgentPerformance: (slug: string) =>
    apiFetch<AgentPerformanceResponse>(
      `/api/v1/agents/${encodeURIComponent(slug)}/performance`,
    ),

  // ── Runs (the one history API — RunStore streams) ──

  getAgentRuns: (slug: string, kind?: RunKind, limit = 50) =>
    apiFetch<{ runs: RunMeta[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/runs?limit=${limit}${
        kind ? `&kind=${encodeURIComponent(kind)}` : ""
      }`,
    ),

  getRun: (runId: string, includeEvents = false) =>
    apiFetch<RunDetail>(
      `/api/v1/agents/runs/${encodeURIComponent(runId)}?events=${includeEvents}`,
    ),

  getRunExport: (runId: string) =>
    apiFetch<{ run_id: string; markdown: string }>(
      `/api/v1/agents/runs/${encodeURIComponent(runId)}/export`,
    ),

  getRunExecutors: (runId: string) =>
    apiFetch<{ executors: AgentExecutorRow[]; performance: AgentPerformance }>(
      `/api/v1/agents/runs/${encodeURIComponent(runId)}/executors`,
    ),

  startAgent: (
    slug: string,
    config: Record<string, unknown> = {},
    trading_context = "",
  ) =>
    apiFetch<{ started: boolean; agent_id: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/start`,
      { method: "POST", body: JSON.stringify({ config, trading_context }) },
    ),

  stopAgent: (slug: string, agentId?: string) =>
    apiFetch<{ stopped: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/stop${
        agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""
      }`,
      { method: "POST" },
    ),

  pauseAgent: (slug: string, agentId?: string) =>
    apiFetch<{ paused: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/pause${
        agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""
      }`,
      { method: "POST" },
    ),

  resumeAgent: (slug: string, agentId?: string) =>
    apiFetch<{ resumed: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/resume${
        agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""
      }`,
      { method: "POST" },
    ),

  getAgentLearnings: (slug: string) =>
    apiFetch<{ content: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/learnings`,
    ),

  updateAgentLearnings: (slug: string, content: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/learnings`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),

  // ── Reports ──

  getReports: (params?: { source_type?: string; tag?: string; search?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.source_type) qs.set("source_type", params.source_type);
    if (params?.tag) qs.set("tag", params.tag);
    if (params?.search) qs.set("search", params.search);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return apiFetch<ReportsListResponse>(`/api/v1/reports${q ? `?${q}` : ""}`);
  },

  getReportsGrouped: () => apiFetch<ReportGroup[]>("/api/v1/reports/latest-by-source"),

  deleteReport: (id: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/reports/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // ── Routines ──

  getRoutines: () => apiFetch<RoutineInfo[]>("/api/v1/routines"),

  // Synchronous: resolves with the worker output (hard 120s worker timeout;
  // 422 with detail on error). name may be "slug/name" for agent routines.
  runRoutine: (name: string, config: Record<string, unknown> = {}) =>
    apiFetch<RoutineRunResponse>(
      `/api/v1/routines/run`,
      { method: "POST", body: JSON.stringify({ routine_name: name, config }) },
    ),

  // Durable cron schedule (5-field cron expression; 422 if missing/invalid).
  scheduleRoutine: (
    name: string,
    config: Record<string, unknown> = {},
    cron: string,
    tz?: string,
  ) =>
    apiFetch<RoutineSchedule>(
      `/api/v1/routines/schedule`,
      { method: "POST", body: JSON.stringify({ routine_name: name, config, cron, ...(tz ? { tz } : {}) }) },
    ),

  getRoutineSchedules: () =>
    apiFetch<{ schedules: RoutineSchedule[] }>("/api/v1/routines/schedules"),

  removeRoutineSchedule: (scheduleId: string) =>
    apiFetch<{ removed: boolean }>(
      `/api/v1/routines/schedules/${encodeURIComponent(scheduleId)}`,
      { method: "DELETE" },
    ),

  getRoutineSource: (name: string) =>
    apiFetch<{ filename: string; source: string }>(
      `/api/v1/routines/${encodeURIComponent(name)}/source`,
    ),

  getRoutineReports: (name: string) =>
    apiFetch<ReportsListResponse>(
      `/api/v1/routines/${encodeURIComponent(name)}/reports`,
    ),

  getRoutineFieldOptions: (source: string) =>
    apiFetch<{ options: string[] }>(
      `/api/v1/routines/options/${encodeURIComponent(source)}`,
    ),

  // ── Agent Routines & Reports ──

  getAgentRoutines: (slug: string) =>
    apiFetch<RoutineInfo[]>(`/api/v1/agents/${encodeURIComponent(slug)}/routines`),

  getAgentReports: (slug: string) =>
    apiFetch<ReportsListResponse>(`/api/v1/agents/${encodeURIComponent(slug)}/reports`),

  // ── Venues (account management) ──

  getVenues: () => apiFetch<VenuesResponse>("/api/v1/venues"),

  onboardVenueAccount: (venueId: string, data: { credentials: Record<string, unknown>; name?: string }) =>
    apiFetch<{ venue_id: string; account: VenueAccount }>(
      `/api/v1/venues/${encodeURIComponent(venueId)}/accounts`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  editVenueAccount: (
    venueId: string,
    address: string,
    data: { credentials?: Record<string, unknown>; name?: string },
  ) =>
    apiFetch<{ venue_id: string; account: VenueAccount }>(
      `/api/v1/venues/${encodeURIComponent(venueId)}/accounts/${encodeURIComponent(address)}`,
      { method: "PUT", body: JSON.stringify(data) },
    ),

  removeVenueAccount: (venueId: string, address: string) =>
    apiFetch<{ venue_id: string; removed: string; warning: string }>(
      `/api/v1/venues/${encodeURIComponent(venueId)}/accounts/${encodeURIComponent(address)}`,
      { method: "DELETE" },
    ),

  setDefaultVenueAccount: (venueId: string, account: string) =>
    apiFetch<{ venue_id: string; default_account: string }>(
      `/api/v1/venues/${encodeURIComponent(venueId)}/default`,
      { method: "POST", body: JSON.stringify({ account }) },
    ),

  // ── Chat ──

  getChatOptions: () =>
    apiFetch<ChatOptionsResponse>("/api/v1/chat/options"),
};
