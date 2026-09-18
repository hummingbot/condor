//! Two accounts, and nothing that duplicates a chain fact.
//!
//! `Protocol` is one per program: who may rotate keys, who cranks, and which
//! Meteora partner config every vault launches from. `Vault` is one per Swig,
//! and its address is `["vault", swig_account]` — so a vault has exactly one
//! Swig and a Swig has exactly one vault, with no registry in between (D4).

use anchor_lang::prelude::*;

pub const PROTOCOL_SEED: &[u8] = b"protocol";
pub const VAULT_SEED: &[u8] = b"vault";
/// The seeds of the PDA that is the Swig's root authority *and* the DBC pool's
/// creator. One address holds both jobs so that every creator-side stream —
/// the seed, the curve fees, the surplus, the position fees — is routed by a
/// program rule rather than by somebody's key (plan §1.1).
pub const VAULT_AUTHORITY_SEED: &[u8] = b"vault_authority";

pub const BPS_DENOMINATOR: u16 = 10_000;

/// The economics every tokenized vault launches with. Checked against the DBC
/// config the runner passes, rather than against one config *address*: a vault
/// prices its own launch off its NAV, so each one needs its own config, and the
/// thing that must not vary is the economics — not which account holds them.
/// Changing any of these is a program upgrade, which is the right weight for a
/// rule that binds every holder of every vault.
pub mod launch_rules {
    //! What a launch may and may not choose.
    //!
    //! A vault creates its own DBC config — it prices its launch off assets it
    //! already holds — so the program cannot recognise a good config by its
    //! address. It checks the terms instead, and each one is either a **bound**
    //! or a **constant**. The difference is not arbitrary:
    //!
    //! * a term is a **bound** when it is a decision the runner is entitled to
    //!   make and a buyer is entitled to read. Those are recorded on the `Vault`
    //!   at `tokenize`, so a holder reads what this vault chose rather than
    //!   inferring it from a config account;
    //! * a term is a **constant** when varying it would break something the
    //!   design rests on, or would quietly move value from holders to somebody
    //!   else. Those are changed by a program upgrade, which is the right weight
    //!   for a rule that binds every holder of every vault.

    /// How much of the raise becomes the vault's capital, the rest becoming
    /// permanently locked liquidity. **This is the split between the strategy
    /// and the holders' exit**: at 80 the strategy gets 8 SOL and the pool about
    /// 2; at 20 it is a small strategy behind a deep market. Both are defensible
    /// and they are not the same product, which is exactly why the runner
    /// chooses and the number is on chain.
    pub const MIN_MIGRATION_FEE_PCT: u8 = 20;
    pub const MAX_MIGRATION_FEE_PCT: u8 = 80;
    /// What the form offers. Not enforced — it is a starting point, not a rule.
    pub const DEFAULT_MIGRATION_FEE_PCT: u8 = 50;

    /// **Constant.** All of the migration fee is the creator's, and the creator
    /// is the vault's own PDA. Anything less would route part of the seed to the
    /// partner — Condor — which is holders' capital paying a platform fee under
    /// another name.
    pub const CREATOR_MIGRATION_FEE_PCT: u8 = 100;

    /// The runner's share of the non-protocol trading fee. Bounded above rather
    /// than fixed: a runner may take less, and 50 is the most they may take,
    /// because the remainder is what pays for the infrastructure every vault
    /// uses.
    pub const MAX_CREATOR_TRADING_FEE_PCT: u8 = 50;

    /// What the migrated pool charges, as a `MigrationFeeOption`. The allowed
    /// set is Meteora's fixed-fee options; the customizable one (6) is excluded
    /// because on that path the fee lives in a field nothing here enforces, so
    /// a vault taking it would be promising a number it could change.
    pub const MIGRATION_FEE_OPTION_25_BPS: u8 = 0;
    pub const MIGRATION_FEE_OPTION_30_BPS: u8 = 1;
    pub const MIGRATION_FEE_OPTION_100_BPS: u8 = 2;
    pub const MIGRATION_FEE_OPTION_200_BPS: u8 = 3;
    pub const MIGRATION_FEE_OPTION_400_BPS: u8 = 4;
    pub const MIGRATION_FEE_OPTION_600_BPS: u8 = 5;
    pub const MAX_MIGRATION_FEE_OPTION: u8 = MIGRATION_FEE_OPTION_600_BPS;
    /// What the form offers: the same 100 bps the curve charged.
    pub const DEFAULT_MIGRATION_FEE_OPTION: u8 = MIGRATION_FEE_OPTION_100_BPS;

    /// **Constant, and load-bearing.** Ten wrapped SOL is the one threshold
    /// Meteora's mainnet keepers migrate automatically. At any other number the
    /// curve fills and nothing happens until somebody notices — which is a
    /// vault's capital sitting in a dead pool.
    pub const MIGRATION_QUOTE_THRESHOLD: u64 = 10_000_000_000;

    /// **Constant.** `TokenType::Token2022`, and a fixed supply: `issue_bps`
    /// means nothing against a mint that can print more.
    pub const TOKEN_TYPE_TOKEN_2022: u8 = 1;
}

/// Below this, a wind-down counts a balance as swept. Dust is the price of a
/// one-asset redemption: a hundred lamports of some token nobody will ever
/// claim must not be able to hold every holder's redemption hostage.
pub const WIND_DOWN_DUST: u64 = 1_000;

pub const MAX_NAME_LEN: usize = 32;
pub const MAX_SYMBOL_LEN: usize = 10;
pub const MAX_URI_LEN: usize = 200;

#[account]
#[derive(InitSpace)]
pub struct Protocol {
    /// Rotates keys and winds down an abandoned vault. Nothing else — it never
    /// trades, holds a delegate, or touches a vault's funds. Moves to a Squads
    /// multisig before the first mainnet vault (plan §1.7).
    pub authority: Pubkey,
    /// The system whose delegate key is installed on every vault and whose
    /// crank ticks them. One key today; a registry the day there are two.
    pub administrator: Pubkey,
    /// Where Meteora pays the protocol's half of the trading fee. Every vault's
    /// config must name it, which is the one economic term the program enforces
    /// by address rather than by value.
    pub fee_claimer: Pubkey,
    /// A partner config Condor publishes as the default template for new
    /// vaults. Informational: a vault prices its own launch, so the program
    /// checks a config's *terms* (`launch_rules`) and never its address.
    pub dbc_config: Pubkey,
    pub bump: u8,
    pub _reserved: [u8; 64],
}

/// The public agent folder a vault runs: a git repo (sha256 of its URL), a
/// commit, and the agent and strategy slugs inside it. Nothing on chain
/// validates these — Condor's own scan gates what Condor runs (plan D17).
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, InitSpace, Default, PartialEq, Eq, Debug)]
pub struct AgentRef {
    pub repo_hash: [u8; 32],
    pub commit: [u8; 20],
    pub agent_slug: [u8; 32],
    pub strategy_slug: [u8; 32],
}

/// Where a vault is in its life. **Private is not a state here**: a vault is
/// private exactly while it has no mint, so the two cannot disagree. A private
/// vault runs, pauses and winds down like any other — the difference is that
/// its runner may withdraw from it, because there is nobody else in it.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, InitSpace, PartialEq, Eq, Debug)]
pub enum VaultState {
    /// The crank may tick it.
    Running,
    /// The runner stopped it. Positions stay open; nothing new is taken.
    Paused,
    /// One-way. Ticks stop, the administrator closes and converts, and the
    /// strategy can never change again.
    WindingDown,
    /// Finished. The delegate is gone, nothing trades, holders redeem.
    Redeemable,
}

impl VaultState {
    /// Whether the strategy may still change. False from `wind_down` onwards,
    /// which is what makes the wind-down one-way.
    pub fn accepts_strategy_changes(&self) -> bool {
        matches!(self, VaultState::Running | VaultState::Paused)
    }
}

#[account]
#[derive(InitSpace)]
pub struct Vault {
    /// The wallet that created it. Publishes the strategy, sets the fee,
    /// pauses, installs the administrator, claims its income, winds down once.
    /// Cannot withdraw anything, ever.
    pub runner: Pubkey,
    pub swig_account: Pubkey,
    /// Where the money actually is: the Swig's funds-owner PDA.
    pub funds_owner: Pubkey,
    /// Set by `tokenize`; the Token-2022 mint DBC created. Default until then,
    /// and that default *is* what "private" means.
    pub mint: Pubkey,
    pub dbc_pool: Pubkey,
    pub agent_ref: AgentRef,
    /// sha256 of the canonical encoding of the private config. The values are
    /// never on chain: this is the commitment a run checks them against.
    pub config_hash: [u8; 32],
    /// The unit of account, and **default until `tokenize`**: what the curve
    /// sells the token for, what the migrated DAMM v2 pool quotes in, what a
    /// wind-down converts into and what a redemption pays. All four are the
    /// same asset because they are the same promise to a holder, and it is the
    /// launch config that fixes it — so it is written here at tokenization,
    /// from the config the runner chose, and never at creation.
    ///
    /// A private vault has none, and needs none: it owes nobody, its runner
    /// takes assets out through the delegate in whatever they are, and
    /// `finalize_wind_down` has nothing to verify. Asking at creation was
    /// asking a question whose answer could not matter until much later and
    /// could not be changed once it did.
    pub quote_mint: Pubkey,
    pub version: u32,
    /// The share of the fixed supply sold in the first sale. The rest is the
    /// *retained* supply: it lands in the vault, not in the runner's hands, and
    /// can only leave through a later sale. Buyers read it as the ceiling on
    /// how far the runner could dilute them, so it is on chain beside the
    /// strategy rather than in a listing.
    pub issue_bps: u16,
    /// The launch terms this vault chose, copied from its DBC config at
    /// `tokenize` so a holder reads them here rather than decoding a config
    /// account. All three are bounded by `launch_rules`; all three are 0 while
    /// the vault is private.
    ///
    /// `migration_fee_pct` is the one that changes what the product *is*: it is
    /// the split between the strategy's capital and the holders' exit depth.
    pub migration_fee_pct: u8,
    pub creator_trading_fee_pct: u8,
    pub migration_fee_option: u8,
    pub state: VaultState,
    /// The installed delegate, or the default key for none.
    pub delegate: Pubkey,
    pub created_ts: i64,
    /// When the vault stopped being private. 0 while it still is.
    pub tokenized_ts: i64,
    pub wind_down_ts: i64,
    pub bump: u8,
    /// The bump of `["vault_authority", swig_account]`. Stored rather than
    /// found: this program signs as that PDA in most of its instructions, and
    /// `find_program_address` is ~1500 CU each time.
    pub authority_bump: u8,
    /// So a later field is an instruction, not an account migration.
    pub _reserved: [u8; 64],
}

impl Vault {
    pub fn has_delegate(&self) -> bool {
        self.delegate != Pubkey::default()
    }

    /// Whether anyone but the runner has a claim on what is inside.
    ///
    /// Everything that separates the two phases hangs off this one question:
    /// a private vault's runner may withdraw and may install a delegate alone;
    /// a tokenized one's may do neither.
    pub fn is_tokenized(&self) -> bool {
        self.mint != Pubkey::default()
    }

    pub fn is_pinned(&self) -> bool {
        self.version > 0
    }

    /// The seeds this program signs with, as the Swig root and the DBC creator.
    pub fn authority_seeds<'a>(&'a self, swig_account: &'a Pubkey) -> [&'a [u8]; 3] {
        [
            VAULT_AUTHORITY_SEED,
            swig_account.as_ref(),
            std::slice::from_ref(&self.authority_bump),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::launch_rules as rules;

    /// The migration fee is the split between the vault's capital and the
    /// holders' exit depth, so both ends of the range have to be launchable —
    /// they are two different products, not a good value and a bad one.
    #[test]
    fn the_migration_fee_range_is_a_real_range() {
        assert!(rules::MIN_MIGRATION_FEE_PCT < rules::DEFAULT_MIGRATION_FEE_PCT);
        assert!(rules::DEFAULT_MIGRATION_FEE_PCT < rules::MAX_MIGRATION_FEE_PCT);
        assert_eq!(rules::MIN_MIGRATION_FEE_PCT, 20);
        assert_eq!(rules::MAX_MIGRATION_FEE_PCT, 80);
    }

    /// At the SDK's cap a migration fee would leave no liquidity at all, which
    /// is a token nobody can sell. The ceiling here is well inside it.
    #[test]
    fn the_migration_fee_always_leaves_a_market() {
        assert!(rules::MAX_MIGRATION_FEE_PCT <= 80);
        let locked = 100 - rules::MAX_MIGRATION_FEE_PCT;
        assert!(locked >= 20, "at least a fifth of the raise stays as liquidity");
    }

    /// The customizable option (6) is excluded: on that path the migrated pool's
    /// fee lives in a field nothing checks, so a vault taking it would be
    /// promising a number it could later change.
    #[test]
    fn the_customizable_fee_option_is_not_allowed() {
        assert_eq!(rules::MAX_MIGRATION_FEE_OPTION, 5);
        assert!(rules::DEFAULT_MIGRATION_FEE_OPTION <= rules::MAX_MIGRATION_FEE_OPTION);
    }

    /// The whole migration fee is the creator's, and the creator is the vault's
    /// own PDA. Anything less routes holders' capital to the partner.
    #[test]
    fn the_seed_is_not_shared_with_the_partner() {
        assert_eq!(rules::CREATOR_MIGRATION_FEE_PCT, 100);
    }

    /// Ten wrapped SOL is the one threshold Meteora's keepers migrate for us.
    #[test]
    fn the_threshold_is_the_one_meteora_serves() {
        assert_eq!(rules::MIGRATION_QUOTE_THRESHOLD, 10_000_000_000);
    }
}
