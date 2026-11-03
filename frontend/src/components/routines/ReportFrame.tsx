import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { useTheme } from "@/hooks/useTheme";
import { api } from "@/lib/api";

/**
 * A rendered report, as its author wrote it.
 *
 * Reports are standalone HTML documents, so they are shown in a sandboxed iframe
 * rather than parsed into components. The body is authenticated (SEC-112) and an
 * iframe `src` cannot carry an Authorization header, so the HTML is fetched with
 * the token in a header and handed to the frame as `srcDoc`. Without
 * `allow-same-origin` the frame stays in an opaque origin, so report scripts
 * cannot reach the dashboard's `localStorage` (and thus the JWT).
 *
 * A chart report does not carry plotly.js: it references the one shared copy at
 * `/api/v1/reports/assets/` (PERF-267). A `srcdoc` document's base URL is the
 * parent's and a classic script is a no-cors subresource, so the opaque-origin
 * frame resolves it against the dashboard's own origin — and the browser caches
 * it once instead of re-parsing 4.85 MB on every report open. Only the paths
 * that leave the origin (Download, Telegram) inline the bundle back in.
 *
 * The one thing the host tells a report is which theme it is in — reports listen
 * for `set-theme` and restyle themselves, which is why the message is re-sent on
 * every load and every theme flip. The theme is the dashboard's, always: a report
 * is a document inside the app, not a surface with a mode of its own (READ-274).
 */
export function ReportFrame({
  reportId,
  title,
  className = "",
}: {
  reportId: string;
  title: string;
  className?: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const { theme: appTheme } = useTheme();
  // The app has three themes, the report protocol has two. Colorblind is a
  // dark-based palette, so it reads as dark inside the frame too — which is
  // also what the report side assumes: anything but `light` is dark.
  const theme = appTheme === "light" ? "light" : "dark";

  const { data: html, isLoading, isError, error } = useQuery({
    queryKey: ["report-html", reportId],
    queryFn: () => api.getReportHtml(reportId),
  });

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    const sendTheme = () => {
      iframe.contentWindow?.postMessage({ type: "set-theme", theme }, "*");
    };
    iframe.addEventListener("load", sendTheme);
    sendTheme();
    return () => iframe.removeEventListener("load", sendTheme);
  }, [theme, html]);

  if (isLoading) {
    return (
      <div
        className={`flex h-full w-full items-center justify-center ${className}`}
      >
        <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
      </div>
    );
  }

  if (isError || html === undefined) {
    return (
      <div
        className={`flex h-full w-full items-center justify-center px-6 text-center text-xs text-[var(--color-red)] ${className}`}
      >
        {(error as Error)?.message ?? "Failed to load report"}
      </div>
    );
  }

  return (
    <iframe
      ref={iframeRef}
      srcDoc={html}
      className={`h-full w-full border-0 ${className}`}
      title={title}
      sandbox="allow-scripts allow-popups allow-downloads"
    />
  );
}
