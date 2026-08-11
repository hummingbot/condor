import { Component, type ErrorInfo, type ReactNode } from "react";

import { SERVER_KEY } from "@/lib/auth";
import { areaForRoute, buildDiagnostics } from "@/lib/diagnostics";
import { openIssueDraft } from "@/lib/github-issue";

function isChunkLoadError(error: Error): boolean {
  const msg = error.message || "";
  return (
    msg.includes("Failed to fetch dynamically imported module") ||
    msg.includes("Importing a module script failed") ||
    msg.includes("Loading chunk") ||
    msg.includes("Loading CSS chunk")
  );
}

interface Props {
  children: ReactNode;
  resetKey?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
  /** React's component stack — the most useful half of a crash report. */
  componentStack: string | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, componentStack: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, componentStack: null };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
    this.setState({ componentStack: info.componentStack ?? null });

    // Auto-reload once on chunk/module import failures (stale deploys)
    if (isChunkLoadError(error)) {
      const key = "ErrorBoundary_lastReload";
      const last = Number(sessionStorage.getItem(key) || 0);
      if (Date.now() - last > 10_000) {
        sessionStorage.setItem(key, String(Date.now()));
        window.location.reload();
      }
    }
  }

  componentDidUpdate(prevProps: Props) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, error: null, componentStack: null });
    }
  }

  /**
   * File the crash the boundary just caught.
   *
   * The message and component stack are the report — asking the user to
   * retype them is asking for a worse one. Read from `window`/`localStorage`
   * rather than router and server context: a class component that renders
   * *because* the tree below it failed is the wrong place to add hooks.
   */
  private reportCrash = () => {
    const error = this.state.error;
    const route = window.location.pathname + window.location.search;
    const stack = [
      error?.stack || `${error?.name}: ${error?.message}`,
      this.state.componentStack ? `\n\nComponent stack:${this.state.componentStack}` : "",
    ].join("");

    openIssueDraft({
      kind: "bug",
      title: `Crash: ${(error?.message || "unknown error").slice(0, 80)}`,
      area: areaForRoute(route),
      description:
        "The dashboard crashed and showed the \"Something went wrong\" screen.\n\n" +
        `Error: ${error?.message || "unknown"}`,
      // The boundary knows the route, not the clicks that led there — the one
      // thing it cannot fill in is left for the reporter to write.
      detail: "1. \n2. \n3. ",
      logs: "```\n" + stack + "\n```",
      diagnostics: buildDiagnostics({
        kind: "bug",
        route,
        server: localStorage.getItem(SERVER_KEY),
        env: null,
      }),
    });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full items-center justify-center">
          <div className="w-full max-w-md rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-6 text-center">
            <h2 className="mb-2 text-lg font-semibold text-[var(--color-red)]">
              Something went wrong
            </h2>
            <p className="mb-4 text-sm text-[var(--color-text-muted)]">
              {this.state.error?.message || "An unexpected error occurred."}
            </p>
            <button
              onClick={() => {
                if (this.state.error && isChunkLoadError(this.state.error)) {
                  window.location.reload();
                } else {
                  this.setState({ hasError: false, error: null, componentStack: null });
                }
              }}
              className="rounded-md bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
            >
              {this.state.error && isChunkLoadError(this.state.error) ? "Reload" : "Try Again"}
            </button>
            {this.state.error && !isChunkLoadError(this.state.error) && (
              <button
                onClick={this.reportCrash}
                className="ml-2 rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Report this
              </button>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
