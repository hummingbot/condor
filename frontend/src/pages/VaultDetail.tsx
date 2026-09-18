/**
 * One vault: a header that says what it is, and four tabs.
 *
 * The header carries the two facts a reader needs before anything else — which
 * phase it is in, and what the chain says its state is — because every action
 * below means something different depending on them. While a vault is private
 * its creator moves assets in and out through the delegate they installed; once
 * it is tokenized nobody can, and the only way out is a wind-down followed by
 * holders redeeming.
 *
 * The tabs are Summary (what it holds), Strategy (what it runs), Activity (what
 * it did) and Token (whether anyone else is in it, and how to let them be).
 * Tokenizing lives in the last of those rather than in the header, because it
 * is one way and a one-way action does not belong next to Pause.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { isTokenized, supplyShare } from "@/components/vaults/format";
import {
  CopyAddress,
  PhaseBadge,
  StateBadge,
  SignerPrompt,
  WalletGate,
} from "@/components/vaults/shared";
import { AddressAvatar } from "@/components/wallet/primitives";
import { VaultPortfolio } from "@/components/vaults/VaultPortfolio";
import { useCanSign } from "@/hooks/useCanSign";
import { useServer } from "@/hooks/useServer";
import { api, type VaultBuild, type VaultInfo } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

// Summary, Portfolio and Activity are public: a vault is an account on a
// public chain, and what it is, what it holds and what it has done are the
// three things anyone looking at one wants. Agent and Token stay behind the
// gate — the first shows a config only its creator may read, and every control
// on the second is a signature.
const PUBLIC_TABS = ["Summary", "Portfolio", "Activity"] as const;
const CREATOR_TABS = ["Agent", "Token"] as const;
type Tab = (typeof PUBLIC_TABS)[number] | (typeof CREATOR_TABS)[number];

/** What a vault may launch against. Both are what Meteora's own keepers
 *  migrate, and the program's fixed graduation threshold is denominated in the
 *  quote asset — so this is a short list on purpose, not a token picker. */
const WSOL = "So11111111111111111111111111111111111111112";
const QUOTE_ASSETS = [
  { symbol: "SOL", mint: WSOL },
  { symbol: "USDC", mint: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" },
];

export function VaultDetail() {
  const { account = "" } = useParams();
  const { server } = useServer();
  const queryClient = useQueryClient();
  const { signAndSubmit } = useWallet();
  const { canSign } = useCanSign();
  const [tab, setTab] = useState<Tab>("Summary");
  // The creator's two tabs are listed for everyone: behind the gate they say
  // whose key is missing, which is more use than a tab that is not there.
  const tabs: Tab[] = [...PUBLIC_TABS, ...CREATOR_TABS];
  const [error, setError] = useState<string | null>(null);

  const vaults = useQuery({
    queryKey: ["vaults", server],
    queryFn: () => api.listVaults(server!),
    enabled: !!server,
    retry: false,
  });
  const vault = vaults.data?.find((v) => v.account === account);

  /** Every action on this page is the same shape: build, sign, submit, refetch.
   *  Some also have something to tell Condor once the signature is on chain —
   *  which version was published, which launch config now exists — and that is
   *  `confirm`, run only after the submit returns. */
  const act = useMutation({
    mutationFn: async (step: Step) => {
      setError(null);
      const prepared = await step.build();
      const signature = await signAndSubmit(
        server!,
        prepared,
        vault?.network ?? "mainnet-beta",
      );
      if (step.confirm) await step.confirm(signature);
      return signature;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vaults", server] }),
    onError: (e: Error) => setError(e.message),
  });

  if (!server) return <NoServerCard message="Pick the server this vault runs on." />;
  if (vaults.isLoading) {
    return <p className="p-6 text-[12px] text-[var(--color-text-muted)]">Reading the chain…</p>;
  }
  if (!vault) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <p className="text-sm">No vault <code className="font-mono">{account}</code> on this server.</p>
        <Link to="/vaults" className="text-[12px] underline">
          Back to vaults
        </Link>
      </div>
    );
  }

  const chain = vault.chain;
  const tokenized = isTokenized(vault);
  const running = chain?.state === "Running";
  const finished = chain?.state === "Redeemable";
  const windingDown = chain?.state === "WindingDown";

  return (
    <div className="mx-auto max-w-4xl">
      <Link
        to="/vaults"
        className="mb-4 inline-flex items-center gap-1 text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-3 w-3" />
        Vaults
      </Link>

      <header className="mb-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <AddressAvatar address={vault.account} className="h-10 w-10" />
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold">{vault.label || "Untitled vault"}</h1>
              {/* Labelled, because a vault has two addresses on this page and
                  an unlabelled one is read as whichever the reader expected.
                  This is the account the chain knows the vault by; the wallet
                  that holds the money is a different address, derived from it,
                  and Portfolio says so where it shows it. */}
              <span className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)]">
                Vault account
                <CopyAddress address={vault.account} label="Vault account" />
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <PhaseBadge vault={vault} />
            <StateBadge vault={vault} />
          </div>
        </div>

        {vault.drift && (
          <p className="mb-3 flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
            <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
            {vault.drift}. Condor will not run it until the record and the chain agree.
          </p>
        )}

        {/* Every button below is signed by the creator's key, so none of them is
            offered while the browser cannot produce it. The prompt takes the
            row's place rather than sitting beside disabled buttons: the thing
            to do next is connect, and that is the only control here — a button
            whose only outcome is "connect a wallet first" in red is not one. */}
        {!canSign && <SignerPrompt />}

        {canSign && (
        <div className="flex flex-wrap gap-2">
          {chain && !windingDown && !finished && (
            <Action
              onClick={() =>
                act.mutate({
                  build: () =>
                    api.buildVaultAction(account, "set-active", { active: !running }),
                })
              }
              pending={act.isPending}
            >
              {running ? "Pause" : "Resume"}
            </Action>
          )}
          {tokenized && (
            <Action
              onClick={() => act.mutate({ build: () => api.buildVaultAction(account, "wind-down") })}
              pending={act.isPending}
              tone="danger"
              confirm="Irreversible. The strategy stops for good and holders redeem what the vault holds."
              hidden={windingDown || finished}
            >
              Wind down
            </Action>
          )}
          {/* No "stop for good" on a private vault: a wind-down exists to pay
              holders, and there are none. Pausing stops the strategy, and the
              assets come out through the delegate whenever the creator likes —
              so an irreversible button offered nothing that Pause and Transfer
              did not already do, at the price of never being able to run this
              vault again. */}
        </div>
        )}

        {error && (
          <p className="mt-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
      </header>

      <nav className="mb-3 flex gap-1 border-b border-[var(--color-border)]">
        {tabs.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={`px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === name
                ? "border-b-2 border-[var(--color-accent)] text-[var(--color-text)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            {name}
          </button>
        ))}
      </nav>

      {/* Above the tabs, not inside one: once a vault is redeemable this is
          the only thing anyone comes to the page to do — and it is a signature,
          so it is the creator's. */}
      {finished && canSign && (
        <RedeemCard vault={vault} server={server} onAct={act.mutate} pending={act.isPending} />
      )}

      {tab === "Summary" && (
        <>
          <SummaryTab vault={vault} />
          {/* The one action that belongs beside the summary rather than in a
              tab: taking assets back out of a private vault. Offered only to
              the key that can sign it — see `useCanSign`. */}
          {canSign && !isTokenized(vault) && <WithdrawCard vault={vault} />}
        </>
      )}
      {tab === "Portfolio" && <VaultPortfolio vault={vault} />}
      {tab === "Activity" && <ActivityTab vault={vault} />}
      {(tab === "Agent" || tab === "Token") && (
        <WalletGate>
          {tab === "Agent" && (
            <StrategyTab vault={vault} onAct={act.mutate} pending={act.isPending} />
          )}
          {tab === "Token" && (
            <TokenTab vault={vault} onAct={act.mutate} pending={act.isPending} />
          )}
        </WalletGate>
      )}
    </div>
  );
}

/** One action: what to sign, and what to tell Condor once it landed. */
type Step = {
  build: () => Promise<VaultBuild>;
  confirm?: (signature: string) => Promise<unknown>;
};

type Act = (step: Step) => void;

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--color-border)] py-1.5 last:border-0">
      <dt className="text-[12px] text-[var(--color-text-muted)]">{label}</dt>
      <dd className="text-right text-[12px]">{children}</dd>
    </div>
  );
}

/**
 * What this vault *is*: its state, its terms, and the keys that matter.
 *
 * Public, and written to be read by someone who did not make it — a holder, or
 * anyone deciding whether to become one. So it leads with the two things that
 * decide what a vault even is: whether it is private or tokenized (one way, and
 * irreversible), and whether it is running. What it holds moved to Portfolio,
 * which can say it properly.
 */
function SummaryTab({ vault }: { vault: VaultInfo }) {
  const chain = vault.chain;
  const tokenized = isTokenized(vault);

  return (
    <>
      <Card title="Status">
        <dl>
          <Row label="Phase">
            {tokenized ? "Tokenized — it has holders" : "Private — its creator's money alone"}
          </Row>
          <Row label="State">{chain?.state ?? "—"}</Row>
          <Row label="Strategy version">{chain?.version ? `v${chain.version}` : "—"}</Row>
          <Row label="Created">{when(chain?.created_ts)}</Row>
          {!!chain?.tokenized_ts && <Row label="Tokenized">{when(chain.tokenized_ts)}</Row>}
          {!!chain?.wind_down_ts && <Row label="Wound down">{when(chain.wind_down_ts)}</Row>}
        </dl>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          {tokenized
            ? "Other people's money is in here. Nothing leaves but a redemption after a wind-down, and the strategy's config is committed on chain as a hash so a run can prove which one it executed."
            : "Private is a finished state, not an unfinished one. Its creator can take the money out at any time; tokenizing is the one-way step that ends that."}
        </p>
      </Card>

      <Card title="Keys">
        <dl>
          <Row label="Vault account — what the chain knows it by">
            <CopyAddress address={vault.account} label="Vault account" />
          </Row>
          <Row label="Wallet — fund it by sending here">
            <CopyAddress address={vault.treasury_address} label="Treasury" />
          </Row>
          <Row label="Creator — the key that manages it">
            <CopyAddress address={vault.creator_address} />
          </Row>
          <Row label="Delegate — the key that signs its trades">
            {chain?.delegate ? (
              <CopyAddress address={chain.delegate} />
            ) : (
              <span className="text-amber-600 dark:text-amber-400">none installed</span>
            )}
          </Row>
          <Row label="Quote asset">
            {vault.quote_mint ? <CopyAddress address={vault.quote_mint} /> : "—"}
          </Row>
        </dl>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          The vault account and the wallet are two addresses, not one: the first is the vault&rsquo;s
          identity and the permissions on it, and the second — derived from the first — is what
          actually holds the assets. Send funds to the wallet. Neither is a token mint; a vault only
          has one of those once it tokenizes.
        </p>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          While a delegate is installed it can move everything in this wallet. That is what lets it
          trade, and it is the custody risk for as long as it is there — the creator can replace it
          or remove it at any time.
          {!tokenized && (
            <>
              {" "}
              It is also how assets come back out while this vault is private: there is no withdraw
              instruction, because the delegate the creator installed can already do it.
            </>
          )}
        </p>
      </Card>

      <Card title={tokenized ? "Its token" : "If it tokenizes"}>
        <dl>
          {chain?.mint && (
            <Row label="Mint">
              <CopyAddress address={chain.mint} />
            </Row>
          )}
          {chain?.dbc_pool && (
            <Row label="Curve — where it was sold">
              <CopyAddress address={chain.dbc_pool} />
            </Row>
          )}
          {chain?.damm_pool && (
            <Row label="Pool — where it trades now">
              <CopyAddress address={chain.damm_pool} />
            </Row>
          )}
          <Row label="Offered on the curve">
            {supplyShare(chain?.circulating_supply, chain?.total_supply)}
          </Row>
          <Row label="Raised before graduation">
            {chain?.graduation_quote_threshold && chain.graduation_quote_threshold !== "0"
              ? chain.graduation_quote_threshold
              : "—"}
          </Row>
          <Row label="Creator's cut of trading fees">
            {chain?.creator_trading_fee_pct ? `${chain.creator_trading_fee_pct}%` : "—"}
          </Row>
        </dl>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          {tokenized
            ? "The raise split in two at graduation: the capital the strategy trades, and the liquidity holders exit through."
            : "These terms are fixed at tokenization and cannot be changed afterwards. Until then they are what this vault would launch on."}
        </p>
      </Card>
    </>
  );
}

/** A chain timestamp, or an em dash. Seconds, as the program writes them. */
function when(ts: number | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

/**
 * Taking assets back out of a private vault.
 *
 * Not an instruction: the delegate this vault's creator installed can already
 * move anything in the wallet, and this asks it to. It disappears at
 * tokenization — not because the key stops being able to, but because from
 * there the assets are other people's too and the only way out is a redemption
 * after a wind-down.
 */
function WithdrawCard({ vault }: { vault: VaultInfo }) {
  const queryClient = useQueryClient();
  const [destination, setDestination] = useState(vault.creator_address);
  const [amount, setAmount] = useState("");
  const [sent, setSent] = useState<string | null>(null);

  const send = useMutation({
    mutationFn: () =>
      api.withdrawFromVault(vault.account, { destination: destination.trim(), amount }),
    onSuccess: (result) => {
      setSent(result.signature);
      setAmount("");
      queryClient.invalidateQueries({ queryKey: ["vault-holdings", vault.account] });
    },
  });

  return (
    <Card title="Withdraw">
      <p className="mb-3 text-[12px] text-[var(--color-text-muted)]">
        This vault is private: the money in it is yours and you can take it out whenever you like.
        Once you launch a token you cannot, and neither can anyone else.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          inputMode="decimal"
          placeholder="0.0"
          className="w-28 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
        />
        <span className="text-[12px] text-[var(--color-text-muted)]">SOL to</span>
        <input
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          spellCheck={false}
          className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-[12px]"
        />
        <Action pending={send.isPending} disabled={!amount || !destination} onClick={() => send.mutate()}>
          Withdraw
        </Action>
      </div>
      {send.error && (
        <p className="mt-2 text-[11px] text-[var(--color-red)]">{(send.error as Error).message}</p>
      )}
      {sent && (
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          Sent — <span className="font-mono">{sent.slice(0, 16)}…</span>
        </p>
      )}
    </Card>
  );
}

function StrategyTab({
  vault,
  onAct,
  pending,
}: {
  vault: VaultInfo;
  onAct: Act;
  pending: boolean;
}) {
  const chain = vault.chain;
  const config = useQuery({
    queryKey: ["vault-config", vault.account],
    queryFn: () => api.getVaultConfig(vault.account),
    retry: false,
  });
  const scan = useMutation({ mutationFn: () => api.scanVault(vault.account) });
  const [draft, setDraft] = useState<string | null>(null);
  const pin = vault.pin as { agent_ref?: Record<string, string>; scan?: { passed: boolean; findings: string[] } } | null;

  return (
    <>
      <Card title="Pinned">
        <dl>
          <Row label="Agent">
            <span className="font-mono">
              {pin?.agent_ref?.agentSlug ?? pin?.agent_ref?.agent_slug ?? "—"}
            </span>
          </Row>
          <Row label="Strategy">
            <span className="font-mono">
              {pin?.agent_ref?.strategySlug ?? pin?.agent_ref?.strategy_slug ?? "—"}
            </span>
          </Row>
          <Row label="Version">v{chain?.version ?? 0}</Row>
          <Row label="Config hash — what the chain carries">
            <span className="font-mono text-[11px]">{chain?.config_hash?.slice(0, 16)}…</span>
          </Row>
        </dl>
      </Card>

      <Card title="Config">
        <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
          Private: only its sha256 is on chain. A run hashes what it is handed and compares, so
          nobody — Condor included — can run this vault on parameters you did not sign.
        </p>
        <textarea
          value={draft ?? JSON.stringify(config.data?.config ?? {}, null, 2)}
          onChange={(e) => setDraft(e.target.value)}
          rows={12}
          spellCheck={false}
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-[12px]"
        />
        <div className="mt-2 flex items-center gap-2">
          <Action
            pending={pending}
            onClick={() => {
              const next = JSON.parse(draft ?? "{}");
              onAct({
                build: () => api.buildVaultPublish(vault.account, { config: next }),
                confirm: (signature) =>
                  api.confirmVaultPublished(vault.account, signature),
              });
            }}
            disabled={draft === null}
          >
            Publish v{(chain?.version ?? 0) + 1}
          </Action>
          <button
            type="button"
            onClick={() => scan.mutate()}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-[12px]"
          >
            Run Condor&rsquo;s check
          </button>
        </div>
        {(scan.data || pin?.scan) && (
          <div className="mt-2 text-[11px]">
            {(scan.data ?? pin!.scan)!.passed ? (
              <p className="text-emerald-600 dark:text-emerald-400">
                Condor will run this version.
              </p>
            ) : (
              <>
                <p className="text-amber-600 dark:text-amber-400">
                  Condor will not run this version:
                </p>
                <ul className="ml-4 list-disc text-[var(--color-text-muted)]">
                  {(scan.data ?? pin!.scan)!.findings.map((finding) => (
                    <li key={finding}>{finding}</li>
                  ))}
                </ul>
              </>
            )}
            <p className="mt-1 text-[var(--color-text-muted)]">
              This is Condor&rsquo;s own decision about what its crank starts. Nothing about it is on
              chain, and it does not stop you running the vault yourself.
            </p>
          </div>
        )}
      </Card>
    </>
  );
}

function ActivityTab({ vault }: { vault: VaultInfo }) {
  return (
    <Card title="Activity">
      <p className="text-[12px] text-[var(--color-text-muted)]">
        Runs, open positions, and — once the vault is tokenized — every sweep and burn, read from
        the chain by signature. Nothing here is stored locally, so it is the same history anyone
        else can verify.
      </p>
      <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
        Created {vault.chain ? new Date(vault.chain.created_ts * 1000).toLocaleString() : "—"}.
      </p>
    </Card>
  );
}

function TokenTab({
  vault,
  onAct,
  pending,
}: {
  vault: VaultInfo;
  onAct: Act;
  pending: boolean;
}) {
  const chain = vault.chain;
  const [name, setName] = useState(vault.label);
  const [symbol, setSymbol] = useState("");
  const [uri, setUri] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  if (isTokenized(vault) && chain?.mint) {
    return (
      <Card title="Token">
        <dl>
          <Row label="Mint">
            <CopyAddress address={chain.mint} />
          </Row>
          <Row label="Curve pool">
            {chain.dbc_pool ? <CopyAddress address={chain.dbc_pool} /> : "—"}
          </Row>
          <Row label="Offered on the curve — circulating over total supply">
            {supplyShare(chain.circulating_supply, chain.total_supply)}
          </Row>
          <Row label="Raised before graduation">
            {chain.graduation_quote_threshold === "0" ? "—" : chain.graduation_quote_threshold}
          </Row>
          <Row label="Locked as liquidity">50% — the rest, less the 2% protocol fee, is capital</Row>
          <Row label="Creator's share of trading fees">{chain.creator_trading_fee_pct}%</Row>
          <Row label="Pool fee after graduation">
            {[25, 30, 100, 200, 400, 600][chain.pool_fee_option] ?? "—"} bps
          </Row>
        </dl>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          The raise split is the one number that changes what this vault is: a high share means a
          large strategy behind a thin market, a low one a small strategy behind a deep one.
          Neither is better, and it was chosen before anyone could buy.
        </p>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          The rest of the supply is the vault&rsquo;s retained supply. It is not circulating and does not
          dilute a redemption. It reaches the market only through the strategy: an LP position on
          this pool sells it as buyers arrive, and the quote they pay lands in the vault — so
          supply and capital move together, capped by what was never issued.
        </p>
        <a
          href={`https://jup.ag/swap/SOL-${chain.mint}`}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-1 text-[12px] underline"
        >
          Trade on Jupiter <ExternalLink className="h-3 w-3" />
        </a>
      </Card>
    );
  }

  return (
    <>
      <LaunchConfigCard vault={vault} onAct={onAct} pending={pending} />
      <Card title="Tokenize">
        <p className="mb-3 text-[12px] text-[var(--color-text-muted)]">
          Launching a token invites other people in. It is <strong>one way</strong>: from the moment
          it lands, nobody can take assets out of this vault — not you, not Condor, not anyone — and
          the only way they leave is a wind-down followed by holders redeeming pro-rata.
        </p>

        <div className="space-y-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Token name"
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[13px]"
          />
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="SYMBOL"
            maxLength={10}
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-[13px]"
          />
          <input
            value={uri}
            onChange={(e) => setUri(e.target.value)}
            placeholder="https://… metadata URI"
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[13px]"
          />
          <p className="text-[11px] text-[var(--color-text-muted)]">
            How much of the supply the curve sells is the config&rsquo;s to say, not this form&rsquo;s.
            The program reads it off the config and records it, so what a buyer sees as the dilution
            ceiling is the same number the curve is actually selling against.
          </p>
        </div>

        <label className="mt-4 flex items-start gap-2 text-[12px]">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="mt-0.5"
          />
          I understand that after this I can never take assets out of this vault.
        </label>

        <Action
          pending={pending}
          disabled={!confirmed || !symbol || !uri}
          tone="danger"
          onClick={() =>
            onAct({
              build: () =>
                api.buildVaultTokenize(vault.account, {
                  name: name.trim(),
                  symbol: symbol.trim(),
                  uri: uri.trim(),
                }),
            })
          }
        >
          Launch the token
        </Action>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          Launching uses the config above. Build and sign that first — its terms are what the
          program checks, and they are on the vault for a buyer to read before they buy.
        </p>
      </Card>
    </>
  );
}

/**
 * The launch terms, as a transaction of their own.
 *
 * A vault prices its launch off the assets it already holds, so each one needs
 * its own Meteora config — which is why the program checks a config's *terms*
 * rather than its address. The locked liquidity is the decision here: it is the
 * split between the strategy's capital and the depth holders exit through, and
 * a high one and a low one are different products rather than a right and a
 * wrong answer.
 */
function LaunchConfigCard({
  vault,
  onAct,
  pending,
}: {
  vault: VaultInfo;
  onAct: Act;
  pending: boolean;
}) {
  // The quote asset is chosen here because this config is what fixes it: the
  // program writes it onto the vault at tokenize and it never changes after.
  // wSOL by default, since that is what Meteora's keepers graduate on the
  // threshold the program requires.
  const [quoteMint, setQuoteMint] = useState(WSOL);
  const [initialCap, setInitialCap] = useState("10");
  const [graduationCap, setGraduationCap] = useState("100");
  const [creatorFee, setCreatorFee] = useState("50");
  const [retained, setRetained] = useState("0");
  const [poolFee, setPoolFee] = useState("2");

  // Half the raise is locked as liquidity on every launch — a constant in the
  // program, so there is nothing to type and nothing to get wrong.
  const caps = [Number(initialCap), Number(graduationCap)];
  const capsWrong = !caps.every(Number.isFinite) || caps[1] <= caps[0];

  return (
    <Card title="Launch config">
      <p className="mb-3 text-[12px] text-[var(--color-text-muted)]">
        One transaction, before the launch itself. These are the terms a buyer reads off the vault
        before they can buy, and the program refuses a launch on a config whose terms are outside
        what it allows.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Labelled
          label="Quote asset"
          hint="What the token sells for. It becomes the graduated pool's quote asset, what a wind-down converts into, and what a redemption pays — and it cannot be changed after launch."
        >
          <select
            value={quoteMint}
            onChange={(e) => setQuoteMint(e.target.value)}
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[13px]"
          >
            {QUOTE_ASSETS.map((asset) => (
              <option key={asset.mint} value={asset.mint}>
                {asset.symbol}
              </option>
            ))}
          </select>
        </Labelled>
        <Labelled label="Start value" hint="What the vault is worth per token at the start of the curve, in the quote asset. Its NAV is what this is priced against — you may strike it above or below.">
          <input
            value={initialCap}
            onChange={(e) => setInitialCap(e.target.value)}
            inputMode="decimal"
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
          />
        </Labelled>
        <Labelled label="End value" hint="Where the curve fills, in the quote asset. Must exceed the start.">
          <input
            value={graduationCap}
            onChange={(e) => setGraduationCap(e.target.value)}
            inputMode="decimal"
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
          />
        </Labelled>
        <Labelled
          label="Held back from the curve"
          hint="0–50%. Supply the curve may not sell. It lands in the treasury as retained supply the strategy can market-make with; what the curve does sell is what buyers read as the dilution ceiling."
        >
          <div className="flex items-center gap-2">
            <input
              value={retained}
              onChange={(e) => setRetained(e.target.value)}
              inputMode="decimal"
              className="w-24 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
            />
            <span className="text-[12px] text-[var(--color-text-muted)]">%</span>
          </div>
        </Labelled>
        <Labelled label="Your share of trading fees" hint="At most 50%. You may take less.">
          <div className="flex items-center gap-2">
            <input
              value={creatorFee}
              onChange={(e) => setCreatorFee(e.target.value)}
              inputMode="decimal"
              className="w-24 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
            />
            <span className="text-[12px] text-[var(--color-text-muted)]">%</span>
          </div>
        </Labelled>
        <Labelled label="Pool fee after graduation" hint="What the graduated pool charges.">
          <select
            value={poolFee}
            onChange={(e) => setPoolFee(e.target.value)}
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[13px]"
          >
            {[25, 30, 100, 200, 400, 600].map((bps, option) => (
              <option key={bps} value={option}>
                {bps} bps
              </option>
            ))}
          </select>
        </Labelled>
      </div>
      <Action
        pending={pending}
        disabled={capsWrong}
        onClick={() =>
          onAct({
            build: () =>
              api.buildVaultLaunchConfig(vault.account, {
                quote_mint: quoteMint,
                initial_market_cap: Number(initialCap),
                graduation_market_cap: Number(graduationCap),
                creator_trading_fee_percentage: Number(creatorFee),
                pool_fee_option: Number(poolFee),
                retained_supply_pct: Number(retained),
              }),
            // Condor remembers the address so the launch form never asks for it.
            confirm: (signature) => api.confirmVaultLaunchConfig(vault.account, signature),
          })
        }
      >
        Create the config
      </Action>
    </Card>
  );
}

function Labelled({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-[12px] font-medium">{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}

/**
 * Redemption: burn tokens, take the quote asset pro-rata.
 *
 * Shown to anyone once a vault is `Redeemable`, not just its creator — a holder
 * is whoever has the token. The payment comes out of an account the program
 * owns, so nothing in the path can refuse it; this form only asks.
 */
function RedeemCard({
  vault,
  server,
  onAct,
  pending,
}: {
  vault: VaultInfo;
  server: string;
  onAct: Act;
  pending: boolean;
}) {
  const [amount, setAmount] = useState("");

  return (
    <Card title="Redeem">
      <p className="mb-3 text-[12px] text-[var(--color-text-muted)]">
        This vault has wound down. Burning tokens pays out its quote asset in proportion — the
        retained supply and the pool&rsquo;s own balance are not counted, because neither was ever issued.
      </p>
      <div className="flex items-center gap-2">
        <input
          value={amount}
          onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ""))}
          inputMode="numeric"
          placeholder="amount, in raw token units"
          className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
        />
        <Action
          pending={pending}
          disabled={!amount}
          onClick={() =>
            onAct({ build: () => api.buildVaultRedeem(server, vault.account, amount) })
          }
        >
          Burn and redeem
        </Action>
      </div>
    </Card>
  );
}

function Action({
  children,
  onClick,
  pending,
  disabled,
  tone = "normal",
  confirm,
  hidden,
}: {
  children: React.ReactNode;
  onClick: () => void;
  pending?: boolean;
  disabled?: boolean;
  tone?: "normal" | "danger";
  confirm?: string;
  hidden?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (hidden) return null;

  if (confirm && asking) {
    return (
      <span className="inline-flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1 text-[11px]">
        {confirm}
        <button type="button" onClick={onClick} className="font-medium underline">
          Yes, do it
        </button>
        <button type="button" onClick={() => setAsking(false)} className="underline">
          Cancel
        </button>
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={() => (confirm ? setAsking(true) : onClick())}
      disabled={pending || disabled}
      className={`mt-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium disabled:opacity-50 ${
        tone === "danger"
          ? "border border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
          : "border border-[var(--color-border)] bg-[var(--color-bg)]"
      }`}
    >
      {pending && <Loader2 className="h-3 w-3 animate-spin" />}
      {children}
    </button>
  );
}
