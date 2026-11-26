// Dev-only living style guide for the components in components/ui/.
// Mounted at /_design only when import.meta.env.DEV — never in a build.
import { useState, type ReactNode } from "react";
import {
  Activity,
  Bell,
  BarChart3,
  Code,
  Inbox,
  KeyRound,
  Layers,
  Play,
  PlayCircle,
  Plus,
  Search,
  Settings2,
  Trash2,
  Wallet,
} from "lucide-react";

import { Badge, StatusDot } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, SectionLabel } from "@/components/ui/Card";
import { EmptyState, Skeleton, Spinner } from "@/components/ui/EmptyState";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Stat } from "@/components/ui/Stat";
import { Tabs } from "@/components/ui/Tabs";
import { useTheme, type Theme } from "@/hooks/useTheme";

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="space-y-4">
      <div>
        <p className="eyebrow mb-2">{title}</p>
        {note && <p className="text-sm text-[var(--color-text-muted)]">{note}</p>}
      </div>
      {children}
    </section>
  );
}

const SWATCHES = [
  "--condor-gold",
  "--condor-gold-deep",
  "--hb-teal",
  "--hb-magenta",
  "--condor-orange",
  "--color-bg",
  "--color-surface",
  "--color-surface-hover",
  "--color-border",
  "--color-text",
  "--color-text-muted",
  "--color-primary",
  "--color-green",
  "--color-red",
  "--color-yellow",
];

export function DesignSystem() {
  const { theme, setTheme, colorblind, setColorblind } = useTheme();
  const [range, setRange] = useState("1W");
  const [scope, setScope] = useState("running");
  const [tab, setTab] = useState("assets");

  return (
    <div className="mx-auto max-w-5xl space-y-10 pb-24">
      <PageHeader
        eyebrow="Hummingbot design system"
        title="Condor foundations"
        description="Tokens and components from hummingbot/design-system, adapted to Condor. Every screen should be built from these pieces."
        actions={
          <div className="flex items-center gap-2">
            <SegmentedControl<Theme>
              aria-label="Theme"
              value={theme}
              onChange={setTheme}
              options={[
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark" },
              ]}
            />
            <Button variant={colorblind ? "soft" : "ghost"} size="sm" onClick={() => setColorblind(!colorblind)}>
              Colour-blind {colorblind ? "on" : "off"}
            </Button>
          </div>
        }
      />

      <Section title="Colour tokens" note="Use var(--token), never Tailwind palette colours like bg-purple-100.">
        <div className="grid grid-cols-5 gap-3">
          {SWATCHES.map((v) => (
            <div key={v} className="space-y-1">
              <div
                className="h-12 rounded-md border border-[var(--color-border)]"
                style={{ background: `var(${v})` }}
              />
              <code className="text-2xs text-[var(--color-text-muted)]">{v}</code>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type scale" note="11px minimum; text-[8–10px] is retired. The mono gold eyebrow is the DS signature — use it above page and section titles.">
        <Card className="space-y-2">
          {[
            ["text-2xl", "24 · KPI value", "$235.95"],
            ["text-xl", "20 · Page title", "Portfolio"],
            ["text-lg", "18 · Section title", "Top holdings"],
            ["text-sm", "14 · Body", "Pools Condor trades through Gateway."],
            ["text-md", "13 · UI default", "Controls, inputs, panel text"],
            ["text-xs", "12 · Secondary", "11 assets · 1 connector"],
            ["eyebrow", "11 · Eyebrow (mono)", "Hummingbot design system"],
            ["text-2xs uppercase tracking-wider font-semibold", "11 · Meta / table head", "Net PnL"],
          ].map(([cls, meta, sample]) => (
            <div key={meta} className="flex items-baseline gap-6 border-b border-[var(--color-border)] pb-2 last:border-0">
              <code className="w-36 shrink-0 text-2xs text-[var(--color-text-muted)]">{meta}</code>
              <span className={`${cls} text-[var(--color-text)]`}>{sample}</span>
            </div>
          ))}
        </Card>
      </Section>

      <Section title="Buttons" note="One primary per view. Text on gold is --on-primary (white fails contrast).">
        <Card className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary"><Play /> Run</Button>
            <Button variant="secondary">Schedule</Button>
            <Button variant="soft"><Plus /> Add server</Button>
            <Button variant="ghost"><Code /> Source</Button>
            <Button variant="danger"><Trash2 /> Delete</Button>
            <Button variant="primary" disabled>Disabled</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm">Small</Button>
            <Button variant="primary" size="md">Medium</Button>
            <Button variant="primary" size="lg"><PlayCircle /> Large</Button>
            <Button variant="ghost" size="icon" aria-label="Settings"><Settings2 /></Button>
            <Button variant="secondary" size="icon" aria-label="Notifications"><Bell /></Button>
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-dashed border-[var(--color-border)] p-2">
            <span className="mr-2 text-2xs text-[var(--color-text-muted)]">Toolbar example:</span>
            <Button variant="ghost" size="sm"><Code /> Source</Button>
            <Button variant="soft" size="sm"><Settings2 /> Config</Button>
            <Button variant="ghost" size="sm"><Bell /> Notify</Button>
            <Button variant="primary" size="sm"><Play /> Run</Button>
            <Button variant="secondary" size="sm">Schedule</Button>
          </div>
        </Card>
      </Section>

      <Section title="Segmented control vs. Tabs" note="Segmented filters a view in place. Tabs switch between whole panels.">
        <Card className="space-y-5">
          <div className="flex flex-wrap items-center gap-4">
            <SegmentedControl
              value={range}
              onChange={setRange}
              options={["1D", "1W", "1M", "3M"].map((v) => ({ value: v, label: v }))}
              size="sm"
            />
            <SegmentedControl
              value={scope}
              onChange={setScope}
              options={[
                { value: "running", label: "Running", icon: <Activity />, count: 2 },
                { value: "terminated", label: "Terminated", count: 14 },
              ]}
            />
          </div>
          <div>
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                { value: "assets", label: "Assets", icon: <Wallet />, count: 11 },
                { value: "positions", label: "Positions", icon: <Layers />, count: 0 },
                { value: "history", label: "History" },
              ]}
            />
            <p className="pt-3 text-md text-[var(--color-text-muted)]">Panel content for “{tab}”.</p>
          </div>
        </Card>
      </Section>

      <Section title="Stats (KPI)" note="Delta colour comes from the sign; a delta that rounds to zero is neutral, never red “-$0.00”.">
        <Card padded={false} className="grid grid-cols-4 divide-x divide-[var(--color-border)]">
          <Stat className="p-6" icon={<Wallet />} label="Portfolio" value="$235.95" sub="11 assets · 1 connector" />
          <Stat className="p-6" icon={<Activity />} label="Bots" value="2" delta={-0.001} formatDelta={(n) => `$${n.toFixed(2)}`} sub="2 controllers · vol $496" />
          <Stat className="p-6" icon={<BarChart3 />} label="Net PnL" value="−$1.99" delta={-1.99} formatDelta={(n) => `$${n.toFixed(2)}`} sub="−$0.01/hr" />
          <Stat className="p-6" icon={<Layers />} label="Agents" value="1" delta={4.2} formatDelta={(n) => `$${n.toFixed(2)}`} sub="12 total · 2 sessions" />
        </Card>
      </Section>

      <Section title="Badges & status">
        <Card className="flex flex-wrap items-center gap-2">
          <Badge>Owner</Badge>
          <Badge tone="primary">Default</Badge>
          <Badge tone="success" dot>Running</Badge>
          <Badge tone="danger" dot>Error</Badge>
          <Badge tone="warning">Insufficient balance · 5</Badge>
          <span className="ml-4 flex items-center gap-2 text-md"><StatusDot tone="success" pulse /> live</span>
          <span className="flex items-center gap-2 text-md"><StatusDot /> idle</span>
        </Card>
      </Section>

      <Section title="Forms" note="Labels left-aligned and short; explanations go in the hint under the control.">
        <Card>
          <CardHeader eyebrow="Backtest chart" title="Configuration" description="Run a backtest and chart entries, exits and PnL." />
          <div className="grid grid-cols-3 gap-4">
            <Field label="Controller config">
              <Select defaultValue="ema"><option value="ema">ema_cross_a</option></Select>
            </Field>
            <Field label="Start date" hint="YYYY-MM-DD">
              <Input defaultValue="2025-04-22" />
            </Field>
            <Field label="End date" hint="YYYY-MM-DD">
              <Input defaultValue="2025-04-23" />
            </Field>
            <Field label="Candle resolution" hint="1m, 5m, 15m or 1h">
              <Input defaultValue="1m" />
            </Field>
            <Field label="Trade cost" hint="As a decimal: 0.0002 = 0.02%">
              <Input defaultValue="0.0002" inputMode="decimal" />
            </Field>
            <Field label="Saved backtest ID" hint="Render a saved backtest instead of running a new one" error={undefined}>
              <Input placeholder="Optional" />
            </Field>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <Field label="Search" className="w-64"><Input icon={<Search />} placeholder="Search markets…" /></Field>
          </div>
        </Card>
      </Section>

      <Section title="Cards & headers">
        <div className="grid grid-cols-2 gap-4">
          <Card>
            <CardHeader
              icon={<BarChart3 />}
              title="Portfolio evolution"
              description="Value over the selected range"
              actions={<SegmentedControl size="sm" value={range} onChange={setRange} options={["1D", "1W", "1M"].map((v) => ({ value: v, label: v }))} />}
            />
            <Skeleton className="h-32 w-full" />
          </Card>
          <Card>
            <SectionLabel className="mb-2">Compact page header</SectionLabel>
            <PageHeader compact eyebrow="Bots" title="Your bots" description="2 controllers" actions={<Button size="sm" variant="primary">Deploy bot</Button>} />
          </Card>
        </div>
      </Section>

      <Section title="Empty & loading states" note="Replace bare “Loading...” text with skeletons; empty states say what is missing and how to fix it.">
        <div className="grid grid-cols-3 gap-4">
          <Card padded={false}>
            <EmptyState icon={<Inbox />} title="No performance history yet" description="It appears after the bot's first closed position." compact />
          </Card>
          <Card padded={false}>
            <EmptyState icon={<KeyRound />} title="View only" description="Add API keys for Binance Perp to trade here." action={<Button variant="primary" size="sm">Add API keys</Button>} compact />
          </Card>
          <Card className="space-y-2">
            <Spinner label="Loading pools…" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-2/3" />
          </Card>
        </div>
      </Section>
    </div>
  );
}
