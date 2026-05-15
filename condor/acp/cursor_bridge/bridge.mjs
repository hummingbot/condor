/**
 * Condor ↔ Cursor JSON bridge (stdio, one JSON object per line).
 *
 * Protocol from Python (stdin):
 *   {"op":"init","cwd":"<abs path>","modelId":"<cursor model id>"}
 *       optional: "apiKey" (otherwise CURSOR_API_KEY env)
 *       optional: "mcpServers": { "<name>": { "type":"stdio", "command", "args", "env?", "cwd" }, ... }
 *   {"op":"prompt","id":"<uuid>","text":"<user message>"}
 *   {"op":"shutdown"}
 *
 * To Python (stdout), events include:
 *   {"kind":"ready"}
 *   {"kind":"text"|"thinking"|"tool"|"tool_status"|"run_started"|"done"|"error", ...}
 */

import * as readline from "node:readline";
import { Agent, CursorAgentError } from "@cursor/sdk";

function out(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

/** @type {import("@cursor/sdk").SDKAgent | null} */
let agent = null;

/** @param {string} promptId @param {import("@cursor/sdk").SDKMessage} msg */
function mapStreamMessage(promptId, msg) {
  switch (msg.type) {
    case "assistant": {
      const content = msg.message?.content ?? [];
      for (const block of content) {
        if (block?.type === "text" && block.text) {
          out({ kind: "text", promptId, text: block.text });
        } else if (block?.type === "tool_use") {
          out({
            kind: "tool",
            promptId,
            phase: "use",
            call_id: block.id,
            name: block.name,
            input: block.input,
          });
        }
      }
      break;
    }
    case "thinking":
      if (msg.text) {
        out({ kind: "thinking", promptId, text: msg.text });
      }
      break;
    case "tool_call":
      out({
        kind: "tool_status",
        promptId,
        call_id: msg.call_id,
        name: msg.name,
        status: msg.status,
        args: msg.args,
        result: msg.result,
      });
      break;
    default:
      break;
  }
}

/** @param {string} promptId @param {string} text */
async function runPrompt(promptId, text) {
  if (!agent) {
    out({ kind: "error", promptId, message: "Agent not initialized" });
    out({ kind: "done", promptId, stopReason: "error" });
    return;
  }
  try {
    const run = await agent.send(text);
    out({ kind: "run_started", promptId, runId: run.id });
    if (run.supports("stream")) {
      try {
        for await (const msg of run.stream()) {
          mapStreamMessage(promptId, msg);
        }
      } catch (streamErr) {
        out({
          kind: "error",
          promptId,
          message: `stream: ${streamErr?.message ?? streamErr}`,
        });
      }
    }
    const result = await run.wait();
    const status = result.status ?? "error";
    const stopReason = status === "finished" ? "end_turn" : status;
    out({
      kind: "done",
      promptId,
      stopReason,
      resultText: result.result,
    });
  } catch (err) {
    let message = err?.message ?? String(err);
    if (err instanceof CursorAgentError) {
      message = `${message} (retryable=${err.isRetryable ?? "?"})`;
    }
    out({ kind: "error", promptId, message });
    out({
      kind: "done",
      promptId,
      stopReason: "error",
    });
  }
}

async function shutdown() {
  if (!agent) {
    return;
  }
  try {
    await agent[Symbol.asyncDispose]();
  } catch {
    try {
      agent.close?.();
    } catch {
      /* noop */
    }
  }
  agent = null;
}

/** @param {string} line */
async function handleLine(line) {
  const trimmed = line.trim();
  if (!trimmed) {
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    out({ kind: "error", message: "Invalid JSON on stdin" });
    return;
  }
  const op = parsed.op;
  if (op === "init") {
    if (agent) {
      out({ kind: "error", stage: "init", message: "Already initialized" });
      process.exit(4);
    }
    const cwd = parsed.cwd || process.cwd();
    let modelId = parsed.modelId ?? "auto";
    if (typeof modelId !== "string" || !modelId.trim()) {
      modelId = "auto";
    }
    const apiKey = parsed.apiKey || process.env.CURSOR_API_KEY;
    if (!apiKey) {
      out({
        kind: "error",
        stage: "init",
        message:
          "CURSOR_API_KEY is not set. Add it to .env or pass apiKey in init message.",
      });
      process.exit(2);
    }
    const mcpServers =
      parsed.mcpServers && typeof parsed.mcpServers === "object"
        ? parsed.mcpServers
        : undefined;
    const createOpts = {
      apiKey,
      model: { id: modelId },
      local: { cwd, settingSources: [] },
    };
    if (mcpServers && Object.keys(mcpServers).length > 0) {
      createOpts.mcpServers = mcpServers;
    }
    try {
      agent = await Agent.create(createOpts);
      console.error(
        "[condor-bridge] Agent.create ok (stderr smoke; MCP child stdio often does not arrive here)",
      );
    } catch (err) {
      let message = err?.message ?? String(err);
      if (err instanceof CursorAgentError) {
        message = `${message} (retryable=${err.isRetryable ?? "?"})`;
      }
      out({ kind: "error", stage: "init", message });
      process.exit(3);
    }
    out({ kind: "ready" });
    return;
  }
  if (op === "prompt") {
    await runPrompt(parsed.id || "0", parsed.text ?? "");
    return;
  }
  if (op === "shutdown") {
    await shutdown();
    process.exit(0);
  }
  out({ kind: "error", message: `Unknown op: ${String(op)}` });
}

async function main() {
  const rl = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  const onSignal = async () => {
    await shutdown();
    process.exit(0);
  };
  process.on("SIGTERM", onSignal);
  process.on("SIGINT", onSignal);

  try {
    for await (const line of rl) {
      await handleLine(line);
    }
  } finally {
    await shutdown();
  }
}

main().catch(async (err) => {
  out({ kind: "error", stage: "fatal", message: err?.message ?? String(err) });
  await shutdown();
  process.exit(1);
});
