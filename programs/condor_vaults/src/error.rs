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
    #[msg("the pool has not graduated yet, so there is no seed to collect")]
    PoolNotGraduated,
    #[msg("the seed has already been collected")]
    SeedAlreadyCollected,
    #[msg("version counter overflow")]
    VersionOverflow,
    #[msg("the signer is not the program's upgrade authority")]
    NotUpgradeAuthority,
    #[msg("the signer is not the protocol authority")]
    NotAuthority,
    #[msg("the signer is not the protocol administrator")]
    NotAdministrator,
    #[msg("the signer is not this vault's creator")]
    NotCreator,
    #[msg("the vault has no strategy pinned yet")]
    NotPinned,
    #[msg("the vault is already pinned; publish a new version instead")]
    AlreadyPinned,
    #[msg("the vault has not been tokenized yet")]
    NotTokenized,
    #[msg("the vault is already tokenized")]
    AlreadyTokenized,
    #[msg("the vault is tokenized: its treasury acts only through `execute`, which cannot move a token to anyone")]
    NotPrivate,
    #[msg("the treasury must be funded with at least the rent floor for an empty account, or it would not exist")]
    FundingBelowRent,
    #[msg("the signer is neither this vault's creator nor its delegate")]
    NotCreatorOrDelegate,
    #[msg("the vault is not running or paused, so its treasury does not act")]
    VaultNotActive,
    #[msg("the treasury will not invoke this program")]
    SelfInvoke,
    #[msg("that program is not a venue the treasury may trade on")]
    ProgramNotAllowed,
    #[msg("that instruction is not one the treasury may send to that program")]
    InstructionNotAllowed,
    #[msg("a writable account is neither the treasury's, the venue's, nor the caller's own — see the log for which")]
    AccountNotAllowed,
    #[msg("an account this call created is not owned by the treasury — see the log for which")]
    RecipientNotVault,
    #[msg("the DBC config's terms are not the ones every Condor vault launches with")]
    LaunchTermsMismatch,
    #[msg("the locked liquidity must be between 20% and 80%: it is the split between the holders' exit depth and the vault's capital")]
    LockedLiquidityOutOfRange,
    #[msg("the config is not the one this vault's pool was launched from")]
    PoolConfigMismatchForPool,
    #[msg("the vault is winding down; its strategy can no longer change")]
    WindingDown,
    #[msg("the vault is not winding down")]
    NotWindingDown,
    #[msg("the vault is not redeemable yet")]
    NotRedeemable,
    #[msg("the wind-down is not finished: a position or a balance remains in the treasury")]
    WindDownIncomplete,
    #[msg("the treasury holds none of the quote asset, so there is nothing for holders to redeem")]
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
