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

/// Venue instructions a treasury may never send, by discriminator.
///
/// The recipient rule is about accounts, and these are the instructions whose
/// damage is not an account at all. `permanent_lock_position` locks the vault's
/// liquidity forever — irreversible by any key, any instruction and any
/// upgrade, gaining the caller nothing and costing holders everything.
/// `update_position_operator` and `initialize_position_by_operator` hand a
/// position's controls to a key of the caller's choosing, which is the
/// custody question delegated to an access-control list this program does not
/// read.
///
/// Denying by discriminator is an allowlist's maintenance burden in the one
/// place the structural rule cannot reach, and it has the failure mode an
/// allowlist always has: a venue upgrade adds an instruction and this list does
/// not know. It is the price of trading on programs somebody else writes.
const DENIED: [(Pubkey, [u8; 8]); 4] = [
    // cp-amm `permanent_lock_position`
    (DAMM_V2_PROGRAM_ID, [165, 176, 125, 6, 231, 171, 186, 213]),
    // lb_clmm `update_position_operator`
    (METEORA_DLMM, [202, 184, 103, 143, 180, 191, 116, 217]),
    // lb_clmm `initialize_position_by_operator`
    (METEORA_DLMM, [251, 189, 190, 244, 117, 254, 35, 148]),
    // cp-amm `lock_position` — vesting a position to somebody else's schedule
    (DAMM_V2_PROGRAM_ID, [227, 62, 2, 252, 247, 10, 171, 185]),
];

fn is_denied(program: &Pubkey, data: &[u8]) -> bool {
    let Some(disc) = data.get(..8) else { return false };
    DENIED
        .iter()
        .any(|(p, d)| p == program && disc == d.as_slice())
}

pub fn is_token_program(program: &Pubkey) -> bool {
    *program == SPL_TOKEN || *program == TOKEN_2022_PROGRAM_ID
}

/// What a *private* vault's treasury may not do, free as that phase is.
///
/// `execute_unchecked` checks nothing on purpose: the money is the creator's
/// and they may move all of it anywhere. But an approval is not a movement. It
/// is a standing permission that outlives the phase it was granted in, and
/// `tokenize` cannot revoke one — it never sees the treasury's token accounts.
/// A delegate approved on the *empty* quote account before launch is still
/// approved when `collect_seed` fills that exact account with the holders'
/// capital, and spends it with this program never invoked and no chance to
/// refuse. So the one thing the private phase may not do is hand out authority
/// over an account, which costs a creator nothing they actually need.
pub fn check_private(program: &Pubkey, data: &[u8]) -> Result<()> {
    if !is_token_program(program) {
        return Ok(());
    }
    // Approve(4), SetAuthority(6), ApproveChecked(13).
    if matches!(data.first(), Some(4) | Some(6) | Some(13)) {
        msg!("a private vault may spend anything, but it may not delegate: an approval survives tokenize, which has no way to revoke it");
        return Err(VaultError::InstructionNotAllowed.into());
    }
    Ok(())
}

/// What was true before the call, for the checks that can only be made after.
pub struct Before {
    /// Accounts that did not exist yet: something the venue is about to
    /// create, whose owner is not knowable until it has.
    pub fresh: Vec<Pubkey>,
    /// The caller's own wallets, and what they held going in.
    ///
    /// A signer is let through because it is the caller spending their own
    /// key — paying rent for an account the venue creates. Paying is all it
    /// may do: `check_after` refuses any signer that ended the call richer.
    /// Without that, every venue instruction with a rent receiver is a
    /// withdrawal, because the treasury is the one paying.
    pub payers: Vec<(Pubkey, u64)>,
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
        return Ok(Before {
            fresh: Vec::new(),
            payers: Vec::new(),
        });
    }
    require!(is_venue(program), VaultError::ProgramNotAllowed);
    require!(!is_denied(program, data), VaultError::InstructionNotAllowed);

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
    let mut payers = Vec::new();
    for account in accounts {
        if !account.is_writable {
            continue;
        }
        let key = account.key();
        if key == *treasury {
            continue; // the treasury itself: rent it pays, lamports it receives
        }
        // Token accounts are asked who owns them *first*, and signing does not
        // excuse one. A token account's address is an ordinary pubkey, so a
        // caller can hold the key to one and sign with it; a signer exemption
        // that came first made "name your own account as the swap output" a
        // legal instruction, which is the one thing this file exists to refuse.
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
        // The caller's own wallet: a plain system account, which is all the
        // exemption ever meant. It may pay for the call; `check_after` refuses
        // it if the call paid *it*.
        if account.is_signer && *account.owner == system_program::ID && account.data_is_empty() {
            payers.push((key, account.lamports()));
            continue;
        }
        // Not yet an account at all: a position or token account the venue is
        // about to create. Allowed now, and what it became is checked after.
        if *account.owner == system_program::ID && account.data_is_empty() && account.lamports() == 0 {
            fresh.push(key);
            continue;
        }
        msg!("account {} (owned by {}) is not the treasury's, the venue's, or the caller's", key, account.owner);
        return Err(VaultError::AccountNotAllowed.into());
    }
    Ok(Before { fresh, payers })
}

/// Whatever the call created now has an owner. It had better be the treasury —
/// and whoever paid for the call had better not have been paid by it.
pub fn check_after(program: &Pubkey, treasury: &Pubkey, accounts: &[AccountInfo], before: &Before) -> Result<()> {
    for (key, was) in &before.payers {
        let Some(account) = accounts.iter().find(|a| a.key() == *key) else { continue };
        if account.lamports() > *was {
            // A rent receiver, most often: the treasury pays a venue's rent and
            // the caller names themselves to collect it back. Repeated, that is
            // the treasury's SOL leaving to a key one position at a time.
            msg!(
                "signer {} ended the call {} lamports richer; a caller may pay for a call, not be paid by it",
                key,
                account.lamports() - was
            );
            return Err(VaultError::RecipientNotVault.into());
        }
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    /// The recipient rule is about accounts; these are the instructions whose
    /// damage is not an account. If a venue upgrade renumbers one of them this
    /// test still passes — that is the standing cost of a denylist, and the
    /// reason the list is short and each entry is justified where it is
    /// declared.
    #[test]
    fn the_instructions_that_hand_away_control_are_refused() {
        let lock = [165u8, 176, 125, 6, 231, 171, 186, 213];
        assert!(is_denied(&DAMM_V2_PROGRAM_ID, &lock));
        // Same bytes, different program: a discriminator only means anything
        // against the program that defined it.
        assert!(!is_denied(&METEORA_DLMM, &lock));
        assert!(is_denied(
            &METEORA_DLMM,
            &[202, 184, 103, 143, 180, 191, 116, 217]
        ));
        assert!(!is_denied(&METEORA_DLMM, &[1, 2, 3, 4, 5, 6, 7, 8]));
        // A swap is the ordinary case and must stay ordinary.
        assert!(!is_denied(&DAMM_V2_PROGRAM_ID, &[248, 198, 158, 145, 225, 117, 135, 200]));
    }

    /// Shorter than a discriminator is not a match, and must not panic either.
    #[test]
    fn a_truncated_instruction_is_not_a_match() {
        assert!(!is_denied(&DAMM_V2_PROGRAM_ID, &[]));
        assert!(!is_denied(&DAMM_V2_PROGRAM_ID, &[165, 176, 125]));
    }

    /// A private vault may spend everything it has and may delegate nothing:
    /// an approval outlives the phase, and `tokenize` cannot take it back.
    #[test]
    fn the_private_phase_may_not_hand_out_authority() {
        for tag in [4u8, 6, 13] {
            assert!(check_private(&SPL_TOKEN, &[tag]).is_err());
            assert!(check_private(&TOKEN_2022_PROGRAM_ID, &[tag]).is_err());
        }
        // Transfer (3) and TransferChecked (12) are how a private vault is
        // emptied, which is the whole point of the phase.
        for tag in [3u8, 12, 9, 17] {
            assert!(check_private(&SPL_TOKEN, &[tag]).is_ok());
        }
        // Everything that is not a token program is somebody else's business.
        assert!(check_private(&METEORA_DLMM, &[4]).is_ok());
        assert!(check_private(&SPL_TOKEN, &[]).is_ok());
    }
}
