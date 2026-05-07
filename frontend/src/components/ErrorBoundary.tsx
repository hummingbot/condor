import { Component, type ErrorInfo, type ReactNode } from "react";

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
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);

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
      this.setState({ hasError: false, error: null });
    }
  }

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
                  this.setState({ hasError: false, error: null });
                }
              }}
              className="rounded-md bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
            >
              {this.state.error && isChunkLoadError(this.state.error) ? "Reload" : "Try Again"}
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
