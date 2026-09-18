use anchor_lang::prelude::*;

#[error_code]
pub enum VaultError {
    #[msg("basis points must be at most 10000")]
    BpsOutOfRange,
    #[msg("the account is not a DBC virtual pool")]
    PoolNotDbc,
    #[msg("the pool was not launched from the protocol's DBC config")]
    PoolConfigMismatch,
    #[msg("the pool's creator is not this vault's authority")]
    PoolCreatorMismatch,
    #[msg("the pool's base mint is not this vault's mint")]
    PoolMintMismatch,
    #[msg("the pool has not migrated yet, so there is no seed to collect")]
    PoolNotMigrated,
    #[msg("the creator's migration fee has already been collected")]
    SeedAlreadyCollected,
    #[msg("version counter overflow")]
    VersionOverflow,
    #[msg("the account is not a Swig wallet account")]
    NotSwigAccount,
    #[msg("the Swig account is not the one this vault was created for")]
    SwigMismatch,
    #[msg("this program's authority PDA holds no role on the Swig account")]
    NoRootRole,
    #[msg("the delegate holds no role on the Swig account")]
    NoDelegateRole,
    #[msg("the signer is not the program's upgrade authority")]
    NotUpgradeAuthority,
    #[msg("the signer is not the protocol authority")]
    NotAuthority,
    #[msg("the signer is not the protocol administrator")]
    NotAdministrator,
    #[msg("the signer is not this vault's runner")]
    NotRunner,
    #[msg("the vault has no strategy pinned yet")]
    NotPinned,
    #[msg("the vault is already pinned; publish a new version instead")]
    AlreadyPinned,
    #[msg("the vault has not been tokenized yet")]
    NotTokenized,
    #[msg("the vault is already tokenized")]
    AlreadyTokenized,
    #[msg("the vault is tokenized: nobody may withdraw from it")]
    NotPrivate,
    #[msg("the DBC config's terms are not the ones every Condor vault launches with")]
    LaunchTermsMismatch,
    #[msg("the migration fee must be between 20% and 80%: it is the split between the vault's capital and its holders' exit liquidity")]
    MigrationFeeOutOfRange,
    #[msg("the config is not the one this vault's pool was launched from")]
    PoolConfigMismatchForPool,
    #[msg("the vault is winding down; its strategy can no longer change")]
    WindingDown,
    #[msg("the vault is not winding down")]
    NotWindingDown,
    #[msg("the vault is not redeemable yet")]
    NotRedeemable,
    #[msg("the wind-down is not finished: a position or a balance remains in the wallet")]
    WindDownIncomplete,
    #[msg("the redemption pot is empty: sweep the quote asset out of the wallet before finalizing")]
    RedemptionPotEmpty,
    #[msg("the token account is not the vault's own for this mint")]
    WrongTokenAccount,
    #[msg("the mint does not match the vault's")]
    WrongMint,
    #[msg("the quote asset does not match the vault's")]
    WrongQuoteMint,
    #[msg("nothing is redeemable: the supply outside the pool is zero")]
    NothingToRedeem,
    #[msg("the redemption would pay zero — redeem a larger amount")]
    RedemptionTooSmall,
    #[msg("arithmetic overflow")]
    MathOverflow,
    #[msg("the token name, symbol or URI is too long")]
    MetadataTooLong,
}
