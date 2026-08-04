import { useEffect, useRef } from "react";

import { useTheme } from "@/hooks/useTheme";

/**
 * A rendered report, as its author wrote it.
 *
 * Reports are standalone HTML documents on disk, so they are shown in a sandboxed
 * iframe rather than parsed into components. The one thing the host tells them is
 * which theme it is in — reports listen for `set-theme` and restyle themselves,
 * which is why the message is re-sent on every load and every theme flip.
 */
export function ReportFrame({
  filename,
  title,
  className = "",
}: {
  filename: string;
  title: string;
  className?: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    const sendTheme = () => {
      iframe.contentWindow?.postMessage({ type: "set-theme", theme }, "*");
    };
    iframe.addEventListener("load", sendTheme);
    sendTheme();
    return () => iframe.removeEventListener("load", sendTheme);
  }, [theme, filename]);

  return (
    <iframe
      ref={iframeRef}
      src={`/reports/${filename}`}
      className={`h-full w-full border-0 ${className}`}
      title={title}
      sandbox="allow-scripts allow-popups allow-downloads"
    />
  );
}
