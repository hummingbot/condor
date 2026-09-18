//! Two accounts, and nothing that duplicates a chain fact.
//!
//! `Protocol` is one per program: who may rotate keys, who cranks, and which
//! Meteora partner config every vault launches from. `Vault` is one per id,
//! and its address is `["vault", id]`; its treasury is `["treasury", id]`
//! — so a vault has exactly one treasury and a treasury exactly one vault, with no
//! registry in between (D4).

use anchor_lang::prelude::*;

pub const PROTOCOL_SEED: &[u8] = b"protocol";
pub const VAULT_SEED: &[u8] = b"vault";
/// The seeds of the PDA that is the vault's treasury *and* the DBC pool's
/// creator. One address holds both jobs so that every creator-side stream —
/// the seed, the curve fees, the surplus, the position fees — is routed by a
/// program rule rather than by somebody's key (plan §1.1).
pub const TREASURY_SEED: &[u8] = b"treasury";

pub const BPS_DENOMINATOR: u16 = 10_000;

/// The economics every tokenized vault launches with. Checked against the DBC
/// config the creator passes, rather than against one config *address*: a vault
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
    //! * a term is a **bound** when it is a decision the creator is entitled to
    //!   make and a buyer is entitled to read. Those are recorded on the `Vault`
    //!   at `tokenize`, so a holder reads what this vault chose rather than
    //!   inferring it from a config account;
    //! * a term is a **constant** when varying it would break something the
    //!   design rests on, or would quietly move value from holders to somebody
    //!   else. Those are changed by a program upgrade, which is the right weight
    //!   for a rule that binds every holder of every vault.

    /// **Constant.** Half the raise is permanently locked as liquidity in the
    /// graduated pool; the rest, less the protocol's graduation fee, is the
    /// vault's capital. DBC stores the complement as its
    /// `migration_fee_percentage`, and `tokenize` converts.
    ///
    /// One number rather than a range. It was a range — the split between the
    /// holders' exit depth and the strategy's size is a real decision, and two
    /// defensible vaults can differ on it. But a curve is only feasible for
    /// some combinations of this number and the market caps, so most of the
    /// range could not be launched at all, and a bound a creator cannot reach
    /// is a promise the product does not keep. Fifty is the value that builds.
    /// Widening it again means finding the feasible region first.
    pub const LOCKED_LIQUIDITY_PCT: u8 = 50;

    /// **Constant.** The protocol's graduation fee: the share of the unlocked
    /// raise — the vault's capital — that DBC holds for `Protocol.fee_claimer`
    /// rather than for the pool creator (DBC calls it the partner's share of
    /// the migration fee). It is the one platform fee a holder pays, taken
    /// once, at graduation, and it lives here rather than on a pricing page so
    /// that a buyer on the curve can read it before they buy.
    pub const PROTOCOL_GRADUATION_FEE_PCT: u8 = 2;
    /// The rest is the pool creator's, and the pool creator is the vault's own
    /// treasury: 98 % of the unlocked raise becomes capital nobody can
    /// withdraw. DBC's `creator_migration_fee_percentage`.
    pub const CREATOR_GRADUATION_SHARE_PCT: u8 = 100 - PROTOCOL_GRADUATION_FEE_PCT;

    /// The creator's share of the non-protocol trading fee. Bounded above rather
    /// than fixed: a creator may take less, and 50 is the most they may take,
    /// because the remainder is what pays for the infrastructure every vault
    /// uses.
    pub const MAX_CREATOR_TRADING_FEE_PCT: u8 = 50;

    /// `TokenUpdateAuthority::Immutable`. The other four values leave somebody
    /// able to update the token's metadata, and two of them leave the creator
    /// with a mint authority on it.
    pub const TOKEN_UPDATE_AUTHORITY_IMMUTABLE: u8 = 1;

    /// What the graduated pool charges, as DBC's `MigrationFeeOption`. The allowed
    /// set is Meteora's fixed-fee options; the customizable one (6) is excluded
    /// because on that path the fee lives in a field nothing here enforces, so
    /// a vault taking it would be promising a number it could change.
    pub const POOL_FEE_OPTION_25_BPS: u8 = 0;
    pub const POOL_FEE_OPTION_30_BPS: u8 = 1;
    pub const POOL_FEE_OPTION_100_BPS: u8 = 2;
    pub const POOL_FEE_OPTION_200_BPS: u8 = 3;
    pub const POOL_FEE_OPTION_400_BPS: u8 = 4;
    pub const POOL_FEE_OPTION_600_BPS: u8 = 5;
    pub const MAX_POOL_FEE_OPTION: u8 = POOL_FEE_OPTION_600_BPS;
    /// What the form offers: the same 100 bps the curve charged.
    pub const DEFAULT_POOL_FEE_OPTION: u8 = POOL_FEE_OPTION_100_BPS;

    // There is no constant for the graduation threshold, and there was one:
    // ten wrapped SOL, on the belief that Meteora's keepers only graduate that
    // number. It made every launch unbuildable. DBC *derives* the threshold
    // from the market caps and the supply split and offers no way to set it, so
    // a program that insisted on a particular value refused every config its
    // own builder produced — which is what happens when a constant is chosen
    // for a market rather than read from one.
    //
    // What the creator chooses is the **graduation market cap**, in the quote
    // asset, and the threshold follows from it. It is recorded on the `Vault`
    // (`graduation_quote_threshold`) because it is the one number that says how
    // much this curve raises before it becomes a pool, which is what a buyer is
    // deciding about. Graduation itself is permissionless — anyone may call
    // DBC's `migration_damm_v2` once the curve is full — so nothing here
    // depends on a keeper's preferences.

    /// **Constant.** `TokenType::Token2022`, and a fixed supply: a share of the
    /// supply means nothing against a mint that can print more.
    pub const TOKEN_TYPE_TOKEN_2022: u8 = 1;
}


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
/// its creator may withdraw from it, because there is nobody else in it.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, InitSpace, PartialEq, Eq, Debug)]
pub enum VaultState {
    /// The crank may tick it.
    Running,
    /// The creator stopped it. Positions stay open; nothing new is taken.
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
    /// The wallet that created it. Publishes the strategy, pauses, installs
    /// the delegate, claims its income, winds down once. While the vault is
    /// private it may also empty it, through `execute_unchecked`; from
    /// `tokenize` on it cannot move a token to anyone.
    pub creator: Pubkey,
    /// The 32 random bytes chosen at creation. Both PDAs derive from them —
    /// `["vault", id]` is this account and the vault's public identity;
    /// `["treasury", id]` is the treasury, a system account with no data
    /// that holds the SOL and owns every token account and position. Stored so
    /// every instruction can re-derive the treasury's signing seeds.
    pub id: [u8; 32],
    /// Set by `tokenize`; the Token-2022 mint DBC created. Default until then,
    /// and that default *is* what "private" means.
    pub mint: Pubkey,
    pub dbc_pool: Pubkey,
    pub agent_ref: AgentRef,
    /// sha256 of the canonical encoding of the private config. The values are
    /// never on chain: this is the commitment a run checks them against.
    pub config_hash: [u8; 32],
    /// The unit of account, and **default until `tokenize`**: what the curve
    /// sells the token for, what the graduated DAMM v2 pool quotes in, what a
    /// wind-down converts into and what a redemption pays. All four are the
    /// same asset because they are the same promise to a holder, and it is the
    /// launch config that fixes it — so it is written here at tokenization,
    /// from the config the creator chose, and never at creation.
    ///
    /// A private vault has none, and needs none: it owes nobody, its creator
    /// takes assets out through the delegate in whatever they are, and
    /// `finalize_wind_down` has nothing to verify. Asking at creation was
    /// asking a question whose answer could not matter until much later and
    /// could not be changed once it did.
    pub quote_mint: Pubkey,
    pub version: u32,
    /// What the curve offers, and the supply it is offered from — both in the
    /// token's own units, both read off the launch config at `tokenize`.
    ///
    /// Two numbers rather than the ratio they used to be, and the two every
    /// listing already names. `issue_bps` said 7387 and left a buyer to wonder
    /// 7387 of what; these say it in tokens, the
    /// unit every other balance is in, so a holder can compare them with the
    /// mint's supply and the treasury's own holding without converting
    /// anything. There is no standard to follow here: an SPL or Token-2022
    /// mint carries `supply` and nothing else — no maximum, no circulating
    /// figure, and no extension that adds one — so these are this program's
    /// own, and `total_supply` is worth storing precisely because `redeem` burns
    /// and the chain's own `supply` stops being the number it was. `total_supply` less `circulating_supply` is the
    /// retained supply: it lands in the treasury, never in the creator's hands,
    /// and can only reach the market through a later sale.
    ///
    /// Neither moves after `tokenize`. `circulating_supply` is what *will* be
    /// in holders' hands once the curve completes; what actually is, at the
    /// moment the vault stops, is `redeemable_supply`.
    pub circulating_supply: u64,
    pub total_supply: u64,
    /// The launch terms this vault chose, copied from its DBC config at
    /// `tokenize` so a holder reads them here rather than decoding a config
    /// account. All three are bounded by `launch_rules`; all three are 0 while
    /// the vault is private.
    ///
    /// `graduation_quote_threshold` is the one that changes what the product
    /// *is*: how much quote the curve raises before it becomes a pool, and so
    /// how large the vault this token is a claim on will be. In the quote
    /// asset's own units.
    pub graduation_quote_threshold: u64,
    pub creator_trading_fee_pct: u8,
    /// The graduated pool's fee tier, as DBC's `MigrationFeeOption` index.
    pub pool_fee_option: u8,
    pub state: VaultState,
    /// The installed delegate, or the default key for none.
    pub delegate: Pubkey,
    /// What `redeem` divides by: the circulating supply, fixed once at
    /// `finalize_wind_down`. Not recomputed per redemption, because the
    /// balances it is derived from keep moving after the vault stops — the
    /// graduated pool trades forever, and quote paid into it can never reach
    /// the pot. Recomputing would let anyone buy the pool's inventory and
    /// redeem it against a denominator that had just shrunk by the same
    /// amount, diluting every holder who did nothing.
    pub redeemable_supply: u64,
    pub created_ts: i64,
    /// When the vault stopped being private. 0 while it still is.
    pub tokenized_ts: i64,
    pub wind_down_ts: i64,
    pub bump: u8,
    /// The bump of `["treasury", id]`. Stored rather than
    /// found: this program signs as that PDA in most of its instructions, and
    /// `find_program_address` is ~1500 CU each time.
    pub treasury_bump: u8,
    /// So a later field is an instruction, not an account migration.
    pub _reserved: [u8; 64],
}

impl Vault {
    pub fn has_delegate(&self) -> bool {
        self.delegate != Pubkey::default()
    }

    /// Whether anyone but the creator has a claim on what is inside.
    ///
    /// Everything that separates the two phases hangs off this one question:
    /// a private vault's treasury acts through `execute_unchecked`, which may do
    /// anything; a tokenized one's only through `execute`, which may not move
    /// a token to anyone.
    pub fn is_tokenized(&self) -> bool {
        self.mint != Pubkey::default()
    }

    pub fn is_pinned(&self) -> bool {
        self.version > 0
    }

    /// The seeds this program signs with as the treasury — for every venue it
    /// trades on and as the DBC pool creator.
    pub fn treasury_seeds(&self) -> [&[u8]; 3] {
        [
            TREASURY_SEED,
            self.id.as_ref(),
            std::slice::from_ref(&self.treasury_bump),
        ]
    }

    /// The treasury's address, derived rather than stored: one fewer field that
    /// could disagree with the seeds.
    pub fn treasury(&self) -> Pubkey {
        Pubkey::create_program_address(&self.treasury_seeds(), &crate::ID)
            .expect("stored authority bump derives the treasury")
    }

    /// Whether `key` may act for the treasury.
    pub fn may_act(&self, key: &Pubkey) -> bool {
        *key == self.creator || (self.has_delegate() && *key == self.delegate)
    }
}

#[cfg(test)]
mod tests {
    use super::launch_rules as rules;

    /// Every launch locks half and keeps half, so every vault has both a market
    /// to exit through and a strategy to run. A value that left either at zero
    /// would be a different product wearing this one's guarantees.
    #[test]
    fn every_launch_has_both_a_market_and_a_strategy() {
        assert_eq!(rules::LOCKED_LIQUIDITY_PCT, 50);
        assert!(rules::LOCKED_LIQUIDITY_PCT >= 20, "a market deep enough to sell into");
        assert!(100 - rules::LOCKED_LIQUIDITY_PCT >= 20, "capital enough to run a strategy");
    }

    /// The customizable option (6) is excluded: on that path the graduated pool's
    /// fee lives in a field nothing checks, so a vault taking it would be
    /// promising a number it could later change.
    #[test]
    fn the_customizable_fee_option_is_not_allowed() {
        assert_eq!(rules::MAX_POOL_FEE_OPTION, 5);
        assert!(rules::DEFAULT_POOL_FEE_OPTION <= rules::MAX_POOL_FEE_OPTION);
    }

    /// Two percent of the unlocked raise is the protocol's, once, at
    /// graduation; the rest is the treasury's. The two shares are the whole of
    /// it, so no part can go unclaimed or to a third party.
    #[test]
    fn the_protocol_takes_two_percent_at_graduation() {
        assert_eq!(rules::PROTOCOL_GRADUATION_FEE_PCT, 2);
        assert_eq!(rules::CREATOR_GRADUATION_SHARE_PCT, 98);
        assert_eq!(rules::PROTOCOL_GRADUATION_FEE_PCT + rules::CREATOR_GRADUATION_SHARE_PCT, 100);
    }

}
