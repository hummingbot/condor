//! `execute_unchecked` and `execute` — the treasury acting.
//!
//! The vault's treasury is a PDA of this program, so anything it does on chain
//! is a CPI this program makes on its behalf. These two instructions are that
//! CPI, and the difference between them is the whole difference between the
//! two phases (plan §1.9):
//!
//! * **`execute_unchecked`** invokes any program with any accounts. It is what
//!   a private vault's delegate — or its creator — trades with, and it is also
//!   how a private vault is emptied: a transfer is an instruction like any
//!   other. Almost nothing is checked, because almost nothing needs to be: the
//!   only person with a claim on what is inside is the one who created it
//!   (§1.3). The exception is handing out *authority* — an SPL approval
//!   outlives the private phase and `tokenize` cannot revoke it, so
//!   `venues::check_private` refuses those. It refuses everything from
//!   `tokenize` onward, and that refusal is the one-way door.
//! * **`execute`** invokes an allowed venue under `venues::check_before` and
//!   `check_after`: every account the call may write is the treasury's, the
//!   venue's, or the caller's own, and everything the call created belongs to
//!   the treasury. A swap whose output goes anywhere but the treasury fails; a
//!   position opened for anyone but the treasury fails; a transfer to a key
//!   fails. It is allowed in both phases and is the only thing allowed after
//!   launch — which is what lets a creator install their *own* key as delegate
//!   on a tokenized vault: what the key may do is decided here, not by whose
//!   it is.
//!
//! The target program and every account it wants come as `remaining_accounts`,
//! with the writability and signer flags the instruction needs. The treasury
//! cannot sign the outer transaction, so its signer flag is set here and
//! supplied by `invoke_signed`.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::invoke_signed;

use crate::error::VaultError;
use crate::state::{Vault, VaultState, TREASURY_SEED, VAULT_SEED};
use crate::venues;

#[derive(Accounts)]
pub struct Execute<'info> {
    /// The creator or the delegate.
    pub signer: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the treasury, signing by its seeds. Writable because it pays rent
    /// and receives lamports in the ordinary course of trading.
    #[account(
        mut,
        seeds = [TREASURY_SEED, vault.id.as_ref()],
        bump = vault.treasury_bump,
    )]
    pub treasury: UncheckedAccount<'info>,
    /// CHECK: the program the treasury invokes. Checked executable in the handler.
    pub target_program: UncheckedAccount<'info>,
    // remaining_accounts: the instruction's accounts, in order.
}

/// `states` differ between the two instructions, and the difference matters:
/// a wind-down's whole middle act — close every position, swap every balance to
/// the quote asset — is `execute` calls. Refusing them in `WindingDown` would
/// strand every open position forever, because nothing can move the vault back
/// to `Running`. `execute_unchecked` stays out of that state: by then the vault
/// is tokenized anyway, so it is refused twice over.
fn may_act<'info>(ctx: &Context<'info, Execute<'info>>, states: &[VaultState]) -> Result<()> {
    let vault = &ctx.accounts.vault;
    require!(vault.may_act(&ctx.accounts.signer.key()), VaultError::NotCreatorOrDelegate);
    require!(states.contains(&vault.state), VaultError::VaultNotActive);
    require!(ctx.accounts.target_program.executable, VaultError::ProgramNotAllowed);
    // Not itself: a treasury that could call `install_delegate` or `tokenize`
    // as the creator would be a treasury that could rewrite who owns it.
    require_keys_neq!(ctx.accounts.target_program.key(), crate::ID, VaultError::SelfInvoke);
    Ok(())
}

fn invoke_as_treasury<'info>(ctx: &Context<'info, Execute<'info>>, data: Vec<u8>) -> Result<()> {
    let treasury = ctx.accounts.treasury.key();
    let metas: Vec<AccountMeta> = ctx
        .remaining_accounts
        .iter()
        .map(|a| AccountMeta {
            pubkey: a.key(),
            is_signer: a.is_signer || a.key() == treasury,
            is_writable: a.is_writable,
        })
        .collect();
    let ix = Instruction {
        program_id: ctx.accounts.target_program.key(),
        accounts: metas,
        data,
    };
    let mut infos: Vec<AccountInfo<'info>> = ctx.remaining_accounts.to_vec();
    infos.push(ctx.accounts.treasury.to_account_info());
    infos.push(ctx.accounts.target_program.to_account_info());
    let seeds = ctx.accounts.vault.treasury_seeds();
    invoke_signed(&ix, &infos, &[&seeds])?;
    Ok(())
}

pub fn execute_unchecked<'info>(ctx: Context<'info, Execute<'info>>, data: Vec<u8>) -> Result<()> {
    may_act(&ctx, &[VaultState::Running, VaultState::Paused])?;
    require!(!ctx.accounts.vault.is_tokenized(), VaultError::NotPrivate);
    venues::check_private(&ctx.accounts.target_program.key(), &data)?;
    invoke_as_treasury(&ctx, data)
}

pub fn execute<'info>(ctx: Context<'info, Execute<'info>>, data: Vec<u8>) -> Result<()> {
    may_act(
        &ctx,
        &[
            VaultState::Running,
            VaultState::Paused,
            VaultState::WindingDown,
        ],
    )?;
    let program = ctx.accounts.target_program.key();
    let treasury = ctx.accounts.treasury.key();
    let before = venues::check_before(&program, &treasury, ctx.remaining_accounts, &data)?;
    invoke_as_treasury(&ctx, data)?;
    venues::check_after(&program, &treasury, ctx.remaining_accounts, &before)
}
