import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Circle } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

export function BotDetail() {
  const { id } = useParams<{ id: string }>();
  const { server } = useServer();

  const { data, isLoading, error } = useQuery({
    queryKey: ["bot", server, id],
    queryFn: () => api.getBot(server!, id!),
    enabled: !!server && !!id,
    refetchInterval: 10000,
  });

  if (!server || !id) return null;
  if (isLoading) return <p className="text-[var(--color-text-muted)]">Loading...</p>;
  if (error)
    return (
      <p className="text-[var(--color-red)]">
        {error instanceof Error ? error.message : "Error"}
      </p>
    );
  if (!data) return null;

  const { bot, config, performance } = data;
  const statusColor =
    bot.status === "running"
      ? "text-[var(--color-green)]"
      : "text-[var(--color-red)]";

  return (
    <div>
      <Link
        to="/bots"
        className="mb-4 inline-flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to bots
      </Link>

      <div className="mb-6 flex items-center gap-3">
        <h2 className="text-xl font-bold">{bot.name}</h2>
        <span className={`flex items-center gap-1.5 text-sm ${statusColor}`}>
          <Circle className="h-2 w-2 fill-current" />
          {bot.status}
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Config */}
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 font-medium text-[var(--color-text-muted)]">
            Configuration
          </h3>
          {Object.keys(config).length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              No config available
            </p>
          ) : (
            <dl className="space-y-2 text-sm">
              {Object.entries(config).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <dt className="text-[var(--color-text-muted)]">{k}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        {/* Performance */}
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 font-medium text-[var(--color-text-muted)]">
            Performance
          </h3>
          {Object.keys(performance).length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              No performance data
            </p>
          ) : (
            <dl className="space-y-2 text-sm">
              {Object.entries(performance).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <dt className="text-[var(--color-text-muted)]">{k}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    </div>
  );
}
