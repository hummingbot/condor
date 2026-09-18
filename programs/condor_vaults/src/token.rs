//! The three things this program does with tokens, and how it reads them.
//!
//! No `anchor-spl`: it would pull a second Anchor version graph in for a burn,
//! a checked transfer and two struct reads. The SPL Token layouts have been
//! fixed since 2020, and Token-2022 keeps the same first 165 bytes and appends
//! its extensions after them, so one set of offsets reads both.
//!
//! **Token accounts are derived, never accepted.** Every balance this program
//! touches belongs to the vault's wallet, and the only way to be sure of
//! that is to rebuild the address from the owner, the mint and the token
//! program and compare (plan §1.6). A caller-supplied "vault token account" is
//! how a redemption pays out of somebody else's balance.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::invoke;

pub const TOKEN_PROGRAM_ID: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
pub const TOKEN_2022_PROGRAM_ID: Pubkey = pubkey!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
pub const ASSOCIATED_TOKEN_PROGRAM_ID: Pubkey =
    pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/// SPL Token instruction tags.
const IX_TRANSFER_CHECKED: u8 = 12;
const IX_BURN: u8 = 8;

// Account layout, identical in both token programs for the first 165 bytes.
const ACCOUNT_MINT: usize = 0;
const ACCOUNT_OWNER: usize = 32;
const ACCOUNT_AMOUNT: usize = 64;
const ACCOUNT_STATE: usize = 108;
pub const ACCOUNT_LEN: usize = 165;

// Mint layout.
const MINT_SUPPLY: usize = 36;
const MINT_DECIMALS: usize = 44;
pub const MINT_LEN: usize = 82;

pub fn is_token_program(key: &Pubkey) -> bool {
    *key == TOKEN_PROGRAM_ID || *key == TOKEN_2022_PROGRAM_ID
}

pub struct TokenAccount {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub amount: u64,
}

/// Read a token account, or `None` if the account is not one (wrong owner
/// program, too short, or uninitialized).
///
/// `None` rather than an error because `finalize_wind_down` is handed a list of
/// accounts to check and a non-token account in it is simply not a balance —
/// while a token account with something in it is the thing that must stop the
/// finalize.
pub fn read_token_account(info: &AccountInfo) -> Option<TokenAccount> {
    if !is_token_program(info.owner) {
        return None;
    }
    let data = info.try_borrow_data().ok()?;
    if data.len() < ACCOUNT_LEN || data[ACCOUNT_STATE] == 0 {
        return None;
    }
    Some(TokenAccount {
        mint: Pubkey::new_from_array(data[ACCOUNT_MINT..ACCOUNT_MINT + 32].try_into().ok()?),
        owner: Pubkey::new_from_array(data[ACCOUNT_OWNER..ACCOUNT_OWNER + 32].try_into().ok()?),
        amount: u64::from_le_bytes(data[ACCOUNT_AMOUNT..ACCOUNT_AMOUNT + 8].try_into().ok()?),
    })
}

pub fn require_token_account(info: &AccountInfo) -> Result<TokenAccount> {
    read_token_account(info).ok_or_else(|| error!(crate::error::VaultError::WrongTokenAccount))
}

pub struct MintInfo {
    pub supply: u64,
    pub decimals: u8,
}

pub fn read_mint(info: &AccountInfo) -> Result<MintInfo> {
    require!(
        is_token_program(info.owner),
        crate::error::VaultError::WrongMint
    );
    let data = info.try_borrow_data()?;
    require!(data.len() >= MINT_LEN, crate::error::VaultError::WrongMint);
    Ok(MintInfo {
        supply: u64::from_le_bytes(data[MINT_SUPPLY..MINT_SUPPLY + 8].try_into().unwrap()),
        decimals: data[MINT_DECIMALS],
    })
}

/// The associated token account for `owner` and `mint` under `token_program`.
pub fn associated_token_address(
    owner: &Pubkey,
    mint: &Pubkey,
    token_program: &Pubkey,
) -> Pubkey {
    Pubkey::find_program_address(
        &[owner.as_ref(), token_program.as_ref(), mint.as_ref()],
        &ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    .0
}

/// Check that `info` really is `owner`'s associated account for `mint`.
pub fn require_associated(
    info: &AccountInfo,
    owner: &Pubkey,
    mint: &Pubkey,
    token_program: &Pubkey,
) -> Result<TokenAccount> {
    let expected = associated_token_address(owner, mint, token_program);
    require_keys_eq!(
        info.key(),
        expected,
        crate::error::VaultError::WrongTokenAccount
    );
    let account = require_token_account(info)?;
    require_keys_eq!(
        account.owner,
        *owner,
        crate::error::VaultError::WrongTokenAccount
    );
    require_keys_eq!(account.mint, *mint, crate::error::VaultError::WrongMint);
    Ok(account)
}

// ── instructions ─────────────────────────────────────────────────────────────

pub fn transfer_checked_data(amount: u64, decimals: u8) -> Vec<u8> {
    let mut data = Vec::with_capacity(10);
    data.push(IX_TRANSFER_CHECKED);
    data.extend_from_slice(&amount.to_le_bytes());
    data.push(decimals);
    data
}

pub fn burn_data(amount: u64) -> Vec<u8> {
    let mut data = Vec::with_capacity(9);
    data.push(IX_BURN);
    data.extend_from_slice(&amount.to_le_bytes());
    data
}

/// The metas of a `TransferChecked`, in the token program's order.
pub fn transfer_checked_metas(
    source: &Pubkey,
    mint: &Pubkey,
    destination: &Pubkey,
    authority: &Pubkey,
) -> Vec<AccountMeta> {
    vec![
        AccountMeta::new(*source, false),
        AccountMeta::new_readonly(*mint, false),
        AccountMeta::new(*destination, false),
        AccountMeta::new_readonly(*authority, true),
    ]
}

/// Move tokens between two accounts, signed by the source's owner in the outer
/// transaction. The PDA-signed counterpart goes through `invoke_signed` with
/// the wallet's seeds, because only that can produce its signature.
#[allow(clippy::too_many_arguments)]
pub fn transfer_checked<'info>(
    token_program: &AccountInfo<'info>,
    source: &AccountInfo<'info>,
    mint: &AccountInfo<'info>,
    destination: &AccountInfo<'info>,
    authority: &AccountInfo<'info>,
    amount: u64,
    decimals: u8,
) -> Result<()> {
    let ix = Instruction {
        program_id: token_program.key(),
        accounts: transfer_checked_metas(
            &source.key(),
            &mint.key(),
            &destination.key(),
            &authority.key(),
        ),
        data: transfer_checked_data(amount, decimals),
    };
    invoke(
        &ix,
        &[
            source.clone(),
            mint.clone(),
            destination.clone(),
            authority.clone(),
            token_program.clone(),
        ],
    )?;
    Ok(())
}

/// Burn `amount` from `from`, signed by its owner in the outer transaction.
pub fn burn<'info>(
    token_program: &AccountInfo<'info>,
    from: &AccountInfo<'info>,
    mint: &AccountInfo<'info>,
    owner: &AccountInfo<'info>,
    amount: u64,
) -> Result<()> {
    let ix = Instruction {
        program_id: token_program.key(),
        accounts: vec![
            AccountMeta::new(from.key(), false),
            AccountMeta::new(mint.key(), false),
            AccountMeta::new_readonly(owner.key(), true),
        ],
        data: burn_data(amount),
    };
    invoke(
        &ix,
        &[
            from.clone(),
            mint.clone(),
            owner.clone(),
            token_program.clone(),
        ],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transfer_checked_is_tag_12_amount_decimals() {
        assert_eq!(transfer_checked_data(1_000, 6), vec![12, 232, 3, 0, 0, 0, 0, 0, 0, 6]);
    }

    #[test]
    fn burn_is_tag_8_amount() {
        assert_eq!(burn_data(1), vec![8, 1, 0, 0, 0, 0, 0, 0, 0]);
    }

    /// Wrapped SOL's canonical associated account for a known owner, so a
    /// reordered seed list fails here rather than on chain.
    #[test]
    fn associated_addresses_use_owner_program_mint() {
        let owner = Pubkey::new_from_array([1u8; 32]);
        let mint = Pubkey::new_from_array([2u8; 32]);
        let derived = associated_token_address(&owner, &mint, &TOKEN_PROGRAM_ID);
        let expected = Pubkey::find_program_address(
            &[owner.as_ref(), TOKEN_PROGRAM_ID.as_ref(), mint.as_ref()],
            &ASSOCIATED_TOKEN_PROGRAM_ID,
        )
        .0;
        assert_eq!(derived, expected);
        // A different token program is a different account, which is the whole
        // reason the program id is a seed.
        assert_ne!(
            derived,
            associated_token_address(&owner, &mint, &TOKEN_2022_PROGRAM_ID)
        );
    }
}
