/**
 * What the keep-position checkbox actually promises, per executor type.
 *
 * `keep_position` does not mean the same thing everywhere, and the stop dialog
 * used to claim it did. For a position or grid executor, stopping with the flag
 * set leaves the filled exposure sitting on the exchange — the position really
 * does stay open. For an **LP executor it does not**: `early_stop()` ALWAYS
 * closes the CLMM position on-chain (hummingbot
 * `strategy_v2/executors/lp_executor/lp_executor.py`, which moves the state to
 * CLOSING either way and says so in as many words); the flag only decides
 * whether the withdrawn tokens are kept as a position hold (POSITION_HOLD) or
 * swapped back to quote (EARLY_STOP).
 *
 * Both close types are POSITION_HOLD-shaped in the result, which is what made
 * the unconditional "the position stays open on the exchange" copy so easy to
 * write and so wrong: a user ticking the box to keep earning LP fees unwinds
 * their range and is told it survived.
 */

/** Executor shape this module needs — anything with a type tag will do. */
export interface StoppableExecutor {
  type?: string | null;
}

/**
 * The lowercase-tolerant LP discriminator used elsewhere in the tree
 * (`useMainControllerData`, `LpPositionBar`).
 */
export function isLpExecutor(ex: StoppableExecutor | null | undefined): boolean {
  return ex?.type?.toLowerCase() === "lp";
}

export interface StopKeepCopy {
  /** Checkbox label. */
  label: string;
  /** Helper line shown when the box is ticked. */
  checked: string;
  /** Helper line shown when it is not. */
  unchecked: string;
}

const NON_LP: StopKeepCopy = {
  label: "Keep position open",
  checked: "The executor stops but the position stays open on the exchange.",
  unchecked: "The executor stops and closes any open position.",
};

const LP: StopKeepCopy = {
  label: "Keep token exposure",
  checked:
    "The pool position is closed on-chain either way; the withdrawn tokens are kept as a position hold instead of being swapped back to quote.",
  unchecked:
    "The pool position is closed on-chain and the withdrawn tokens are swapped back to quote.",
};

const MIXED: StopKeepCopy = {
  label: "Keep exposure",
  checked:
    "Non-LP positions stay open on the exchange. LP pool positions are closed on-chain either way; their withdrawn tokens are kept as a position hold.",
  unchecked:
    "Every position is closed, and LP pool positions are unwound back to quote.",
};

/**
 * The copy for a stop about to be confirmed, given the executors it covers.
 *
 * A selection mixing LP with anything else gets its own wording rather than
 * either half's, because both halves would be a lie about the other.
 */
export function stopKeepCopy(executors: readonly StoppableExecutor[]): StopKeepCopy {
  if (executors.length === 0) return NON_LP;
  const lpCount = executors.filter(isLpExecutor).length;
  if (lpCount === 0) return NON_LP;
  return lpCount === executors.length ? LP : MIXED;
}
