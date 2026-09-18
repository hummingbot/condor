//! Meteora's Dynamic Bonding Curve and DAMM v2, as this program sees them.
//!
//! Both are Anchor programs, so their instruction data is an 8-byte
//! discriminator followed by Borsh — derived here from the IDLs shipped in
//! `@meteora-ag/dynamic-bonding-curve-sdk` 1.5.12 (IDL 0.2.1) and
//! `@meteora-ag/cp-amm-sdk`. The discriminators are copied verbatim rather
//! than recomputed: `sha256("global:<name>")[..8]` is only a convention, and a
//! constant that was read from the IDL can be checked against it.
//!
//! What this program needs from them is small. DBC creates the token and runs
//! the curve; Meteora's keepers migrate it. This program only ever:
//!
//! * **launches** the pool with its own PDA as creator, so every creator-side
//!   stream is routed by rule rather than by a key (plan D8);
//! * **collects the seed** — the migration fee, 80 % of the raise, into the
//!   treasury — which DBC's own one-time flag makes idempotent, so the call can be
//!   permissionless;
//! * **claims the creator's income**: curve trading fees, the surplus above the
//!   threshold, and the migrated position's fees.
//!
//! The vault's *trading* never goes through here: that is the delegate's, out
//! through Gateway.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};

pub const DBC_PROGRAM_ID: Pubkey = pubkey!("dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN");
/// DBC's own signer PDA, a fixed address in its IDL.
pub const DBC_POOL_AUTHORITY: Pubkey = pubkey!("FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM");

pub const DAMM_V2_PROGRAM_ID: Pubkey = pubkey!("cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG");
pub const DAMM_V2_POOL_AUTHORITY: Pubkey = pubkey!("HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC");

pub const TOKEN_2022_PROGRAM_ID: Pubkey = pubkey!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");

// ── instruction discriminators (from the IDLs) ───────────────────────────────

pub const IX_INITIALIZE_POOL_TOKEN2022: [u8; 8] = [169, 118, 51, 78, 145, 110, 220, 155];
pub const IX_WITHDRAW_MIGRATION_FEE: [u8; 8] = [237, 142, 45, 23, 129, 6, 222, 162];
pub const IX_CLAIM_CREATOR_TRADING_FEE: [u8; 8] = [82, 220, 250, 189, 3, 85, 107, 45];
pub const IX_CREATOR_WITHDRAW_SURPLUS: [u8; 8] = [165, 3, 137, 7, 28, 134, 76, 80];
pub const IX_CLAIM_POSITION_FEE: [u8; 8] = [180, 38, 154, 17, 133, 33, 162, 211];
pub const IX_WITHDRAW_LEFTOVER: [u8; 8] = [20, 198, 202, 237, 235, 243, 183, 66];

/// `withdraw_migration_fee`'s one argument: whose share is being taken.
/// 0 = the partner's, 1 = the creator's. The vault's seed is the creator's.
pub const MIGRATION_FEE_FLAG_CREATOR: u8 = 1;

// ── VirtualPool ──────────────────────────────────────────────────────────────

/// `sha256("account:VirtualPool")[..8]`.
pub const VIRTUAL_POOL_DISCRIMINATOR: [u8; 8] = [213, 224, 5, 209, 98, 69, 119, 92];

// Offsets into the account data, discriminator included. The struct is
// `VirtualPool { pool_state: PoolState }`, so these are PoolState's offsets + 8.
const POOL_CONFIG: usize = 8 + 64;
const POOL_CREATOR: usize = 8 + 96;
const POOL_BASE_MINT: usize = 8 + 128;
const POOL_BASE_VAULT: usize = 8 + 160;
const POOL_QUOTE_VAULT: usize = 8 + 192;
const POOL_IS_MIGRATED: usize = 8 + 297;
const POOL_MIGRATION_FEE_WITHDRAW_STATUS: usize = 8 + 303;
const POOL_MIN_LEN: usize = 8 + 416;

pub struct VirtualPool {
    pub config: Pubkey,
    pub creator: Pubkey,
    pub base_mint: Pubkey,
    pub base_vault: Pubkey,
    pub quote_vault: Pubkey,
    pub is_migrated: bool,
    /// Bit 1 is the creator's share; set once the seed has been collected.
    pub migration_fee_withdraw_status: u8,
}

impl VirtualPool {
    pub fn creator_migration_fee_withdrawn(&self) -> bool {
        self.migration_fee_withdraw_status & 0b10 != 0
    }
}

pub fn read_virtual_pool(info: &AccountInfo) -> Result<VirtualPool> {
    require_keys_eq!(*info.owner, DBC_PROGRAM_ID, crate::error::VaultError::PoolNotDbc);
    let data = info.try_borrow_data()?;
    require!(data.len() >= POOL_MIN_LEN, crate::error::VaultError::PoolNotDbc);
    require!(
        data[..8] == VIRTUAL_POOL_DISCRIMINATOR,
        crate::error::VaultError::PoolNotDbc
    );
    let key_at = |o: usize| Pubkey::new_from_array(data[o..o + 32].try_into().unwrap());
    Ok(VirtualPool {
        config: key_at(POOL_CONFIG),
        creator: key_at(POOL_CREATOR),
        base_mint: key_at(POOL_BASE_MINT),
        base_vault: key_at(POOL_BASE_VAULT),
        quote_vault: key_at(POOL_QUOTE_VAULT),
        is_migrated: data[POOL_IS_MIGRATED] != 0,
        migration_fee_withdraw_status: data[POOL_MIGRATION_FEE_WITHDRAW_STATUS],
    })
}

// ── PoolConfig ───────────────────────────────────────────────────────────────

pub const POOL_CONFIG_DISCRIMINATOR: [u8; 8] = [26, 108, 14, 123, 116, 230, 129, 43];

// Offsets into `PoolConfig`, discriminator included.
const CONFIG_QUOTE_MINT: usize = 8;
const CONFIG_FEE_CLAIMER: usize = 8 + 32;
const CONFIG_LEFTOVER_RECEIVER: usize = 8 + 64;
const CONFIG_MIGRATION_OPTION: usize = 8 + 225;
const CONFIG_MIGRATION_FEE_OPTION: usize = 8 + 235;
const CONFIG_TOKEN_TYPE: usize = 8 + 229;
const CONFIG_FIXED_TOKEN_SUPPLY_FLAG: usize = 8 + 236;
const CONFIG_CREATOR_TRADING_FEE_PCT: usize = 8 + 237;
const CONFIG_MIGRATION_FEE_PCT: usize = 8 + 239;
const CONFIG_CREATOR_MIGRATION_FEE_PCT: usize = 8 + 240;
const CONFIG_MIGRATION_QUOTE_THRESHOLD: usize = 8 + 256;
const CONFIG_MIGRATED_POOL_FEE_BPS: usize = 8 + 354;
const CONFIG_MIN_LEN: usize = 8 + 1040;

/// `MigrationOption::DammV2`. Token-2022 launches must migrate to DAMM v2.
pub const MIGRATION_OPTION_DAMM_V2: u8 = 1;

/// Everything about a partner config this program insists on.
///
/// Read rather than assumed because a vault prices its own launch off its NAV,
/// so each one creates its own config and the program can no longer recognise
/// a good one by its address. What it checks instead is the economics — which
/// is the thing holders actually care about, and is now legible on chain
/// rather than by reference to an address nobody can interpret.
pub struct PoolConfigParams {
    pub quote_mint: Pubkey,
    pub fee_claimer: Pubkey,
    pub leftover_receiver: Pubkey,
    pub migration_option: u8,
    /// Which DAMM v2 fee config the pool migrates into — and therefore what the
    /// migrated pool charges.
    pub migration_fee_option: u8,
    pub token_type: u8,
    pub fixed_token_supply: bool,
    pub creator_trading_fee_pct: u8,
    pub migration_fee_pct: u8,
    pub creator_migration_fee_pct: u8,
    pub migration_quote_threshold: u64,
    pub migrated_pool_fee_bps: u16,
}

pub fn read_pool_config(info: &AccountInfo) -> Result<PoolConfigParams> {
    require_keys_eq!(*info.owner, DBC_PROGRAM_ID, crate::error::VaultError::PoolNotDbc);
    let data = info.try_borrow_data()?;
    require!(data.len() >= CONFIG_MIN_LEN, crate::error::VaultError::PoolNotDbc);
    require!(
        data[..8] == POOL_CONFIG_DISCRIMINATOR,
        crate::error::VaultError::PoolNotDbc
    );
    let key_at = |o: usize| Pubkey::new_from_array(data[o..o + 32].try_into().unwrap());
    Ok(PoolConfigParams {
        quote_mint: key_at(CONFIG_QUOTE_MINT),
        fee_claimer: key_at(CONFIG_FEE_CLAIMER),
        leftover_receiver: key_at(CONFIG_LEFTOVER_RECEIVER),
        migration_option: data[CONFIG_MIGRATION_OPTION],
        migration_fee_option: data[CONFIG_MIGRATION_FEE_OPTION],
        token_type: data[CONFIG_TOKEN_TYPE],
        fixed_token_supply: data[CONFIG_FIXED_TOKEN_SUPPLY_FLAG] != 0,
        creator_trading_fee_pct: data[CONFIG_CREATOR_TRADING_FEE_PCT],
        migration_fee_pct: data[CONFIG_MIGRATION_FEE_PCT],
        creator_migration_fee_pct: data[CONFIG_CREATOR_MIGRATION_FEE_PCT],
        migration_quote_threshold: u64::from_le_bytes(
            data[CONFIG_MIGRATION_QUOTE_THRESHOLD..CONFIG_MIGRATION_QUOTE_THRESHOLD + 8]
                .try_into()
                .unwrap(),
        ),
        migrated_pool_fee_bps: u16::from_le_bytes(
            data[CONFIG_MIGRATED_POOL_FEE_BPS..CONFIG_MIGRATED_POOL_FEE_BPS + 2]
                .try_into()
                .unwrap(),
        ),
    })
}

// ── instruction data ─────────────────────────────────────────────────────────

/// `InitializePoolParameters { name, symbol, uri }`, Borsh: each string is a
/// u32 length followed by its bytes.
pub fn initialize_pool_data(name: &str, symbol: &str, uri: &str) -> Vec<u8> {
    let mut data = Vec::with_capacity(8 + 12 + name.len() + symbol.len() + uri.len());
    data.extend_from_slice(&IX_INITIALIZE_POOL_TOKEN2022);
    for field in [name, symbol, uri] {
        data.extend_from_slice(&(field.len() as u32).to_le_bytes());
        data.extend_from_slice(field.as_bytes());
    }
    data
}

pub fn withdraw_migration_fee_data() -> Vec<u8> {
    let mut data = Vec::with_capacity(9);
    data.extend_from_slice(&IX_WITHDRAW_MIGRATION_FEE);
    data.push(MIGRATION_FEE_FLAG_CREATOR);
    data
}

/// `claim_creator_trading_fee(max_base_amount, max_quote_amount)`. Both are set
/// to u64::MAX: the creator is claiming what is theirs, and a cap here would
/// only be a second number to keep in step with the pool's.
pub fn claim_creator_trading_fee_data() -> Vec<u8> {
    let mut data = Vec::with_capacity(24);
    data.extend_from_slice(&IX_CLAIM_CREATOR_TRADING_FEE);
    data.extend_from_slice(&u64::MAX.to_le_bytes());
    data.extend_from_slice(&u64::MAX.to_le_bytes());
    data
}

pub fn creator_withdraw_surplus_data() -> Vec<u8> {
    IX_CREATOR_WITHDRAW_SURPLUS.to_vec()
}

pub fn claim_position_fee_data() -> Vec<u8> {
    IX_CLAIM_POSITION_FEE.to_vec()
}

pub fn withdraw_leftover_data() -> Vec<u8> {
    IX_WITHDRAW_LEFTOVER.to_vec()
}

/// Anchor's own event-authority PDA, which every Anchor program with events
/// names in its account list.
pub fn event_authority(program_id: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[b"__event_authority"], program_id).0
}

// ── instruction builders ─────────────────────────────────────────────────────

/// Accounts in the IDL's order. Every builder below takes them as already
/// validated `AccountInfo`s and puts them in that order — the order *is* the
/// interface, and a mis-ordered list is a runtime error a hundred CU in.
pub struct DbcAccounts<'a, 'info> {
    pub pool_authority: &'a AccountInfo<'info>,
    pub config: &'a AccountInfo<'info>,
    pub virtual_pool: &'a AccountInfo<'info>,
    pub token_quote_account: &'a AccountInfo<'info>,
    pub quote_vault: &'a AccountInfo<'info>,
    pub quote_mint: &'a AccountInfo<'info>,
    pub creator: &'a AccountInfo<'info>,
    pub token_quote_program: &'a AccountInfo<'info>,
    pub event_authority: &'a AccountInfo<'info>,
    pub program: &'a AccountInfo<'info>,
}

impl<'a, 'info> DbcAccounts<'a, 'info> {
    fn quote_only_metas(&self) -> Vec<AccountMeta> {
        vec![
            AccountMeta::new_readonly(self.pool_authority.key(), false),
            AccountMeta::new_readonly(self.config.key(), false),
            AccountMeta::new(self.virtual_pool.key(), false),
            AccountMeta::new(self.token_quote_account.key(), false),
            AccountMeta::new(self.quote_vault.key(), false),
            AccountMeta::new_readonly(self.quote_mint.key(), false),
            AccountMeta::new_readonly(self.creator.key(), true),
            AccountMeta::new_readonly(self.token_quote_program.key(), false),
            AccountMeta::new_readonly(self.event_authority.key(), false),
            AccountMeta::new_readonly(self.program.key(), false),
        ]
    }

    fn quote_only_infos(&self) -> Vec<AccountInfo<'info>> {
        vec![
            self.pool_authority.clone(),
            self.config.clone(),
            self.virtual_pool.clone(),
            self.token_quote_account.clone(),
            self.quote_vault.clone(),
            self.quote_mint.clone(),
            self.creator.clone(),
            self.token_quote_program.clone(),
            self.event_authority.clone(),
            self.program.clone(),
        ]
    }

    /// `withdraw_migration_fee(flag = creator)` — the seed.
    pub fn withdraw_migration_fee(&self) -> (Instruction, Vec<AccountInfo<'info>>) {
        (
            Instruction {
                program_id: DBC_PROGRAM_ID,
                accounts: self.quote_only_metas(),
                data: withdraw_migration_fee_data(),
            },
            self.quote_only_infos(),
        )
    }

    /// `creator_withdraw_surplus` — the vault creator's share of what the final swap
    /// paid above the threshold. Same account list, different discriminator.
    pub fn creator_withdraw_surplus(&self) -> (Instruction, Vec<AccountInfo<'info>>) {
        (
            Instruction {
                program_id: DBC_PROGRAM_ID,
                accounts: self.quote_only_metas(),
                data: creator_withdraw_surplus_data(),
            },
            self.quote_only_infos(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// sha256 of `global:<name>`, so the constants above can be checked
    /// against the convention they were read from without pulling in a hash
    /// crate the on-chain build would then carry.
    fn anchor_discriminator(name: &str) -> [u8; 8] {
        use sha2::{Digest, Sha256};
        let digest = Sha256::digest(format!("global:{name}").as_bytes());
        digest[..8].try_into().unwrap()
    }

    #[test]
    fn discriminators_are_the_anchor_convention() {
        for (name, expected) in [
            ("initialize_virtual_pool_with_token2022", IX_INITIALIZE_POOL_TOKEN2022),
            ("withdraw_migration_fee", IX_WITHDRAW_MIGRATION_FEE),
            ("claim_creator_trading_fee", IX_CLAIM_CREATOR_TRADING_FEE),
            ("creator_withdraw_surplus", IX_CREATOR_WITHDRAW_SURPLUS),
            ("claim_position_fee", IX_CLAIM_POSITION_FEE),
            ("withdraw_leftover", IX_WITHDRAW_LEFTOVER),
        ] {
            assert_eq!(
                anchor_discriminator(name),
                expected,
                "discriminator for {name} does not match sha256(global:{name})",
            );
        }
    }

    #[test]
    fn initialize_pool_borsh_encodes_three_strings() {
        let data = initialize_pool_data("Momentum", "MOM", "https://x/y.json");
        assert_eq!(&data[..8], &IX_INITIALIZE_POOL_TOKEN2022);
        assert_eq!(&data[8..12], &8u32.to_le_bytes());
        assert_eq!(&data[12..20], b"Momentum");
        assert_eq!(&data[20..24], &3u32.to_le_bytes());
        assert_eq!(&data[24..27], b"MOM");
        assert_eq!(&data[27..31], &16u32.to_le_bytes());
        assert_eq!(&data[31..], b"https://x/y.json");
    }

    #[test]
    fn the_seed_is_the_creators_half_of_the_migration_fee() {
        let data = withdraw_migration_fee_data();
        assert_eq!(data.len(), 9);
        assert_eq!(data[8], MIGRATION_FEE_FLAG_CREATOR);
    }

    #[test]
    fn the_creator_withdraw_flag_is_the_second_bit() {
        let pool = VirtualPool {
            config: Pubkey::default(),
            creator: Pubkey::default(),
            base_mint: Pubkey::default(),
            base_vault: Pubkey::default(),
            quote_vault: Pubkey::default(),
            is_migrated: true,
            migration_fee_withdraw_status: 0b01,
        };
        assert!(!pool.creator_migration_fee_withdrawn());
        let collected = VirtualPool {
            migration_fee_withdraw_status: 0b11,
            ..pool
        };
        assert!(collected.creator_migration_fee_withdrawn());
    }
}
