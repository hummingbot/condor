import { authFetch, authHeaders } from "./auth-token";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeaders(),
    ...(init?.headers as Record<string, string>),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// ── Types ──

export interface ServerInfo {
  name: string;
  host: string;
  port: number;
  online: boolean;
  permission: string;
}

export interface BalanceItem {
  token: string;
  total: number;
  available: number;
  usd_value: number;
}

export interface ConnectorBalance {
  connector: string;
  balances: BalanceItem[];
  total_usd: number;
  note?: string | null;
}

export interface PortfolioResponse {
  server: string;
  connectors: ConnectorBalance[];
  total_usd: number;
}

export interface PortfolioHistoryPoint {
  timestamp: number;
  total_usd: number;
  tokens?: Record<string, number>;
}

export interface PortfolioHistoryResponse {
  server: string;
  points: PortfolioHistoryPoint[];
  interval: string;
  top_tokens?: string[];
}

export interface BotInfo {
  id: string;
  name: string;
  status: string;
  connector: string;
  trading_pair: string;
  pnl: number;
  uptime: number;
  controller_type: string;
}

export interface BotDetail {
  bot: BotInfo;
  config: Record<string, unknown>;
  performance: Record<string, unknown>;
}

export interface ControllerInfo {
  controller_name: string;
  controller_id: string;
  bot_name: string;
  status: string;
  connector: string;
  trading_pair: string;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  global_pnl_pct: number;
  volume_traded: number;
  close_type_counts: Record<string, number>;
  positions_summary: Record<string, unknown>[];
  deployed_at: string | null;
  config: Record<string, unknown>;
}

export interface BotLogEntry {
  timestamp?: number;
  msg?: string;
  log_category?: string;
  [key: string]: unknown;
}

export interface BotSummary {
  bot_name: string;
  status: string;
  num_controllers: number;
  error_count: number;
  deployed_at: string | null;
  error_logs: BotLogEntry[];
  general_logs: BotLogEntry[];
}

export interface BotsPageResponse {
  controllers: ControllerInfo[];
  bots: BotSummary[];
  total_pnl: number;
  total_volume: number;
  server_online?: boolean;
  error_hint?: string;
}

export interface BotRunInfo {
  bot_name: string;
  bot_run_id: number | null;
  account_name: string;
  strategy_type: string;
  strategy_name: string;
  run_status: string;
  deployment_status: string;
  created_at: string | null;
  stopped_at: string | null;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  volume_traded: number;
  num_controllers: number;
}

export interface BotRunsResponse {
  runs: BotRunInfo[];
  total: number;
}

export interface ControllerPerformanceSnapshot {
  timestamp: string;
  bot_name: string;
  controller_id: string;
  controller_name: string;
  connector: string;
  trading_pair: string;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  global_pnl_pct: number;
  volume_traded: number;
  close_type_counts: Record<string, number>;
  positions_summary: Record<string, unknown>[];
  custom_info: Record<string, unknown>;
}

export interface ControllerPerformanceHistoryResponse {
  snapshots: ControllerPerformanceSnapshot[];
  next_cursor: string | null;
  interval: string;
  server_online?: boolean;
  error_hint?: string;
}

export interface ExecutorInfo {
  id: string;
  type: string;
  connector: string;
  trading_pair: string;
  side: string;
  status: string;
  close_type: string;
  pnl: number;
  volume: number;
  timestamp: number;
  controller_id: string;
  cum_fees_quote: number;
  net_pnl_pct: number;
  entry_price: number;
  current_price: number;
  close_timestamp: number;
  custom_info: Record<string, unknown>;
  config: Record<string, unknown>;
}

/** Executor totals for a period, aggregated server-side over the full history.
 *  `pnl` and `volume` are USD-denominated; `converted` is false when some quote
 *  asset had no path to USD and its rows were counted in their own quote. */
export interface ExecutorPeriodSummary {
  period: string;
  pnl: number;
  volume: number;
  count: number;
  converted: boolean;
}

export interface PositionHeld {
  connector_name: string;
  trading_pair: string;
  position_side?: string;
  side?: string;
  net_amount_base?: number;
  amount?: number;
  buy_breakeven_price?: number;
  entry_price?: number;
  current_price?: number;
  unrealized_pnl_quote?: number;
  unrealized_pnl?: number;
  leverage?: number;
  controller_id?: string;
}

export interface PositionsResponse {
  positions: PositionHeld[];
  summary: Record<string, unknown>;
}

export interface ConsolidatedPosition {
  connector_name: string;
  trading_pair: string;
  position_side: string;
  amount: number;
  entry_price: number;
  notional_value: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  cum_fees: number;
  executor_count: number;
  leverage: number;
  controller_id: string;
  source: "executor" | "bot";
  source_name: string;
}

export interface ConsolidatedPositionsResponse {
  executor_positions: ConsolidatedPosition[];
  bot_positions: ConsolidatedPosition[];
}

export interface CandleData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MarketPrice {
  connector: string;
  trading_pair: string;
  mid_price: number;
  best_bid: number;
  best_ask: number;
}

export interface OrderBookLevel {
  price: number;
  amount: number;
}

export interface OrderBookResponse {
  connector: string;
  trading_pair: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

export interface TradingRule {
  trading_pair: string;
  min_order_size: number;
  min_notional_size: number;
  min_price_increment: number;
  min_base_amount_increment: number;
}

export interface TradingRulesResponse {
  connector: string;
  rules: TradingRule[];
}

export interface Ticker {
  trading_pair: string;
  price: number;
  base_volume: number;
  quote_volume: number;
  /** 24h volume in USD; null when the quote asset couldn't be priced. */
  usd_volume: number | null;
}

export interface TickersResponse {
  connector: string;
  tickers: Ticker[];
  updated_at: number | null;
}

// ── Deploy Bot ──

export interface ControllerConfigSummary {
  id: string;
  controller_name: string;
  controller_type: string;
  connector_name: string;
  trading_pair: string;
}

export interface AvailableControllersResponse {
  configs: ControllerConfigSummary[];
  controller_types: Record<string, string[]>;
}

export interface ControllerConfigDetail {
  id: string;
  controller_name: string;
  controller_type: string;
  config: Record<string, unknown>;
}

export interface ControllerSourceResponse {
  controller_name: string;
  controller_type: string;
  source: string;
}

export interface DeployBotRequest {
  bot_name: string;
  controllers_config: string[];
  account_name?: string;
  image?: string;
  max_global_drawdown_quote?: number | null;
  max_controller_drawdown_quote?: number | null;
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
  /** null = no closed executors to derive it from (bot-mode sessions), not 0%. */
  win_rate: number | null;
  server_name: string;
  total_amount_quote: number;
  trading_context: string;
  frequency_sec: number;
  execution_mode: "dry_run" | "run_once" | "loop";
  risk_limits: Record<string, unknown>;
}

export interface StrategySummary {
  slug: string;
  name: string;
  description: string;
  status: string;
  agent_id: string;
  session_count: number;
  experiment_count: number;
  tick_count: number;
  daily_pnl: number;
  total_pnl: number;
  total_volume: number;
  open_positions: number;
  instances: RunningInstance[];
}

/**
 * The coordinator's slug — the Agent you get by binding none.
 *
 * Condor lives in the same registry as every specialist (FEAT-033), so it
 * comes back from `/agents` like the rest. But a chat is *with* Condor
 * precisely when `agent_slug` is empty, so any list of specialists to bind has
 * to lift it out or offer the same identity twice.
 */
export const CHAT_SLUG = "condor";

export interface AgentSummary {
  slug: string;
  name: string;
  description: string;
  /** Routing hint only — every Agent is consultable, delegable and loopable. */
  when_to_consult: string;
  agent_key: string;
  strategy_count: number;
  strategies: StrategySummary[];
  // Rolled-up aggregates across the agent's strategies (FEAT-004) for summary cards.
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
  agent_id: string;
  session_num: number;
  kind: "session" | "experiment";
  status: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  volume: number;
  fees: number;
  trade_count: number;
  /** null = no closed executors to derive it from (bot-mode sessions), not 0%. */
  win_rate: number | null;
  open_count: number;
  closed_count: number;
  executors: AgentExecutorRow[];
  // ── Bot-mode attribution ──
  // A session trading through bots has no rows in the agent_id-keyed executor
  // table: its executors live inside the bot instance's own database, and the
  // only ones Condor can reconstruct are the positions open right now. These say
  // what the session actually operated when `executors` is empty.
  /** Instance names alive now, one per owned base. */
  bot_names?: string[];
  /** Every instance ever deployed under an owned base, oldest first — a name in
   *  here but not in `bot_names` is one this session already stopped. */
  bot_instances?: string[];
  /** Owned bases with no live and no archived instance: their PnL is unknown,
   *  not zero, so the totals are a floor. */
  unresolved_bases?: string[];
  controllers?: AgentControllerRow[];
  /** Raw `CloseType.X -> n` across the operated bots. `trade_count` counts only
   *  round-trip closes and reads a directional controller's risk stop as churn,
   *  so this is what explains "0 trades" on non-zero volume. */
  close_type_counts?: Record<string, number>;
  /** False when `fees` is a floor: the backend reports no cumulative fee column,
   *  so 0 means unknown rather than free. */
  fees_known?: boolean;
}

/** One controller of one bot instance a session operated. */
export interface AgentControllerRow {
  /** The deploy this controller ran under. */
  bot_name: string;
  controller_id: string;
  controller_name: string;
  connector: string;
  trading_pair: string;
  /** From the performance snapshot, which reports "running" even for archived
   *  instances — never render this as live truth. Derive liveness from whether
   *  `bot_name` appears in `AgentPerformance.bot_names`. */
  status: string;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  volume_traded: number;
  cum_fees_quote: number;
  closed_trades: number;
  close_type_counts: Record<string, number>;
}

export interface SessionCanvas {
  sections: Record<string, string>;
  section_titles: Record<string, string>;
  section_order: string[];
  last_revised_tick: number;
  revisions: { tick: number; section: string; text: string }[];
}

export interface AgentPerformanceResponse {
  slug: string;
  sessions: AgentPerformance[];
  totals: Record<string, number>;
}

export interface SessionInfo {
  number: number;
  snapshot_count: number;
  created_at: string;
}

export interface ExperimentInfo {
  number: number;
  execution_mode: string;
  agent_key: string;
  snapshot_count: number;
  created_at: string;
  error?: boolean;
}

// Agent = identity + brain (AGENT.md, tools, consult capability) that owns strategies.
export interface AgentDetail {
  slug: string;
  name: string;
  description: string;
  agent_md: string;
  agent_key: string;
  tools: string[];
  when_to_consult: string;
  server_required: boolean;
  server_name: string;
  strategies: StrategySummary[];
}

// How a delegation ended. `running`…`stopped` come from the live registry;
// `interrupted` is a task whose process died mid-flight, and `unknown` a
// transcript too old to say — both only ever arrive from history.
export type DelegationStatus =
  | "running"
  | "done"
  | "error"
  | "stopped"
  | "interrupted"
  | "unknown";

// Delegation = a fire-and-forget background task handed to a detached Agent
// instance (DELEGATE mode). The registry is in-process, but the record outlives
// it on disk — so this shape also comes back from history (FEAT-035), where
// `ended_at`/`tool_count` are known and the live registry has nothing to say.
export interface Delegation {
  task_id: string;
  agent: string;
  user_id: number;
  chat_id: number;
  server_name: string | null;
  task: string;
  status: DelegationStatus;
  result: string;
  error: string;
  /** The conversation that started this task; "" when there was none. */
  conversation_id: string;
  /** Wall-clock start (epoch seconds) — drives the elapsed time. */
  started_at: number;
  ended_at?: number;
  tool_count?: number;
}

/** A history row: the record minus the bodies, which the sheet fetches on open. */
export type DelegationSummary = Omit<Delegation, "result" | "error">;

// One entry of a delegation's session transcript. Mirrors the three shapes the
// runner's event sink folds into `DelegateTask.events`; tool entries are patched
// in place server-side, so `id` is stable across a status flip.
export type DelegationEvent =
  | { type: "thought"; text: string }
  | { type: "text"; text: string }
  | {
      type: "tool";
      id: string;
      name: string;
      status: string;
      kind: string;
      input: Record<string, unknown> | null;
      /** Clipped at 2000 chars, the same boundary the on-disk transcript uses. */
      output: string | null;
    };

// Strategy = a playbook that loops under an Agent. Holds the operational
// history: sessions, experiments, live instances, config and learnings.
export interface StrategyDetail {
  slug: string;
  agent_slug: string;
  name: string;
  description: string;
  strategy_md: string;
  config: Record<string, unknown>;
  default_trading_context: string;
  learnings: string;
  status: string;
  agent_id: string;
  sessions: SessionInfo[];
  experiments: ExperimentInfo[];
  instances: RunningInstance[];
}

export interface SnapshotSummary {
  tick: number;
  timestamp: string;
  file: string;
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

export interface RoutineInstance {
  instance_id: string;
  routine_name: string;
  config: Record<string, unknown>;
  status: string;
  source: string;
  /**
   * The conversation that asked for this run, "" for the scheduler, the
   * dashboard and Telegram. Set since ARCH-089, and what scopes a run to a
   * chat — the routine's own name cannot, since an agent runs shared-library
   * routines under their bare name.
   */
  conversation_id?: string;
  schedule?: Record<string, unknown>;
  created_at: number;
  last_run_at: number | null;
  last_result: string | null;
  last_duration: number | null;
  /** The report the last run rendered, if it rendered one. */
  report_id?: string | null;
  run_count: number;
  has_result?: boolean;
  result_text?: string;
  has_chart?: boolean;
  table_data?: Record<string, unknown>[] | null;
  table_columns?: string[] | null;
  sections?: Record<string, unknown>[] | null;
  error?: string | null;
}

export interface RoutineHooks {
  telegram: { enabled: boolean; chat_ids: string[] };
  trigger: "success" | "always" | "failure";
}

// ── Archived Bots ──

export interface ArchivedBotSummary {
  bot_name: string;
  db_path: string;
  total_trades: number;
  total_orders: number;
  trading_pairs: string[];
  exchanges: string[];
  start_time: number | null;
  end_time: number | null;
}

export interface PnlPoint {
  timestamp: number;
  pnl: number;
}

export interface ArchivedBotPerformance {
  bot_name: string;
  db_path: string;
  total_pnl: number;
  total_fees: number;
  total_volume: number;
  trade_count: number;
  buy_count: number;
  sell_count: number;
  pnl_by_pair: Record<string, number>;
  cumulative_pnl: PnlPoint[];
  trading_pairs: string[];
  exchanges: string[];
  executors: ExecutorInfo[];
  primary_connector: string;
  primary_trading_pair: string;
  executor_count: number;
}

export interface PaginatedExecutors {
  executors: ExecutorInfo[];
  total: number;
  offset: number;
  limit: number;
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
  /** Producing assistant — "" on reports written before attribution existed. */
  agent?: string;
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

// ── Settings ──

export interface GatewayStatus {
  running: boolean;
  info: Record<string, unknown> | null;
  image?: string;
  created_at?: string;
  container_status?: string;
}

export interface CredentialInfo {
  connector_name: string;
  connector_type: string;
}

export interface ConnectorInfo {
  name: string;
  type: string;
  [key: string]: unknown;
}

export interface ConnectorFieldInfo {
  key: string;
  type: string;
  required: boolean;
  default?: unknown;
  description?: string;
}

// ── Voice Settings ──

export interface VoicePrefs {
  whisper_model: string;
  language: string | null;
  auto_send: boolean;
}

export interface VoiceSettingsResponse {
  voice: VoicePrefs;
  available_models: Record<string, string>;
  available_languages: Record<string, string>;
}

// ── Chat ──

export interface ChatAgentOption {
  key: string;
  label: string;
  /** True for sentinels that open a model list ("openrouter:", "custom:")
   *  rather than naming a startable model. Sent explicitly because the key's
   *  shape doesn't tell you — "ollama:" also ends in a colon but IS startable. */
  picker?: boolean;
}

/** A saved OpenAI-compatible endpoint (Venice AI, Together, local vLLM, ...). */
export interface CustomProvider {
  name: string;
  base_url: string;
  has_key: boolean;
}

export interface ChatOptionsResponse {
  agents: ChatAgentOption[];
  custom_providers: CustomProvider[];
  default_agent: string;
}

export interface OpenRouterModelOption {
  slug: string;
  name: string;
  context_length: number;
  prompt_price: number;
  completion_price: number;
}

/** A domain Agent a session can be bound to. Every Agent is chattable. */
export interface AgentBindingOption {
  slug: string;
  name: string;
  description: string;
  when_to_consult: string;
  /** The model it answers on unless the user overrides one — its own default. */
  agent_key: string;
}

export interface SessionOptionsResponse extends ChatOptionsResponse {
  servers: string[];
  agent_bindings: AgentBindingOption[];
}

/** Mirrors condor/runtime/models.py SessionInfo. */
export interface SessionInfo {
  key: string;
  agent_key: string;
  user_id: number | null;
  /** Originating frontend: "tg" | "web" | "mcp". */
  surface: string;
  slot: string;
  server_name: string | null;
  /** The bound Agent pinned this server; the chat cannot change it. */
  server_pinned: boolean;
  is_busy: boolean;
  alive: boolean;
  created_at: string;
  last_prompt_at: string | null;
  /** Bound domain Agent, or "" for the plain assistant. */
  agent_slug: string;
  /** Display name of whoever is answering. */
  label: string;
  /** Durable conversation behind this session. Outlives it. */
  conversation_id: string;
}

export interface CreateSessionRequest {
  key: string;
  agent_key: string;
  server_name?: string | null;
  agent_slug?: string;
  lazy_context?: boolean;
  /** Empty mints a new conversation; an id resumes that transcript. */
  conversation_id?: string;
}

// ── Conversations ──
//
// A session is the live subprocess; a conversation is what was said. The
// second outlives the first, so this — not localStorage — is the source of
// truth for a transcript.

export interface ConversationMeta {
  id: string;
  user_id: number;
  /** Where it was born: "tg" | "web" | "mcp". */
  surface: string;
  title: string;
  agent_key: string;
  agent_slug: string;
  server_name: string | null;
  created_at: string;
  updated_at: string;
  turn_count: number;
  last_snippet: string;
}

export interface ConversationTurn {
  /** "user" | "assistant" | "system". */
  role: string;
  text: string;
  thought: string;
  tool_calls: { id?: string; title?: string; status?: string }[];
  /** System entries only: "switch" | "error". */
  kind: string;
  ts: number;
}

export interface ConversationDetail {
  meta: ConversationMeta;
  turns: ConversationTurn[];
}

export interface SwitchSessionRequest {
  agent_slug?: string;
  agent_key?: string;
  /** Move the conversation to this server. Also a respawn — the server is
   *  baked into the MCP subprocess at spawn. */
  server_name?: string;
  /** Short recap carried into the new session — the subprocess is replaced. */
  handoff?: string;
}

/** Agent key for a model served by a saved custom endpoint. Mirrors
 *  build_custom_agent_key() in condor/preferences.py. */
export function customAgentKey(provider: string, model: string): string {
  return `custom@${provider}:${model}`;
}

/** Inverse of customAgentKey; returns null for non-custom keys. */
export function parseCustomAgentKey(
  agentKey: string,
): { provider: string; model: string } | null {
  const colon = agentKey.indexOf(":");
  if (colon < 0) return null;
  const prefix = agentKey.slice(0, colon);
  if (!prefix.startsWith("custom@")) return null;
  return { provider: prefix.slice("custom@".length), model: agentKey.slice(colon + 1) };
}

// ── Backtesting ──

export interface BacktestTask {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed";
  config?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
  created_at?: number;
  saved?: boolean;
}

// ── API functions ──

export const api = {
  getServers: () => apiFetch<ServerInfo[]>("/api/v1/servers"),

  getServerStatus: (name: string) =>
    apiFetch<{ online: boolean; error?: string }>(
      `/api/v1/servers/${encodeURIComponent(name)}/status`,
    ),

  getPortfolio: (server: string, refresh = false) =>
    apiFetch<PortfolioResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/portfolio${refresh ? "?refresh=true" : ""}`,
    ),

  getPortfolioHistory: (server: string, range = "1D", breakdown = false) =>
    apiFetch<PortfolioHistoryResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/portfolio/history?range=${encodeURIComponent(range)}${breakdown ? "&breakdown=true" : ""}`,
    ),

  getBots: (server: string) =>
    apiFetch<BotsPageResponse>(`/api/v1/servers/${encodeURIComponent(server)}/bots`),

  getBot: (server: string, botId: string) =>
    apiFetch<BotDetail>(`/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botId)}`),

  getAvailableConfigs: (server: string) =>
    apiFetch<AvailableControllersResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs`,
    ),

  getConfigDetail: (server: string, configId: string) =>
    apiFetch<ControllerConfigDetail>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`,
    ),

  updateConfig: (server: string, configId: string, data: Record<string, unknown>) =>
    apiFetch<{ updated: boolean }>(`/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  updateBotControllerConfig: (server: string, botName: string, configId: string, data: Record<string, unknown>) =>
    apiFetch<{ updated: boolean }>(`/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/controllers/${encodeURIComponent(configId)}/config`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  updateConfigYaml: (server: string, configId: string, yamlContent: string) =>
    apiFetch<{ updated: boolean }>(`/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`, {
      method: "PUT",
      body: JSON.stringify({ yaml_content: yamlContent }),
    }),

  getControllerConfigTemplate: (server: string, controllerType: string, controllerName: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}/template`,
    ),

  createControllerConfig: (server: string, configId: string, data: Record<string, unknown>) =>
    apiFetch<{ created: boolean; config_id: string }>(`/api/v1/servers/${encodeURIComponent(server)}/controllers/configs`, {
      method: "POST",
      body: JSON.stringify({ ...data, id: configId }),
    }),

  deleteControllerConfig: (server: string, configId: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`, {
      method: "DELETE",
    }),

  deleteController: (server: string, controllerType: string, controllerName: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}`,
      { method: "DELETE" },
    ),

  getControllerSource: (server: string, controllerType: string, controllerName: string) =>
    apiFetch<ControllerSourceResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}/source`,
    ),

  updateControllerSource: (server: string, controllerType: string, controllerName: string, source: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}/source`,
      { method: "PUT", body: JSON.stringify({ source }) },
    ),

  deployBot: (server: string, data: DeployBotRequest) =>
    apiFetch<Record<string, unknown>>(`/api/v1/servers/${encodeURIComponent(server)}/bots/deploy`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  stopBot: (server: string, botName: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/stop`,
      { method: "POST" },
    ),

  stopControllers: (server: string, botName: string, controllerNames: string[]) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/controllers/stop`,
      { method: "POST", body: JSON.stringify({ controller_names: controllerNames }) },
    ),

  startControllers: (server: string, botName: string, controllerNames: string[]) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/controllers/start`,
      { method: "POST", body: JSON.stringify({ controller_names: controllerNames }) },
    ),

  getControllerPerformanceHistory: (
    server: string,
    params: {
      bot_name?: string;
      controller_id?: string;
      start_time?: string;
      end_time?: string;
      interval?: string;
      limit?: number;
      cursor?: string;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.bot_name) qs.set("bot_name", params.bot_name);
    if (params.controller_id) qs.set("controller_id", params.controller_id);
    if (params.start_time) qs.set("start_time", params.start_time);
    if (params.end_time) qs.set("end_time", params.end_time);
    if (params.interval) qs.set("interval", params.interval);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.cursor) qs.set("cursor", params.cursor);
    const q = qs.toString();
    return apiFetch<ControllerPerformanceHistoryResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/controller-performance/history${q ? `?${q}` : ""}`,
    );
  },

  getBotRuns: (
    server: string,
    params: {
      bot_name?: string;
      run_status?: string;
      deployment_status?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.bot_name) qs.set("bot_name", params.bot_name);
    if (params.run_status) qs.set("run_status", params.run_status);
    if (params.deployment_status) qs.set("deployment_status", params.deployment_status);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return apiFetch<BotRunsResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/bot-runs${q ? `?${q}` : ""}`,
    );
  },

  deleteBotRun: (server: string, botRunId: number) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/servers/${encodeURIComponent(server)}/bot-runs/${botRunId}`, {
      method: "DELETE",
    }),

  getExecutors: (
    server: string,
    params?: { executor_type?: string; trading_pair?: string; status?: string; controller_id?: string; limit?: number },
  ) => {
    const qs = new URLSearchParams();
    if (params?.executor_type) qs.set("executor_type", params.executor_type);
    if (params?.trading_pair) qs.set("trading_pair", params.trading_pair);
    if (params?.status) qs.set("status", params.status);
    if (params?.controller_id) qs.set("controller_id", params.controller_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return apiFetch<ExecutorInfo[]>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors${q ? `?${q}` : ""}`,
    );
  },

  getExecutorsSummary: (server: string, period: string) =>
    apiFetch<ExecutorPeriodSummary>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/summary?period=${encodeURIComponent(period)}`,
    ),

  getExecutorsPage: (
    server: string,
    params: {
      cursor?: string;
      limit?: number;
      executor_type?: string;
      trading_pair?: string;
      status?: string;
      controller_id?: string;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.cursor) qs.set("cursor", params.cursor);
    qs.set("limit", String(params.limit ?? 50));
    if (params.executor_type) qs.set("executor_type", params.executor_type);
    if (params.trading_pair) qs.set("trading_pair", params.trading_pair);
    if (params.status) qs.set("status", params.status);
    if (params.controller_id) qs.set("controller_id", params.controller_id);
    return apiFetch<{ executors: ExecutorInfo[]; next_cursor: string | null }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/page?${qs.toString()}`,
    );
  },

  createExecutor: (
    server: string,
    data: { executor_type: string; config: Record<string, unknown>; account_name?: string; controller_id?: string },
  ) =>
    apiFetch<{ status: string; executor_id: string }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  stopExecutor: (server: string, executorId: string, keepPosition = false) =>
    apiFetch<{ status: string; result: unknown }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/${encodeURIComponent(executorId)}/stop?keep_position=${keepPosition}`,
      { method: "POST" },
    ),

  getPositionsHeld: (server: string) =>
    apiFetch<PositionsResponse>(`/api/v1/servers/${encodeURIComponent(server)}/executors/positions`),

  clearPositionHeld: (server: string, connector: string, pair: string, controllerId?: string) => {
    const params = controllerId ? `?controller_id=${encodeURIComponent(controllerId)}` : "";
    return apiFetch<{ status: string }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/positions/${encodeURIComponent(connector)}/${encodeURIComponent(pair)}${params}`,
      { method: "DELETE" },
    );
  },

  getConsolidatedPositions: (server: string) =>
    apiFetch<ConsolidatedPositionsResponse>(`/api/v1/servers/${encodeURIComponent(server)}/positions`),

  getConnectors: (server: string) =>
    apiFetch<string[]>(`/api/v1/servers/${encodeURIComponent(server)}/market/connectors`),

  getConnectedExchanges: (server: string) =>
    apiFetch<string[]>(`/api/v1/servers/${encodeURIComponent(server)}/market/connected-exchanges`),

  getPrice: (server: string, connector: string, pair: string) =>
    apiFetch<MarketPrice>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/prices?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}`,
    ),

  getRates: (server: string, tradingPairs: string[], connector?: string) =>
    apiFetch<{ rates: Record<string, number | null> }>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/rates`,
      {
        method: "POST",
        body: JSON.stringify({ trading_pairs: tradingPairs, connector }),
      },
    ),

  getTradingRules: (server: string, connector: string) =>
    apiFetch<TradingRulesResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/trading-rules?connector=${encodeURIComponent(connector)}`,
    ),

  getTickers: (server: string, connector: string) =>
    apiFetch<TickersResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/tickers?connector=${encodeURIComponent(connector)}`,
    ),

  getOrderBook: (
    server: string,
    connector: string,
    pair: string,
    depth = 20,
  ) =>
    apiFetch<OrderBookResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/order-book?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}&depth=${depth}`,
    ),

  getCandles: (
    server: string,
    connector: string,
    pair: string,
    interval = "1m",
    limit = 1000,
    startTime?: number,
    endTime?: number,
    poolAddress?: string,
  ) => {
    let url = `/api/v1/servers/${encodeURIComponent(server)}/market/candles?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}&interval=${encodeURIComponent(interval)}&limit=${limit}`;
    if (startTime) url += `&start_time=${startTime}`;
    if (endTime) url += `&end_time=${endTime}`;
    // DEX/LP executors: the pool address routes the backend to GeckoTerminal,
    // since these connectors have no CEX candle feed.
    if (poolAddress) url += `&pool_address=${encodeURIComponent(poolAddress)}`;
    return apiFetch<CandleData[]>(url);
  },

  // Resolve a token address → ticker (GeckoTerminal). Not server-scoped; used to
  // render LP/DEX pairs, which are stored as `<address>-<quote>`.
  getTokenSymbol: (mint: string, network?: string) =>
    apiFetch<{ mint: string; symbol: string }>(
      `/api/v1/market/token-symbol?mint=${encodeURIComponent(mint)}${network ? `&network=${encodeURIComponent(network)}` : ""}`,
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
    server_required?: boolean;
    server_name?: string;
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

  /** Set or clear the Agent's server pin and model. An empty `server_name`
   *  clears the pin, so the Agent follows whatever server the chat is pointed
   *  at; an empty `agent_key` clears the model, falling back to the chat's. */
  updateAgentConfig: (
    slug: string,
    data: { server_name?: string; server_required?: boolean; agent_key?: string },
  ) =>
    apiFetch<{
      updated: boolean;
      server_name: string;
      server_required: boolean;
      agent_key: string;
    }>(`/api/v1/agents/${encodeURIComponent(slug)}/config`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteAgent: (slug: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/agents/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }),

  // ── Delegations (fire-and-forget background agent tasks) ──

  getDelegations: () =>
    apiFetch<{ delegations: Delegation[] }>("/api/v1/agents/delegations"),

  /** Every delegation ever recorded, newest first — live ones included, and the
   *  ones whose process is long gone. Optionally scoped to one agent. */
  getDelegationHistory: (agent?: string, limit = 100) =>
    apiFetch<{ delegations: DelegationSummary[] }>(
      `/api/v1/agents/delegations/history?limit=${limit}` +
        (agent ? `&agent=${encodeURIComponent(agent)}` : ""),
    ),

  /** One delegation's full record, from the registry or from disk. */
  getDelegation: (taskId: string) =>
    apiFetch<Delegation>(
      `/api/v1/agents/delegations/${encodeURIComponent(taskId)}`,
    ),

  /** The human-facing transcript. Separate from the (agent-facing) status route
   *  on purpose — see `get_delegation_events` in condor/web/routes/agents.py.
   *  `markdown` is the fallback for records written before the events sidecar:
   *  no structured events, just the transcript as it was rendered to disk. */
  getDelegationEvents: (taskId: string) =>
    apiFetch<{
      task_id: string;
      status: DelegationStatus;
      events: DelegationEvent[];
      markdown: string;
    }>(`/api/v1/agents/delegations/${encodeURIComponent(taskId)}/events`),

  stopDelegation: (taskId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/api/v1/agents/delegations/${encodeURIComponent(taskId)}/stop`,
      { method: "POST" },
    ),

  // ── Strategies (playbooks that loop under an Agent) ──

  getStrategies: (slug: string) =>
    apiFetch<StrategySummary[]>(`/api/v1/agents/${encodeURIComponent(slug)}/strategies`),

  getStrategy: (slug: string, sslug: string) =>
    apiFetch<StrategyDetail>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`,
    ),

  /** Materialize the Agent's default playbook (idempotent) so it can be tuned/looped. */
  createDefaultStrategy: (slug: string) =>
    apiFetch<StrategySummary>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/default`,
      { method: "POST" },
    ),

  createStrategy: (
    slug: string,
    data: {
      name: string;
      description?: string;
      instructions?: string;
      agent_key?: string;
      default_trading_context?: string;
      config?: Record<string, unknown>;
    },
  ) =>
    apiFetch<StrategySummary>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  updateStrategyMd: (slug: string, sslug: string, content: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),

  updateStrategyConfig: (slug: string, sslug: string, config: Record<string, unknown>) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/config`,
      { method: "PUT", body: JSON.stringify({ config }) },
    ),

  deleteStrategy: (slug: string, sslug: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`,
      { method: "DELETE" },
    ),

  getStrategyPerformance: (slug: string, sslug: string) =>
    apiFetch<AgentPerformanceResponse>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/performance`,
    ),

  getStrategySessionExecutors: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{
      executors: AgentExecutorRow[];
      performance: AgentPerformance;
      // Realized-PnL curve sliced from the session's bot-ownership window. Derived
      // from the bots' own history, not from the journal's per-tick snapshots,
      // which only record what the aggregator believed at the time.
      pnl_series?: { timestamp: string; pnl: number; volume: number }[];
    }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/executors`,
    ),

  startStrategy: (
    slug: string,
    sslug: string,
    config: Record<string, unknown> = {},
    trading_context = "",
  ) =>
    apiFetch<{ started: boolean; agent_id: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/start`,
      { method: "POST", body: JSON.stringify({ config, trading_context }) },
    ),

  stopStrategy: (slug: string, sslug: string) =>
    apiFetch<{ stopped: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/stop`,
      { method: "POST" },
    ),

  pauseStrategy: (slug: string, sslug: string) =>
    apiFetch<{ paused: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/pause`,
      { method: "POST" },
    ),

  resumeStrategy: (slug: string, sslug: string) =>
    apiFetch<{ resumed: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/resume`,
      { method: "POST" },
    ),

  getStrategyLearnings: (slug: string, sslug: string) =>
    apiFetch<{ content: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/learnings`,
    ),

  updateStrategyLearnings: (slug: string, sslug: string, content: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/learnings`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),

  getStrategySessions: (slug: string, sslug: string) =>
    apiFetch<{ sessions: SessionInfo[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions`,
    ),

  getSessionJournal: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{ content: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/journal`,
    ),

  getSessionSnapshots: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{ snapshots: SnapshotSummary[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/snapshots`,
    ),

  getSessionCanvas: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<SessionCanvas>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/canvas`,
    ),

  // The live report this session keeps — `{report: null}` when it has none.
  getSessionReport: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{ report: ReportSummary | null }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/report`,
    ),

  getSnapshot: (slug: string, sslug: string, sessionNum: number, tick: number) =>
    apiFetch<{ content: string; tick: number }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/snapshots/${tick}`,
    ),

  // ── Backtesting ──

  submitBacktest: (
    server: string,
    data: {
      config_id: string;
      start_time: number;
      end_time: number;
      backtesting_resolution?: string;
      trade_cost?: number;
    },
  ) =>
    apiFetch<BacktestTask>(`/api/v1/servers/${encodeURIComponent(server)}/backtesting/tasks`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  listBacktestTasks: (server: string) =>
    apiFetch<BacktestTask[]>(`/api/v1/servers/${encodeURIComponent(server)}/backtesting/tasks`),

  getBacktestTask: (server: string, taskId: string) =>
    apiFetch<BacktestTask>(
      `/api/v1/servers/${encodeURIComponent(server)}/backtesting/tasks/${encodeURIComponent(taskId)}`,
    ),

  deleteBacktestTask: (server: string, taskId: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/backtesting/tasks/${encodeURIComponent(taskId)}`,
      { method: "DELETE" },
    ),

  getSavedBacktests: (server: string) =>
    apiFetch<BacktestTask[]>(`/api/v1/servers/${encodeURIComponent(server)}/backtesting/saved`),

  deleteSavedBacktest: (server: string, taskId: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/backtesting/saved/${encodeURIComponent(taskId)}`,
      { method: "DELETE" },
    ),

  // ── Experiments ──

  getAgentExperiments: (slug: string, sslug: string) =>
    apiFetch<{ experiments: ExperimentInfo[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/experiments`,
    ),

  getExperiment: (slug: string, sslug: string, expNum: number) =>
    apiFetch<{ content: string; number: number }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/experiments/${expNum}`,
    ),

  // ── Archived Bots ──

  getArchivedBots: (server: string) =>
    apiFetch<{ bots: ArchivedBotSummary[] }>(`/api/v1/servers/${encodeURIComponent(server)}/archived`),

  getArchivedBotPerformance: (server: string, dbPath: string, includeExecutors = false) =>
    apiFetch<ArchivedBotPerformance>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived/performance?db_path=${encodeURIComponent(dbPath)}&include_executors=${includeExecutors}`,
    ),

  getArchivedExecutors: (server: string, dbPath: string, offset = 0, limit = 50) =>
    apiFetch<PaginatedExecutors>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived/executors?db_path=${encodeURIComponent(dbPath)}&offset=${offset}&limit=${limit}`,
    ),

  // ── Reports ──

  getReports: (params?: { source_type?: string; tag?: string; search?: string; agent?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.source_type) qs.set("source_type", params.source_type);
    if (params?.tag) qs.set("tag", params.tag);
    if (params?.search) qs.set("search", params.search);
    if (params?.agent) qs.set("agent", params.agent);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return apiFetch<ReportsListResponse>(`/api/v1/reports${q ? `?${q}` : ""}`);
  },

  getReport: (id: string) =>
    apiFetch<ReportSummary>(`/api/v1/reports/${encodeURIComponent(id)}`),

  getReportsGrouped: () => apiFetch<ReportGroup[]>("/api/v1/reports/latest-by-source"),

  /**
   * A report's rendered HTML body.
   *
   * Report bodies are authenticated (SEC-112), and an iframe `src` cannot carry
   * an Authorization header — so callers fetch the HTML here and hand it to the
   * iframe as `srcDoc`, keeping the token in a header instead of the URL.
   */
  getReportHtml: async (id: string): Promise<string> => {
    const res = await authFetch(`/api/v1/reports/${encodeURIComponent(id)}/html`);
    if (!res.ok) throw new Error(`Failed to load report (${res.status})`);
    return res.text();
  },

  deleteReport: (id: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/reports/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // ── Routines ──

  getRoutines: () => apiFetch<RoutineInfo[]>("/api/v1/routines"),

  getRoutineInstances: () =>
    apiFetch<RoutineInstance[]>("/api/v1/routines/instances"),

  getRoutineInstance: (id: string) =>
    apiFetch<RoutineInstance>(`/api/v1/routines/instances/${encodeURIComponent(id)}`),

  runRoutine: (server: string, name: string, config: Record<string, unknown> = {}) =>
    apiFetch<{ instance_id: string }>(
      `/api/v1/routines/run`,
      { method: "POST", body: JSON.stringify({ routine_name: name, server_name: server, config }) },
    ),

  scheduleRoutine: (
    server: string,
    name: string,
    config: Record<string, unknown> = {},
    interval_sec: number = 300,
  ) =>
    apiFetch<{ instance_id: string }>(
      `/api/v1/routines/schedule`,
      { method: "POST", body: JSON.stringify({ routine_name: name, server_name: server, config, interval_sec }) },
    ),

  stopRoutineInstance: (id: string) =>
    apiFetch<{ stopped: boolean }>(`/api/v1/routines/instances/${encodeURIComponent(id)}/stop`, {
      method: "POST",
    }),

  getRoutineSource: (name: string) =>
    apiFetch<{ filename: string; source: string }>(
      `/api/v1/routines/${encodeURIComponent(name)}/source`,
    ),

  getRoutineReports: (name: string) =>
    apiFetch<ReportsListResponse>(
      `/api/v1/routines/${encodeURIComponent(name)}/reports`,
    ),

  getRoutineFieldOptions: (source: string, server: string) =>
    apiFetch<{ options: string[] }>(
      `/api/v1/routines/options/${encodeURIComponent(source)}?server=${encodeURIComponent(server)}`,
    ),

  getRoutineHooks: (name: string) =>
    apiFetch<RoutineHooks>(`/api/v1/routines/${encodeURIComponent(name)}/hooks`),

  saveRoutineHooks: (name: string, hooks: RoutineHooks) =>
    apiFetch<RoutineHooks>(`/api/v1/routines/${encodeURIComponent(name)}/hooks`, {
      method: "PUT",
      body: JSON.stringify(hooks),
    }),

  // ── Agent Routines & Reports ──

  getAgentRoutines: (slug: string) =>
    apiFetch<RoutineInfo[]>(`/api/v1/agents/${encodeURIComponent(slug)}/routines`),

  getAgentReports: (slug: string) =>
    apiFetch<ReportsListResponse>(`/api/v1/agents/${encodeURIComponent(slug)}/reports`),

  // ── Settings ──

  getSettingsServers: () => apiFetch<ServerInfo[]>("/api/v1/settings/servers"),

  addServer: (data: { name: string; host: string; port: number; username: string; password: string }) =>
    apiFetch<{ added: boolean; name: string }>("/api/v1/settings/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateServer: (name: string, data: { host?: string; port?: number; username?: string; password?: string }) =>
    apiFetch<{ updated: boolean }>(`/api/v1/settings/servers/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteServer: (name: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/settings/servers/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  setDefaultServer: (name: string) =>
    apiFetch<{ default: boolean }>(`/api/v1/settings/servers/${encodeURIComponent(name)}/default`, {
      method: "POST",
    }),

  getGatewayStatus: (server: string) =>
    apiFetch<GatewayStatus>(`/api/v1/settings/gateway/status?server=${encodeURIComponent(server)}`),

  startGateway: (server: string, data: { image: string; port?: number }) =>
    apiFetch<{ started: boolean }>(`/api/v1/settings/gateway/start?server=${encodeURIComponent(server)}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  stopGateway: (server: string) =>
    apiFetch<{ stopped: boolean }>(`/api/v1/settings/gateway/stop?server=${encodeURIComponent(server)}`, {
      method: "POST",
    }),

  restartGateway: (server: string) =>
    apiFetch<{ restarted: boolean }>(`/api/v1/settings/gateway/restart?server=${encodeURIComponent(server)}`, {
      method: "POST",
    }),

  pullGatewayImage: (server: string, data: { image: string }) =>
    apiFetch<{ pulled: boolean; image: string }>(`/api/v1/settings/gateway/pull?server=${encodeURIComponent(server)}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getGatewayPullStatus: (server: string) =>
    apiFetch<{ pull_operations: Record<string, { status: string; progress: string; duration_seconds: number; started_at: number }>; total_operations: number }>(
      `/api/v1/settings/gateway/pull-status?server=${encodeURIComponent(server)}`,
    ),

  getGatewayLogs: (server: string) =>
    apiFetch<{ logs: string }>(`/api/v1/settings/gateway/logs?server=${encodeURIComponent(server)}`),

  getCredentials: (server: string) =>
    apiFetch<{ credentials: (CredentialInfo | string)[] }>(`/api/v1/settings/credentials?server=${encodeURIComponent(server)}`),

  getAvailableConnectors: (server: string, type?: string) => {
    let url = `/api/v1/settings/connectors?server=${encodeURIComponent(server)}`;
    if (type) url += `&type=${encodeURIComponent(type)}`;
    return apiFetch<{ connectors: ConnectorInfo[] }>(url);
  },

  getConnectorConfigMap: (server: string, name: string) =>
    apiFetch<{ config_map: Record<string, unknown> }>(
      `/api/v1/settings/connectors/${encodeURIComponent(name)}/config-map?server=${encodeURIComponent(server)}`,
    ),

  addCredential: (server: string, data: { connector_name: string; credentials: Record<string, string> }) =>
    apiFetch<{ added: boolean }>(`/api/v1/settings/credentials?server=${encodeURIComponent(server)}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  deleteCredential: (server: string, connector: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/settings/credentials/${encodeURIComponent(connector)}?server=${encodeURIComponent(server)}`,
      { method: "DELETE" },
    ),

  // ── Voice Settings ──

  getVoiceSettings: () =>
    apiFetch<VoiceSettingsResponse>("/api/v1/settings/voice"),

  updateVoiceSettings: (data: Partial<VoicePrefs>) =>
    apiFetch<{ voice: VoicePrefs }>("/api/v1/settings/voice", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  // ── Chat ──

  getOpenRouterModels: () =>
    apiFetch<{ models: OpenRouterModelOption[] }>("/api/v1/chat/openrouter/models"),

  // ── Sessions (runtime API) ──
  //
  // The same sessions the Telegram bot uses: one keyspace, so a session started
  // there is listed and killable here.

  getSessionOptions: () =>
    apiFetch<SessionOptionsResponse>("/api/v1/sessions/options"),

  listSessions: () => apiFetch<SessionInfo[]>("/api/v1/sessions"),

  createSession: (spec: CreateSessionRequest) =>
    apiFetch<SessionInfo>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify(spec),
    }),

  destroySession: (key: string) =>
    apiFetch<{ destroyed: boolean }>(
      `/api/v1/sessions/${encodeURIComponent(key)}`,
      { method: "DELETE" },
    ),

  // Binds the chat to a different brain. The subprocess is replaced, so the
  // conversation only carries over via `handoff`.
  switchSession: (key: string, body: SwitchSessionRequest) =>
    apiFetch<{ ok: boolean; session: SessionInfo }>(
      `/api/v1/sessions/${encodeURIComponent(key)}/action`,
      { method: "POST", body: JSON.stringify({ action: "switch", ...body }) },
    ),

  cancelSessionPrompt: (key: string) =>
    apiFetch<{ ok: boolean }>(
      `/api/v1/sessions/${encodeURIComponent(key)}/action`,
      { method: "POST", body: JSON.stringify({ action: "cancel" }) },
    ),

  // ── Conversations (durable transcripts) ──
  //
  // Everything the user has ever said, across Telegram and the dashboard, in
  // one keyspace. A conversation is continuable long after its session died.

  listConversations: (limit = 100) =>
    apiFetch<ConversationMeta[]>(`/api/v1/conversations?limit=${limit}`),

  getConversation: (id: string, limit = 200) =>
    apiFetch<ConversationDetail>(
      `/api/v1/conversations/${encodeURIComponent(id)}?limit=${limit}`,
    ),

  renameConversation: (id: string, title: string) =>
    apiFetch<ConversationMeta>(`/api/v1/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  /** The only way to lose a transcript. Killing a session no longer does. */
  deleteConversation: (id: string) =>
    apiFetch<{ deleted: boolean; sessions_destroyed: number }>(
      `/api/v1/conversations/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),

  // ── Custom OpenAI-compatible LLM endpoints ──

  getCustomProviders: () =>
    apiFetch<{ providers: CustomProvider[] }>("/api/v1/settings/custom-providers"),

  /** Validates the endpoint server-side before saving; rejects with the
   *  provider's own error text (bad key, unreachable host, wrong shape). */
  addCustomProvider: (data: { base_url: string; api_key?: string; name?: string }) =>
    apiFetch<{ provider: CustomProvider; models: string[] }>(
      "/api/v1/settings/custom-providers",
      { method: "POST", body: JSON.stringify(data) },
    ),

  getCustomProviderModels: (name: string) =>
    apiFetch<{ provider: CustomProvider; models: string[] }>(
      `/api/v1/settings/custom-providers/${encodeURIComponent(name)}/models`,
    ),

  deleteCustomProvider: (name: string) =>
    apiFetch<{ deleted: boolean; name: string }>(
      `/api/v1/settings/custom-providers/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
};
