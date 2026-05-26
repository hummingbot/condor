import { type RefObject, useEffect } from "react";

import { colorizeReportDocument } from "@/lib/colorize-report";

/** Apply sentiment colors inside a same-origin report iframe after load. */
export function useColorizeReportIframe(
  iframeRef: RefObject<HTMLIFrameElement | null>,
  reportKey: string | undefined,
) {
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe || !reportKey) return;

    const apply = () => {
      const doc = iframe.contentDocument;
      if (doc) colorizeReportDocument(doc);
    };

    iframe.addEventListener("load", apply);
    if (iframe.contentDocument?.readyState === "complete") {
      apply();
    }

    return () => iframe.removeEventListener("load", apply);
  }, [iframeRef, reportKey]);
}
