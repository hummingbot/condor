import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

/**
 * The routine library, full screen.
 *
 * There is one library now, not two. The page used to be a launcher — filter
 * chips over a card grid over a strip of runs — whose every card opened the
 * *actual* library as a full-screen overlay, so the same list existed twice and
 * the first copy was only ever a door to the second. FEAT-077 then took the
 * page out of the nav and the library moved beside the conversation, which left
 * the people who worked from the page with nothing to open.
 *
 * So the page is the browser: the tab is back in the nav and it lands straight
 * on the full-screen view — agent bubbles and routine cards down the left, the
 * report and its Run / Config / Schedule controls filling the rest. The same
 * component, in the same arrangement, opens from an agent's page and beside a
 * chat; this route is just the widest door to it.
 *
 * `?agent=<slug>` still scopes the list, which is what the agent pages link
 * with; `?routine=<name>` opens straight onto one, and `?report=<id>` /
 * `?run=<id>` onto exactly what was on screen — which is what maximizing the
 * chat's routine pane hands over, so the page opens where the pane left off.
 */
export function Routines() {
  const { server } = useServer();
  const [searchParams] = useSearchParams();
  const agentParam = searchParams.get("agent");
  const routineParam = searchParams.get("routine");
  const reportParam = searchParams.get("report");
  const runParam = searchParams.get("run");

  // The browser reads these; the page owns the fetch because the run strip in
  // its sidebar has to see runs the browser never opened.
  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    refetchInterval: 5000,
  });

  if (!server) {
    return (
      <div className="p-6">
        <NoServerCard message="Select a server from the sidebar to view routines." />
      </div>
    );
  }

  return (
    <ReportBrowser
      page
      instances={instances}
      initialSource={routineParam ?? undefined}
      initialReportId={reportParam ?? undefined}
      initialInstanceId={runParam ?? undefined}
      initialSourceTypeFilter={agentParam || "all"}
    />
  );
}
