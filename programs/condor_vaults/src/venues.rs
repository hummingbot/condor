//! Where a treasury may go under `execute`, and what it may hand over there.
//!
//! The rule is about **recipients**, because that is where every custody
//! design that looked at anything else failed (plan §1.8): a DEX swap names
//! its output account as an ordinary account and checks only its mint, so a
//! treasury that may "only send tokens to pool vaults" can still swap its USDC
//! into a pool and have the SOL land in whoever's account the caller wrote
//! down. So instead of asking where value *leaves to*, this asks, for every
//! account the instruction may write: is it the treasury's, the venue's, or the
//! caller's own? Anything else is refused before the call is made, and what
//! the call *created* is checked after it returns.
//!
//! The rule is structural rather than an allowlist of pools. That is where a
//! manager's freedom comes from: a pool created this morning passes because
//! its reserves are owned by the venue's own authority, and it is where a
//! holder's assurance comes from: a recipient that is a key fails, whoever's
//! key it is — the manager's, Condor's, anyone's.
//!
//! What a holder is trusting is this file: the venue list and the few lines
//! that say how each venue owns its pool accounts. Both are pinned to the
//! program's layout and extended by upgrade.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::system_program;

use crate::dbc::{DAMM_V2_POOL_AUTHORITY, DAMM_V2_PROGRAM_ID, TOKEN_2022_PROGRAM_ID};
use crate::error::VaultError;
use crate::token;

pub const METEORA_DLMM: Pubkey = pubkey!("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
pub const RAYDIUM_CLMM: Pubkey = pubkey!("CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK");
pub const SPL_TOKEN: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
pub const ASSOCIATED_TOKEN: Pubkey = pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/// The venues a tokenized vault may trade on. v1: the three the LP strategies
/// use. Routers are absent on purpose — see plan §1.9 — and so is anything
/// this file does not know how to recognise a pool account of.
pub const VENUES: [Pubkey; 3] = [METEORA_DLMM, DAMM_V2_PROGRAM_ID, RAYDIUM_CLMM];

pub fn is_venue(program: &Pubkey) -> bool {
    VENUES.contains(program)
}

fn is_token_program(program: &Pubkey) -> bool {
    *program == SPL_TOKEN || *program == TOKEN_2022_PROGRAM_ID
}

/// What was true before the call, for the checks that can only be made after.
pub struct Before {
    /// Accounts that did not exist yet: something the venue is about to
    /// create, whose owner is not knowable until it has.
    pub fresh: Vec<Pubkey>,
}

/// Refuse anything the treasury must not do, before it does it.
pub fn check_before(
    program: &Pubkey,
    treasury: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> Result<Before> {
    if is_token_program(program)
        || *program == ASSOCIATED_TOKEN
        || *program == system_program::ID
    {
        check_base_program(program, treasury, accounts, data)?;
        return Ok(Before { fresh: Vec::new() });
    }
    require!(is_venue(program), VaultError::ProgramNotAllowed);

    // Who may own a token account the venue writes to: the venue's pool
    // authorities. DLMM's pairs own their own reserves and Raydium's pool
    // state owns its vaults, so "an account in this call that the venue owns"
    // covers both; DAMM v2 uses one fixed authority for every pool.
    let mut pool_authorities: Vec<Pubkey> = accounts
        .iter()
        .filter(|a| a.owner == program)
        .map(|a| a.key())
        .collect();
    if *program == DAMM_V2_PROGRAM_ID {
        pool_authorities.push(DAMM_V2_POOL_AUTHORITY);
    }

    let mut fresh = Vec::new();
    for account in accounts {
        if !account.is_writable {
            continue;
        }
        let key = account.key();
        if key == *treasury {
            continue; // the treasury itself: rent it pays, lamports it receives
        }
        if account.is_signer {
            continue; // the caller, spending their own key's lamports
        }
        if let Some(holding) = token::read_token_account(account) {
            if holding.owner == *treasury || pool_authorities.contains(&holding.owner) {
                continue;
            }
            msg!("token account {} is owned by {}, which is neither the treasury nor the venue", key, holding.owner);
            return Err(VaultError::AccountNotAllowed.into());
        }
        if account.owner == program {
            continue; // pool state, bin arrays, positions: the venue's own invariants
        }
        // Not yet an account at all: a position or token account the venue is
        // about to create. Allowed now, and what it became is checked after.
        // A *funded* system account with no data is somebody's wallet, and is
        // refused — that is exactly a rent receiver someone else named.
        if *account.owner == system_program::ID && account.data_is_empty() && account.lamports() == 0 {
            fresh.push(key);
            continue;
        }
        msg!("account {} (owned by {}) is not the treasury's, the venue's, or the caller's", key, account.owner);
        return Err(VaultError::AccountNotAllowed.into());
    }
    Ok(Before { fresh })
}

/// Whatever the call created now has an owner. It had better be the treasury.
pub fn check_after(program: &Pubkey, treasury: &Pubkey, accounts: &[AccountInfo], before: &Before) -> Result<()> {
    for key in &before.fresh {
        let Some(account) = accounts.iter().find(|a| a.key() == *key) else { continue };
        if *account.owner == system_program::ID {
            continue; // never created after all
        }
        if let Some(holding) = token::read_token_account(account) {
            // A new token account — the treasury's ATA for a mint it had not
            // held, or the account that holds a position NFT. Either way, the
            // treasury's.
            if holding.owner != *treasury {
                msg!("new token account {} is owned by {}, not the treasury", key, holding.owner);
                return Err(VaultError::RecipientNotVault.into());
            }
            continue;
        }
        if account.owner == program {
            if *program == METEORA_DLMM {
                // PositionV2: discriminator, lb_pair, then owner. DLMM records
                // the owner as data, so it is checked here; the NFT venues
                // record it as the holder of a token account, checked above.
                let data = account.try_borrow_data()?;
                let owned_by_wallet =
                    data.len() >= 72 && Pubkey::new_from_array(data[40..72].try_into().unwrap()) == *treasury;
                if !owned_by_wallet {
                    msg!("new DLMM position {} is not owned by the treasury", key);
                    return Err(VaultError::RecipientNotVault.into());
                }
            }
            continue;
        }
        msg!("new account {} ended up owned by {}, which is not the venue", key, account.owner);
        return Err(VaultError::RecipientNotVault.into());
    }
    Ok(())
}

/// The four programs every venue instruction is made of, admitted for the
/// handful of things a trading treasury needs from them and nothing else. A
/// token transfer is conspicuously absent: under `execute` there is no such
/// thing as the treasury sending a token to an account, only to a venue.
fn check_base_program(program: &Pubkey, treasury: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> Result<()> {
    let key_at = |i: usize| -> Result<Pubkey> {
        accounts.get(i).map(|a| a.key()).ok_or_else(|| error!(VaultError::InstructionNotAllowed))
    };
    if *program == ASSOCIATED_TOKEN {
        // Create / CreateIdempotent: [payer, ata, owner, mint, system, token].
        // The owner is the treasury; the payer is whoever signed.
        let create = data.is_empty() || data == [0] || data == [1];
        require!(create && key_at(2)? == *treasury, VaultError::InstructionNotAllowed);
        return Ok(());
    }
    if is_token_program(program) {
        let owned_by_wallet = |i: usize| -> Result<bool> {
            Ok(accounts
                .get(i)
                .and_then(token::read_token_account)
                .is_some_and(|t| t.owner == *treasury))
        };
        match data.first() {
            // SyncNative on the treasury's own wrapped-SOL account.
            Some(17) => require!(owned_by_wallet(0)?, VaultError::InstructionNotAllowed),
            // CloseAccount [account, destination, authority]: the treasury's own
            // account, rent back to the treasury.
            Some(9) => require!(owned_by_wallet(0)? && key_at(1)? == *treasury, VaultError::InstructionNotAllowed),
            _ => return Err(VaultError::InstructionNotAllowed.into()),
        }
        return Ok(());
    }
    // System program.
    let disc = data.get(..4).map(|b| u32::from_le_bytes(b.try_into().unwrap()));
    match disc {
        // Transfer [from, to]: only the treasury wrapping SOL into its own
        // token account. To a key it is a withdrawal, and refused.
        Some(2) => {
            let to_is_wallets_token_account = accounts
                .get(1)
                .and_then(token::read_token_account)
                .is_some_and(|t| t.owner == *treasury);
            require!(key_at(0)? == *treasury && to_is_wallets_token_account, VaultError::InstructionNotAllowed);
        }
        // CreateAccount [payer, new]: the caller pays. The treasury paying to
        // create an account some other program will own is lamports leaving.
        Some(0) => {
            let payer = accounts.first().ok_or_else(|| error!(VaultError::InstructionNotAllowed))?;
            require!(payer.is_signer && payer.key() != *treasury, VaultError::InstructionNotAllowed);
        }
        _ => return Err(VaultError::InstructionNotAllowed.into()),
    }
    Ok(())
}
