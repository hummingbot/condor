// ── Parsed types for journal.md and snapshot.md ──

export interface JournalSummary {
  lastTick: number;
  lastTime: string;
  status: string;
  pnl: number;
  openExecutors: number;
  lastAction: string;
}

export interface Decision {
  tick: number;
  time: string;
  action: string;
  reasoning: string;
  riskNote: string;
}

export interface TickEntry {
  tick: number;
  timestamp: string;
  cost: number;
  actions: number;
  summary: string;
}

export interface ExecutorEntry {
  id: string;
  type: string;
  connector: string;
  pair: string;
  side: string;
  amount: number;
  created: string;
  status: string;
  pnl: number;
  volume: number;
  stopped?: string;
}

export interface MetricEntry {
  timestamp: string;
  pnl: number;
  volume: number;
  open: number;
  exposure: number;
}

export interface ParsedJournal {
  summary: JournalSummary;
  decisions: Decision[];
  ticks: TickEntry[];
  executors: ExecutorEntry[];
  metrics: MetricEntry[];
}

export interface ToolCall {
  number: number;
  name: string;
  status: string;
  input: string;
  output: string;
}

export interface SnapshotCost {
  llmCost: number;
  duration: number;
  inputTokens: number;
  outputTokens: number;
}

export interface ParsedSnapshot {
  tick: number;
  timestamp: string;
  systemPrompt: string;
  systemPromptLength: number;
  executorState: string;
  riskState: string;
  agentResponse: string;
  toolCalls: ToolCall[];
  cost: SnapshotCost;
}

// ── Section extraction helper ──

function getSection(text: string, name: string): string {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Find section start, then grab everything until next ## header or <details> block
  const startPattern = new RegExp(`^## ${escaped}\\n`, "m");
  const startMatch = startPattern.exec(text);
  if (!startMatch) return "";
  const contentStart = startMatch.index + startMatch[0].length;
  // Find next section boundary: ## header or <details> at start of line
  const rest = text.slice(contentStart);
  const endMatch = rest.match(/^(?:## |<details>)/m);
  const content = endMatch ? rest.slice(0, endMatch.index) : rest;
  return content.trim();
}

// ── Journal parser ──

export function parseJournal(content: string): ParsedJournal {
  const summary = parseSummary(getSection(content, "Summary"));
  const decisions = parseDecisions(getSection(content, "Decisions"));
  const ticks = parseTicks(getSection(content, "Ticks"));
  const executors = parseExecutors(getSection(content, "Executors"));
  const metrics = parseMetrics(getSection(content, "Snapshots"));
  return { summary, decisions, ticks, executors, metrics };
}

function parseSummary(text: string): JournalSummary {
  const result: JournalSummary = {
    lastTick: 0,
    lastTime: "",
    status: "idle",
    pnl: 0,
    openExecutors: 0,
    lastAction: "",
  };

  // Last tick: #N at HH:MM UTC
  const tickMatch = text.match(/Last tick: #(\d+) at (.+)/);
  if (tickMatch) {
    result.lastTick = parseInt(tickMatch[1]);
    result.lastTime = tickMatch[2];
  }

  // Status: X | PnL: $N | Open: N executors
  const statusMatch = text.match(/Status:\s*(\S+)\s*\|\s*PnL:\s*\$([+-]?[\d.]+)\s*\|\s*Open:\s*(\d+)/);
  if (statusMatch) {
    result.status = statusMatch[1];
    result.pnl = parseFloat(statusMatch[2]);
    result.openExecutors = parseInt(statusMatch[3]);
  }

  // Last action: text
  const actionMatch = text.match(/Last action:\s*(.+)/);
  if (actionMatch) {
    result.lastAction = actionMatch[1].trim();
  }

  return result;
}

function parseDecisions(text: string): Decision[] {
  const results: Decision[] = [];
  // - **#N** (HH:MM) action -- reasoning [risk_note]
  // - **error** (HH:MM) text
  for (const line of text.split("\n")) {
    if (!line.startsWith("- ")) continue;

    const m = line.match(/^- \*\*#(\d+)\*\* \((\d{2}:\d{2})\)\s+(.+)/);
    if (m) {
      const tick = parseInt(m[1]);
      const time = m[2];
      let rest = m[3];

      let riskNote = "";
      const riskMatch = rest.match(/\[([^\]]+)\]\s*$/);
      if (riskMatch) {
        riskNote = riskMatch[1];
        rest = rest.slice(0, riskMatch.index).trim();
      }

      let action = rest;
      let reasoning = "";
      const dashIdx = rest.indexOf(" -- ");
      if (dashIdx !== -1) {
        action = rest.slice(0, dashIdx);
        reasoning = rest.slice(dashIdx + 4);
      }

      results.push({ tick, time, action, reasoning, riskNote });
      continue;
    }

    // error entries
    const errMatch = line.match(/^- \*\*error\*\* \((\d{2}:\d{2})\)\s+(.+)/);
    if (errMatch) {
      results.push({
        tick: 0,
        time: errMatch[1],
        action: "ERROR: " + errMatch[2],
        reasoning: "",
        riskNote: "",
      });
    }
  }
  return results;
}

function parseTicks(text: string): TickEntry[] {
  const results: TickEntry[] = [];
  for (const line of text.split("\n")) {
    if (!line.startsWith("- tick#")) continue;
    const parts = line.slice(2).split(" | ");
    const entry: TickEntry = { tick: 0, timestamp: "", cost: 0, actions: 0, summary: "" };
    for (const part of parts) {
      const p = part.trim();
      if (p.startsWith("tick#")) {
        entry.tick = parseInt(p.replace("tick#", ""));
      } else if (p.startsWith("cost=$")) {
        entry.cost = parseFloat(p.replace("cost=$", ""));
      } else if (p.startsWith("actions=")) {
        entry.actions = parseInt(p.replace("actions=", ""));
      } else if (/^\d{4}-\d{2}-\d{2}/.test(p)) {
        entry.timestamp = p;
      } else {
        entry.summary = p;
      }
    }
    results.push(entry);
  }
  return results;
}

function parseExecutors(text: string): ExecutorEntry[] {
  const results: ExecutorEntry[] = [];
  for (const line of text.split("\n")) {
    if (!line.startsWith("- executor=")) continue;
    const entry: Record<string, string> = {};
    for (const part of line.slice(2).split(" | ")) {
      if (part.includes("=")) {
        const eqIdx = part.indexOf("=");
        entry[part.slice(0, eqIdx).trim()] = part.slice(eqIdx + 1).trim();
      } else {
        // "connector pair side" segment
        const tokens = part.trim().split(/\s+/);
        if (tokens.length >= 3) {
          entry.connector = tokens[0];
          entry.pair = tokens[1];
          entry.side = tokens[2];
        } else if (tokens.length === 2) {
          entry.connector = tokens[0];
          entry.pair = tokens[1];
        }
      }
    }
    results.push({
      id: entry.executor || "",
      type: entry.type || "",
      connector: entry.connector || "",
      pair: entry.pair || "",
      side: entry.side || "",
      amount: parseFloat((entry.amount || "0").replace("$", "")),
      created: entry.created || "",
      status: entry.status || "",
      pnl: parseFloat(entry.pnl || "0"),
      volume: parseFloat(entry.volume || "0"),
      stopped: entry.stopped,
    });
  }
  return results;
}

function parseMetrics(text: string): MetricEntry[] {
  const results: MetricEntry[] = [];
  for (const line of text.split("\n")) {
    if (!line.startsWith("- ")) continue;
    const entry: MetricEntry = { timestamp: "", pnl: 0, volume: 0, open: 0, exposure: 0 };
    for (const part of line.slice(2).split(" | ")) {
      const p = part.trim();
      if (p.startsWith("pnl=$")) {
        entry.pnl = parseFloat(p.replace("pnl=$", "").replace("+", ""));
      } else if (p.startsWith("volume=$")) {
        entry.volume = parseFloat(p.replace("volume=$", "").replace(/,/g, ""));
      } else if (p.startsWith("open=")) {
        entry.open = parseInt(p.replace("open=", ""));
      } else if (p.startsWith("exposure=$")) {
        entry.exposure = parseFloat(p.replace("exposure=$", ""));
      } else if (/^\d{4}-\d{2}-\d{2}/.test(p)) {
        entry.timestamp = p;
      }
    }
    results.push(entry);
  }
  return results;
}

// ── Snapshot parser ──

export function parseSnapshot(content: string): ParsedSnapshot {
  const result: ParsedSnapshot = {
    tick: 0,
    timestamp: "",
    systemPrompt: "",
    systemPromptLength: 0,
    executorState: "",
    riskState: "",
    agentResponse: "",
    toolCalls: [],
    cost: { llmCost: 0, duration: 0, inputTokens: 0, outputTokens: 0 },
  };

  // Header: # Snapshot #N — timestamp
  const headerMatch = content.match(/^# Snapshot #(\d+)\s*[—–-]\s*(.+)/m);
  if (headerMatch) {
    result.tick = parseInt(headerMatch[1]);
    result.timestamp = headerMatch[2].trim();
  }

  // System prompt - inside <details> block
  const promptLenMatch = content.match(/System Prompt \((\d+) chars\)/);
  if (promptLenMatch) {
    result.systemPromptLength = parseInt(promptLenMatch[1]);
  }
  const detailsMatch = content.match(/<details><summary>System Prompt[^<]*<\/summary>\s*(.*?)\s*<\/details>/s);
  if (detailsMatch) {
    result.systemPrompt = detailsMatch[1].trim();
  }

  // Sections
  result.executorState = getSection(content, "Executor State");
  result.riskState = getSection(content, "Risk State");
  result.agentResponse = getSection(content, "Agent Response");

  // Tool calls - inside <details> block
  const toolDetailsMatch = content.match(/<details><summary>Tool Calls[^<]*<\/summary>\s*(.*?)\s*<\/details>/s);
  if (toolDetailsMatch) {
    result.toolCalls = parseToolCalls(toolDetailsMatch[1]);
  }

  // Cost: LLM: $N | Duration: Ns | Tokens: N in / N out
  const costSection = getSection(content, "Cost");
  const costMatch = costSection.match(
    /LLM:\s*\$([\d.]+)\s*\|\s*Duration:\s*([\d.]+)s\s*\|\s*Tokens:\s*(\d+)\s*in\s*\/\s*(\d+)\s*out/
  );
  if (costMatch) {
    result.cost = {
      llmCost: parseFloat(costMatch[1]),
      duration: parseFloat(costMatch[2]),
      inputTokens: parseInt(costMatch[3]),
      outputTokens: parseInt(costMatch[4]),
    };
  }

  return result;
}

function parseToolCalls(text: string): ToolCall[] {
  const results: ToolCall[] = [];
  // Match each ### N. tool_name (status) line, with optional content after
  const headerRegex = /^### (\d+)\.\s+(\S+)\s+\(([^)]*)\)/gm;
  let match;
  const headers: { number: number; name: string; status: string; index: number }[] = [];

  while ((match = headerRegex.exec(text)) !== null) {
    headers.push({
      number: parseInt(match[1]),
      name: match[2],
      status: match[3],
      index: match.index + match[0].length,
    });
  }

  for (let i = 0; i < headers.length; i++) {
    const h = headers[i];
    const blockEnd = i + 1 < headers.length ? headers[i + 1].index - headers[i + 1].name.length - 20 : text.length;
    const block = text.slice(h.index, blockEnd);

    const tc: ToolCall = {
      number: h.number,
      name: h.name,
      status: h.status,
      input: "",
      output: "",
    };

    // Extract input code block
    const inputMatch = block.match(/\*\*Input:\*\*\n```(?:json)?\n(.*?)\n```/s);
    if (inputMatch) tc.input = inputMatch[1].trim();

    // Extract output code block
    const outputMatch = block.match(/\*\*Output:\*\*\n```\n(.*?)\n```/s);
    if (outputMatch) tc.output = outputMatch[1].trim();

    results.push(tc);
  }
  return results;
}
