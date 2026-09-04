import { useQuery } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { ADMIN_PEOPLE_KEY, type AdminPerson, adminApi } from "@/lib/admin-api";

import { displayName } from "./identity";
import { PersonRow } from "./PersonRow";

/**
 * The list of people, filtered and searchable (FEAT-088).
 *
 * The filter opens on **Pending** whenever anything is pending: a waiting
 * request is the only reason this tab is time-sensitive, and it is the one
 * thing the admin came to do. With nothing waiting the list opens on everyone.
 *
 * "No access" is a filter rather than a badge because the useful question after
 * approving someone is which of them still cannot reach anything — a person
 * approved and then forgotten is indistinguishable from a person who was never
 * approved, from their side.
 */

type Filter = "all" | "pending" | "blocked" | "no-access";

function hasNoAccess(person: AdminPerson): boolean {
  return !person.is_admin && !person.servers.some((g) => g.permission);
}

function matches(person: AdminPerson, filter: Filter): boolean {
  switch (filter) {
    case "pending":
      return person.role === "pending";
    case "blocked":
      return person.role === "blocked";
    case "no-access":
      return hasNoAccess(person);
    default:
      return true;
  }
}

export function PeopleList({
  people,
  isLoading,
  initialUserId,
}: {
  people: AdminPerson[];
  isLoading: boolean;
  /** Deep link from a server card: open this person's panel on arrival. */
  initialUserId?: number;
}) {
  const pendingCount = people.filter((p) => p.role === "pending").length;
  const [filter, setFilter] = useState<Filter | null>(null);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<number | null>(initialUserId ?? null);

  // Null until the admin picks one, so the default can follow the data as it
  // loads rather than freezing whatever was true on the first render.
  const active: Filter = filter ?? (pendingCount > 0 ? "pending" : "all");

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return people.filter((person) => {
      if (!matches(person, active)) return false;
      if (!needle) return true;
      return (
        displayName(person).toLowerCase().includes(needle) ||
        person.username.toLowerCase().includes(needle) ||
        String(person.user_id).includes(needle)
      );
    });
  }, [people, active, search]);

  const tabs: { key: Filter; label: string }[] = [
    { key: "all", label: `All ${people.length}` },
    { key: "pending", label: `Pending ${pendingCount}` },
    { key: "blocked", label: "Blocked" },
    { key: "no-access", label: "No access" },
  ];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="name, @handle or id"
            aria-label="Search people"
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-1.5 pl-8 pr-3 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none"
          />
        </div>
        <div className="flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setFilter(t.key)}
              aria-pressed={active === t.key}
              className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                active === t.key
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
          {people.length === 0 ? "No one is registered yet." : "Nobody matches."}
        </p>
      ) : (
        <div className="space-y-2">
          {shown.map((person) => (
            <PersonRow
              key={person.user_id}
              person={person}
              expanded={expanded === person.user_id}
              onToggle={() =>
                setExpanded(expanded === person.user_id ? null : person.user_id)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** The list, fetching its own data — the shape the Admin tab composes. */
export function ConnectedPeopleList({ initialUserId }: { initialUserId?: number }) {
  const { data: people = [], isLoading } = useQuery({
    queryKey: ADMIN_PEOPLE_KEY,
    queryFn: adminApi.getPeople,
    retry: false,
  });

  return (
    <PeopleList
      people={people}
      isLoading={isLoading}
      initialUserId={initialUserId}
    />
  );
}
