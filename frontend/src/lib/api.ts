const TOKEN_KEY = "condor_token";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
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

export interface BotSummary {
  bot_name: string;
  status: string;
  num_controllers: number;
  error_count: number;
  deployed_at: string | null;
}

export interface BotsPageResponse {
  controllers: ControllerInfo[];
  bots: BotSummary[];
  total_pnl: number;
  total_volume: number;
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
  tick_count: number;
  daily_pnl: number;
  server_name: string;
  total_amount_quote: number;
  trading_context: string;
  frequency_sec: number;
  execution_mode: "dry_run" | "run_once" | "loop";
  risk_limits: Record<string, unknown>;
}

export interface AgentSummary {
  slug: string;
  name: string;
  description: string;
  status: string;
  agent_id: string;
  session_count: number;
  experiment_count: number;
  tick_count: number;
  daily_pnl: number;
  instances: RunningInstance[];
}

export interface SessionInfo {
  number: number;
  snapshot_count: number;
  created_at: string;
}

export interface ExperimentInfo {
  number: number;
  execution_mode: string;
  snapshot_count: number;
  created_at: string;
}

export interface AgentDetail {
  slug: string;
  name: string;
  description: string;
  agent_md: string;
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

// ── API functions ──

export const api = {
  getServers: () => apiFetch<ServerInfo[]>("/api/v1/servers"),

  getServerStatus: (name: string) =>
    apiFetch<{ online: boolean; error?: string }>(
      `/api/v1/servers/${name}/status`,
    ),

  getPortfolio: (server: string) =>
    apiFetch<PortfolioResponse>(`/api/v1/servers/${server}/portfolio`),

  getPortfolioHistory: (server: string, range = "1D", breakdown = false) =>
    apiFetch<PortfolioHistoryResponse>(
      `/api/v1/servers/${server}/portfolio/history?range=${range}${breakdown ? "&breakdown=true" : ""}`,
    ),

  getBots: (server: string) =>
    apiFetch<BotsPageResponse>(`/api/v1/servers/${server}/bots`),

  getBot: (server: string, botId: string) =>
    apiFetch<BotDetail>(`/api/v1/servers/${server}/bots/${botId}`),

  getAvailableConfigs: (server: string) =>
    apiFetch<AvailableControllersResponse>(
      `/api/v1/servers/${server}/controllers/configs`,
    ),

  getConfigDetail: (server: string, configId: string) =>
    apiFetch<ControllerConfigDetail>(
      `/api/v1/servers/${server}/controllers/configs/${configId}`,
    ),

  updateConfig: (server: string, configId: string, data: Record<string, unknown>) =>
    apiFetch<{ updated: boolean }>(`/api/v1/servers/${server}/controllers/configs/${configId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deployBot: (server: string, data: DeployBotRequest) =>
    apiFetch<Record<string, unknown>>(`/api/v1/servers/${server}/bots/deploy`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getExecutors: (
    server: string,
    params?: { executor_type?: string; trading_pair?: string; status?: string },
  ) => {
    const qs = new URLSearchParams();
    if (params?.executor_type) qs.set("executor_type", params.executor_type);
    if (params?.trading_pair) qs.set("trading_pair", params.trading_pair);
    if (params?.status) qs.set("status", params.status);
    const q = qs.toString();
    return apiFetch<ExecutorInfo[]>(
      `/api/v1/servers/${server}/executors${q ? `?${q}` : ""}`,
    );
  },

  createExecutor: (
    server: string,
    data: { executor_type: string; config: Record<string, unknown>; account_name?: string; controller_id?: string },
  ) =>
    apiFetch<{ status: string; executor_id: string }>(
      `/api/v1/servers/${server}/executors`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  stopExecutor: (server: string, executorId: string, keepPosition = false) =>
    apiFetch<{ status: string; result: unknown }>(
      `/api/v1/servers/${server}/executors/${executorId}/stop?keep_position=${keepPosition}`,
      { method: "POST" },
    ),

  getPositionsHeld: (server: string) =>
    apiFetch<PositionsResponse>(`/api/v1/servers/${server}/executors/positions`),

  clearPositionHeld: (server: string, connector: string, pair: string, controllerId?: string) => {
    const params = controllerId ? `?controller_id=${encodeURIComponent(controllerId)}` : "";
    return apiFetch<{ status: string }>(
      `/api/v1/servers/${server}/executors/positions/${connector}/${pair}${params}`,
      { method: "DELETE" },
    );
  },

  getConnectors: (server: string) =>
    apiFetch<string[]>(`/api/v1/servers/${server}/market/connectors`),

  getConnectedExchanges: (server: string) =>
    apiFetch<string[]>(`/api/v1/servers/${server}/market/connected-exchanges`),

  getPrice: (server: string, connector: string, pair: string) =>
    apiFetch<MarketPrice>(
      `/api/v1/servers/${server}/market/prices?connector=${connector}&trading_pair=${pair}`,
    ),

  getTradingRules: (server: string, connector: string) =>
    apiFetch<TradingRulesResponse>(
      `/api/v1/servers/${server}/market/trading-rules?connector=${connector}`,
    ),

  getOrderBook: (
    server: string,
    connector: string,
    pair: string,
    depth = 20,
  ) =>
    apiFetch<OrderBookResponse>(
      `/api/v1/servers/${server}/market/order-book?connector=${connector}&trading_pair=${pair}&depth=${depth}`,
    ),

  getCandles: (
    server: string,
    connector: string,
    pair: string,
    interval = "1m",
    limit = 1000,
    startTime?: number,
  ) => {
    let url = `/api/v1/servers/${server}/market/candles?connector=${connector}&trading_pair=${pair}&interval=${interval}&limit=${limit}`;
    if (startTime) url += `&start_time=${startTime}`;
    return apiFetch<CandleData[]>(url);
  },

  // ── Agents ──

  getAgents: () => apiFetch<AgentSummary[]>("/api/v1/agents"),

  getAgent: (slug: string) =>
    apiFetch<AgentDetail>(`/api/v1/agents/${slug}`),

  createAgent: (data: {
    name: string;
    description?: string;
    instructions?: string;
    default_trading_context?: string;
    config?: Record<string, unknown>;
  }) =>
    apiFetch<AgentSummary>("/api/v1/agents", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateAgentMd: (slug: string, content: string) =>
    apiFetch<{ updated: boolean }>(`/api/v1/agents/${slug}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),

  updateAgentConfig: (slug: string, config: Record<string, unknown>) =>
    apiFetch<{ updated: boolean }>(`/api/v1/agents/${slug}/config`, {
      method: "PUT",
      body: JSON.stringify({ config }),
    }),

  deleteAgent: (slug: string) =>
    apiFetch<{ deleted: boolean }>(`/api/v1/agents/${slug}`, {
      method: "DELETE",
    }),

  startAgent: (slug: string, config: Record<string, unknown> = {}, trading_context = "") =>
    apiFetch<{ started: boolean; agent_id: string }>(
      `/api/v1/agents/${slug}/start`,
      { method: "POST", body: JSON.stringify({ config, trading_context }) },
    ),

  stopAgent: (slug: string) =>
    apiFetch<{ stopped: boolean }>(`/api/v1/agents/${slug}/stop`, {
      method: "POST",
    }),

  pauseAgent: (slug: string) =>
    apiFetch<{ paused: boolean }>(`/api/v1/agents/${slug}/pause`, {
      method: "POST",
    }),

  resumeAgent: (slug: string) =>
    apiFetch<{ resumed: boolean }>(`/api/v1/agents/${slug}/resume`, {
      method: "POST",
    }),

  getAgentLearnings: (slug: string) =>
    apiFetch<{ content: string }>(`/api/v1/agents/${slug}/learnings`),

  updateAgentLearnings: (slug: string, content: string) =>
    apiFetch<{ updated: boolean }>(`/api/v1/agents/${slug}/learnings`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),

  getAgentSessions: (slug: string) =>
    apiFetch<{ sessions: SessionInfo[] }>(`/api/v1/agents/${slug}/sessions`),

  getSessionJournal: (slug: string, sessionNum: number) =>
    apiFetch<{ content: string }>(
      `/api/v1/agents/${slug}/sessions/${sessionNum}/journal`,
    ),

  getSessionSnapshots: (slug: string, sessionNum: number) =>
    apiFetch<{ snapshots: SnapshotSummary[] }>(
      `/api/v1/agents/${slug}/sessions/${sessionNum}/snapshots`,
    ),

  getSnapshot: (slug: string, sessionNum: number, tick: number) =>
    apiFetch<{ content: string; tick: number }>(
      `/api/v1/agents/${slug}/sessions/${sessionNum}/snapshots/${tick}`,
    ),

  // ── Experiments ──

  getAgentExperiments: (slug: string) =>
    apiFetch<{ experiments: ExperimentInfo[] }>(`/api/v1/agents/${slug}/experiments`),

  getExperiment: (slug: string, expNum: number) =>
    apiFetch<{ content: string; number: number }>(
      `/api/v1/agents/${slug}/experiments/${expNum}`,
    ),
};
