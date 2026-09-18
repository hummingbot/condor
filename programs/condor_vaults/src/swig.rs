//! The Swig wallet's wire format, and the four calls this program makes into it.
//!
//! ## Why these are hand-written
//!
//! Swig *does* publish Rust crates: `swig-sdk`, `swig-interface`, `swig-state`,
//! `swig-compact-instructions` and `swig-cli`, in
//! github.com/anagrambuild/swig-wallet at v3.0.0, all **AGPL-3.0** and all
//! git-only in practice. Three things were checked before deciding against
//! them.
//!
//! **`cargo add swig-sdk` gets you somebody else's crate.** There *is* a
//! `swig-sdk` on crates.io — version 0.3.2, MIT, "Standalone Rust SDK for the
//! Swig wallet protocol on Solana" — and it is not Swig's. It is published by a
//! different account and the repository it names,
//! github.com/bitrouter/swig-sdk, returns 404. Swig's own Rust SDK is the
//! `rust-sdk/` directory of the repo above, at 3.0.0, and the name on crates.io
//! is taken, so it is reachable only as a git dependency. Anyone reaching for
//! the obvious install line lands on an unofficial crate with a dead repo, for
//! a library whose whole job is encoding wallet instructions. Hence this note.
//!
//! **The official crate with these exact builders cannot go on chain.**
//! `swig-interface` depends on `solana-sdk`, which pulls `getrandom`, which has
//! no SBF backend: `cargo build-sbf` fails outright. `swig-sdk` is further out
//! still — it depends on `solana-client`. Both are client-side.
//!
//! **What would work was measured and declined.** `swig-state` and
//! `swig-compact-instructions` are pinocchio-based and *do* build for SBF
//! alongside Anchor, so a real on-chain path exists for the role-table reader
//! and the compact-instruction encoder below. Three things decided against it:
//!
//! * **Licence.** All of them are AGPL-3.0 and Condor is MIT.
//! * **Size, measured on this binary.** Using `swig-state`'s
//!   `SwigWithRoles::lookup_role_id` in place of `role_id_of` took the `.so`
//!   from 499,024 to 537,960 bytes — **+38,936 for one function**. Nearly all
//!   of it is `libsecp256k1`, which their reader pulls in to support Secp256k1
//!   and Secp256k1Session authorities. This program only ever uses Ed25519, so
//!   that is 39 KB of capability it deliberately does not want. (The packer
//!   alone was +696 bytes — the cost is entirely `swig-state`.)
//! * **The maintenance saving is smaller than it looks.** A deployed `.so` has
//!   its dependencies baked in, so a breaking Swig upgrade costs the same
//!   either way; the crate would only shorten the eventual fix.
//!
//! Taking them would also mean two frameworks in one binary — Anchor and
//! pinocchio, the latter at two versions (0.9.3 and 0.8.4) — plus shank,
//! num_enum, murmur3 and no-padding. Going the other way, dropping Anchor for
//! pure pinocchio, is worse still: it would cost the IDL that Gateway's client
//! builds every vault instruction from, and the `has_one` / `seeds` /
//! `address =` constraints that are load-bearing security here.
//!
//! So the layouts below are built by hand — but they are not guesswork. Every
//! one has been checked against both of Swig's own implementations:
//!
//! * the **TypeScript SDK** (`@swig-wallet/classic` 2.1.0, `@swig-wallet/coder`),
//!   byte-for-byte, by the `create_v1_matches_the_sdk` test at the bottom;
//! * the **Rust source** (`swig-state` v3.0.0, `state/src/role.rs` and
//!   `state/src/swig.rs`), read directly: `Position` is `#[repr(C, align(8))]`
//!   with `authority_type: u16` at 0, `authority_length: u16` at 2, `id: u32` at
//!   8 and `boundary: u32` at 12, for 16 bytes; the `Swig` header is 48 bytes
//!   with `roles: u16` at offset 34. Both match the constants below.
//!
//! ## What can break this, and it is not hypothetical
//!
//! The Swig program is **upgradeable**: on mainnet its programdata account
//! names upgrade authority `8o2ZThbZ5Bky4RcPVBYjyWuzVtqfwfqPMbsboTkFf3aQ`.
//! So the account layout and the instruction format below can change *at the
//! same program id*, without warning, by a third party.
//!
//! Two things follow, and only the second is about this file:
//!
//! * every Condor vault reaches its funds through that program, so a breaking
//!   Swig upgrade is a systemic risk to all of them at once — and it is the
//!   same risk whether these layouts are hand-written or taken from a crate,
//!   because a deployed `.so` has its dependencies baked in. It belongs in the
//!   threat model, not in a dependency decision;
//! * tracking Swig's own crates would make the *fix* smaller when it happens.
//!   That is the real argument for them, and it is weighed against the licence
//!   above.
//!
//! The mitigation that does not cost a licence is a behavioural test on the
//! fork: create a Swig, add a role, and assert that the role id this file finds
//! is the one Swig's own `sign` accepts. That catches drift by using the
//! program rather than by agreeing with a struct definition.
//!
//! **Why this program touches Swig at all.** A vault's Swig has the program's
//! own PDA as its root authority (plan D3). That is what makes the seed
//! untouchable: there is no key anywhere that can withdraw, only instructions
//! in this file, reached only through the handlers that are allowed to call
//! them. The delegate that trades is a *second* role, added and removed by the
//! root — so replacing the administrator moves no funds and needs no migration.
//!
//! ## Layouts
//!
//! Every integer is little-endian.
//!
//! ```text
//! PDAs
//!   swig account   ["swig", id(32)]
//!   funds owner    ["swig-wallet-address", swig_account]     (a.k.a. the system address)
//!
//! CreateV1  disc 0   accounts [swig(w), payer(ws), funds_owner(w), system]
//!   u16 disc | u16 authority_type | u16 authority_len | u8 swig_bump | u8 wallet_bump
//!   [32] id | [authority_len] authority | [..] actions
//!
//! AddAuthorityV1  disc 1  accounts [swig(w), payer(ws), system, authority(s)]
//!   u16 disc | u16 new_authority_len | u16 actions_len | u16 new_authority_type
//!   u8 no_of_actions | [3] pad | u32 acting_role_id
//!   [new_authority_len] new_authority | [actions_len] actions | [1] authority_index
//!
//! RemoveAuthorityV1  disc 2  accounts [swig(w), payer(ws), system, authority(s)]
//!   u16 disc | u16 authority_payload_len | [4] pad | u32 acting_role_id
//!   u32 role_to_remove | [1] authority_index
//!
//! SignV2  disc 11  accounts [swig(w), funds_owner(w), authority(s), ...inner]
//!   u16 disc | u16 instruction_payload_len | u32 role_id
//!   u8 count, then per inner instruction:
//!     u8 program_index | u8 account_count | [account_count] account_indexes
//!     u16 data_len | [data_len] data
//!   [1] authority_index
//!
//! An action is an 8-byte header: u32 permission, u32 total_length. The two
//! this program uses carry no payload, so the header is the whole action.
//! ```

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::{invoke, invoke_signed};

/// The Swig wallet program, deployed and immutable on mainnet.
pub const SWIG_PROGRAM_ID: Pubkey = pubkey!("swigypWHEksbC64pWKwah1WTeh9JXwx8H1rJHLdbQMB");

pub const SWIG_SEED: &[u8] = b"swig";
pub const SWIG_WALLET_SEED: &[u8] = b"swig-wallet-address";

const DISC_CREATE_V1: u16 = 0;
const DISC_ADD_AUTHORITY_V1: u16 = 1;
const DISC_REMOVE_AUTHORITY_V1: u16 = 2;
const DISC_SIGN_V2: u16 = 11;

/// `AuthorityType::Ed25519`. A PDA is an Ed25519-shaped address that happens to
/// be off the curve; Swig checks `is_signer`, which a CPI grants, so the root
/// authority being a PDA is invisible to it. Proving that on the fork is the
/// first task of M3 (plan D3).
const AUTHORITY_ED25519: u16 = 1;

/// `Permission::All` — the root role.
const PERMISSION_ALL: u32 = 7;
/// `Permission::AllButManageAuthority` — the delegate role: sign anything,
/// never touch the role table, so the root can always revoke.
const PERMISSION_ALL_BUT_MANAGE_AUTHORITY: u32 = 15;
const ACTION_HEADER_LEN: u32 = 8;

// The account layout, for reading the role table.
const SWIG_HEADER_LEN: usize = 48;
const SWIG_DISCRIMINATOR_CONFIG: u8 = 1;
const SWIG_ROLES_OFFSET: usize = 34;
const SWIG_POSITION_LEN: usize = 16;
const SWIG_AUTHORITY_ED25519: u16 = 1;
const SWIG_AUTHORITY_ED25519_SESSION: u16 = 2;

fn action_bytes(permission: u32) -> [u8; 8] {
    let mut out = [0u8; 8];
    out[..4].copy_from_slice(&permission.to_le_bytes());
    out[4..].copy_from_slice(&ACTION_HEADER_LEN.to_le_bytes());
    out
}

/// The Swig account address for an id, and its bump.
pub fn swig_pda(id: &[u8; 32]) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[SWIG_SEED, id], &SWIG_PROGRAM_ID)
}

/// The funds owner: where the wallet's SOL, token accounts and LP positions
/// live. This is the address connectors treat as "the wallet".
pub fn swig_funds_owner(swig_account: &Pubkey) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[SWIG_WALLET_SEED, swig_account.as_ref()], &SWIG_PROGRAM_ID)
}

// ── instruction data ─────────────────────────────────────────────────────────

pub fn create_v1_data(
    id: &[u8; 32],
    root_authority: &Pubkey,
    swig_bump: u8,
    wallet_bump: u8,
) -> Vec<u8> {
    let mut data = Vec::with_capacity(80);
    data.extend_from_slice(&DISC_CREATE_V1.to_le_bytes());
    data.extend_from_slice(&AUTHORITY_ED25519.to_le_bytes());
    data.extend_from_slice(&32u16.to_le_bytes());
    data.push(swig_bump);
    data.push(wallet_bump);
    data.extend_from_slice(id);
    data.extend_from_slice(root_authority.as_ref());
    data.extend_from_slice(&action_bytes(PERMISSION_ALL));
    data
}

/// Add the delegate role: `allButManageAuthority`, no spend caps.
///
/// Caps are deliberately absent (plan §1.3): they fight LP flows — an ATA rent
/// debit or a wrap will hit a SOL cap mid-swap — and the honest statement is
/// that the installed administrator is trusted while installed. What the role
/// cannot do is rewrite the role table, so the root can always take it back.
pub fn add_delegate_data(acting_role_id: u32, delegate: &Pubkey, authority_index: u8) -> Vec<u8> {
    let actions = action_bytes(PERMISSION_ALL_BUT_MANAGE_AUTHORITY);
    let mut data = Vec::with_capacity(16 + 32 + actions.len() + 1);
    data.extend_from_slice(&DISC_ADD_AUTHORITY_V1.to_le_bytes());
    data.extend_from_slice(&32u16.to_le_bytes());
    data.extend_from_slice(&(actions.len() as u16).to_le_bytes());
    data.extend_from_slice(&AUTHORITY_ED25519.to_le_bytes());
    data.push(1); // one action
    data.extend_from_slice(&[0u8; 3]);
    data.extend_from_slice(&acting_role_id.to_le_bytes());
    data.extend_from_slice(delegate.as_ref());
    data.extend_from_slice(&actions);
    data.push(authority_index);
    data
}

pub fn remove_authority_data(
    acting_role_id: u32,
    role_to_remove: u32,
    authority_index: u8,
) -> Vec<u8> {
    let mut data = Vec::with_capacity(17);
    data.extend_from_slice(&DISC_REMOVE_AUTHORITY_V1.to_le_bytes());
    data.extend_from_slice(&1u16.to_le_bytes()); // authority payload: one index byte
    data.extend_from_slice(&[0u8; 4]);
    data.extend_from_slice(&acting_role_id.to_le_bytes());
    data.extend_from_slice(&role_to_remove.to_le_bytes());
    data.push(authority_index);
    data
}

/// One inner instruction, already resolved to indexes into the outer account list.
pub struct CompactInstruction {
    pub program_index: u8,
    pub account_indexes: Vec<u8>,
    pub data: Vec<u8>,
}

pub fn sign_v2_data(
    role_id: u32,
    inner: &[CompactInstruction],
    authority_index: u8,
) -> Vec<u8> {
    let mut payload = Vec::new();
    payload.push(inner.len() as u8);
    for ix in inner {
        payload.push(ix.program_index);
        payload.push(ix.account_indexes.len() as u8);
        payload.extend_from_slice(&ix.account_indexes);
        payload.extend_from_slice(&(ix.data.len() as u16).to_le_bytes());
        payload.extend_from_slice(&ix.data);
    }
    let mut data = Vec::with_capacity(8 + payload.len() + 1);
    data.extend_from_slice(&DISC_SIGN_V2.to_le_bytes());
    data.extend_from_slice(&(payload.len() as u16).to_le_bytes());
    data.extend_from_slice(&role_id.to_le_bytes());
    data.extend_from_slice(&payload);
    data.push(authority_index);
    data
}

// ── the four calls ───────────────────────────────────────────────────────────

/// Create the Swig, with `root_authority` (this program's PDA) as role 0.
///
/// Not `invoke_signed`: the root authority is *data* in the create instruction,
/// not a signer of it. Only the payer signs, which is the runner.
pub fn create<'info>(
    swig_program: &AccountInfo<'info>,
    swig_account: &AccountInfo<'info>,
    payer: &AccountInfo<'info>,
    funds_owner: &AccountInfo<'info>,
    system_program: &AccountInfo<'info>,
    id: &[u8; 32],
    root_authority: &Pubkey,
    swig_bump: u8,
    wallet_bump: u8,
) -> Result<()> {
    let ix = Instruction {
        program_id: SWIG_PROGRAM_ID,
        accounts: vec![
            AccountMeta::new(swig_account.key(), false),
            AccountMeta::new(payer.key(), true),
            AccountMeta::new(funds_owner.key(), false),
            AccountMeta::new_readonly(system_program.key(), false),
        ],
        data: create_v1_data(id, root_authority, swig_bump, wallet_bump),
    };
    invoke(
        &ix,
        &[
            swig_account.clone(),
            payer.clone(),
            funds_owner.clone(),
            system_program.clone(),
            swig_program.clone(),
        ],
    )?;
    Ok(())
}

/// Add `delegate` as a role, signed by the root PDA.
#[allow(clippy::too_many_arguments)]
pub fn add_delegate<'info>(
    swig_program: &AccountInfo<'info>,
    swig_account: &AccountInfo<'info>,
    payer: &AccountInfo<'info>,
    system_program: &AccountInfo<'info>,
    root_authority: &AccountInfo<'info>,
    root_role_id: u32,
    delegate: &Pubkey,
    signer_seeds: &[&[&[u8]]],
) -> Result<()> {
    // The authority sits at index 3 of the account list below; the trailing
    // byte of the instruction data points Swig at it.
    const AUTHORITY_INDEX: u8 = 3;
    let ix = Instruction {
        program_id: SWIG_PROGRAM_ID,
        accounts: vec![
            AccountMeta::new(swig_account.key(), false),
            AccountMeta::new(payer.key(), true),
            AccountMeta::new_readonly(system_program.key(), false),
            AccountMeta::new_readonly(root_authority.key(), true),
        ],
        data: add_delegate_data(root_role_id, delegate, AUTHORITY_INDEX),
    };
    invoke_signed(
        &ix,
        &[
            swig_account.clone(),
            payer.clone(),
            system_program.clone(),
            root_authority.clone(),
            swig_program.clone(),
        ],
        signer_seeds,
    )?;
    Ok(())
}

/// Remove a role, signed by the root PDA.
#[allow(clippy::too_many_arguments)]
pub fn remove_role<'info>(
    swig_program: &AccountInfo<'info>,
    swig_account: &AccountInfo<'info>,
    payer: &AccountInfo<'info>,
    system_program: &AccountInfo<'info>,
    root_authority: &AccountInfo<'info>,
    root_role_id: u32,
    role_to_remove: u32,
    signer_seeds: &[&[&[u8]]],
) -> Result<()> {
    const AUTHORITY_INDEX: u8 = 3;
    let ix = Instruction {
        program_id: SWIG_PROGRAM_ID,
        accounts: vec![
            AccountMeta::new(swig_account.key(), false),
            AccountMeta::new(payer.key(), true),
            AccountMeta::new_readonly(system_program.key(), false),
            AccountMeta::new_readonly(root_authority.key(), true),
        ],
        data: remove_authority_data(root_role_id, role_to_remove, AUTHORITY_INDEX),
    };
    invoke_signed(
        &ix,
        &[
            swig_account.clone(),
            payer.clone(),
            system_program.clone(),
            root_authority.clone(),
            swig_program.clone(),
        ],
        signer_seeds,
    )?;
    Ok(())
}

/// Execute one inner instruction as the wallet, signed by the root PDA.
///
/// The outer account list is `[swig, funds_owner, root_authority]` followed by
/// the inner program and then the inner instruction's own accounts, so the
/// indexes this builds are stable and computed here rather than passed in.
/// Swig clears the signer flag on its own PDAs, which is what lets the
/// funds-owner appear as the authority of a token transfer.
pub fn sign_as_wallet<'info>(
    swig_program: &AccountInfo<'info>,
    swig_account: &AccountInfo<'info>,
    funds_owner: &AccountInfo<'info>,
    root_authority: &AccountInfo<'info>,
    root_role_id: u32,
    inner_program: &AccountInfo<'info>,
    inner_accounts: &[AccountInfo<'info>],
    inner_metas: &[AccountMeta],
    inner_data: Vec<u8>,
    signer_seeds: &[&[&[u8]]],
) -> Result<()> {
    const AUTHORITY_INDEX: u8 = 2;
    let mut metas = vec![
        AccountMeta::new(swig_account.key(), false),
        AccountMeta::new(funds_owner.key(), false),
        AccountMeta::new_readonly(root_authority.key(), true),
    ];
    let program_index = metas.len() as u8;
    metas.push(AccountMeta::new_readonly(inner_program.key(), false));

    let mut account_indexes = Vec::with_capacity(inner_metas.len());
    for meta in inner_metas {
        // Swig itself drops the signer flag on its own PDAs; doing it here too
        // keeps the outer transaction from demanding a signature nobody holds.
        let is_signer = meta.is_signer
            && meta.pubkey != funds_owner.key()
            && meta.pubkey != swig_account.key();
        let existing = metas.iter().position(|m| m.pubkey == meta.pubkey);
        match existing {
            Some(index) => {
                if meta.is_writable {
                    metas[index].is_writable = true;
                }
                if is_signer {
                    metas[index].is_signer = true;
                }
                account_indexes.push(index as u8);
            }
            None => {
                account_indexes.push(metas.len() as u8);
                metas.push(AccountMeta {
                    pubkey: meta.pubkey,
                    is_signer,
                    is_writable: meta.is_writable,
                });
            }
        }
    }

    let compact = CompactInstruction {
        program_index,
        account_indexes,
        data: inner_data,
    };
    let ix = Instruction {
        program_id: SWIG_PROGRAM_ID,
        accounts: metas,
        data: sign_v2_data(root_role_id, &[compact], AUTHORITY_INDEX),
    };

    let mut infos = vec![
        swig_account.clone(),
        funds_owner.clone(),
        root_authority.clone(),
        inner_program.clone(),
    ];
    infos.extend(inner_accounts.iter().cloned());
    infos.push(swig_program.clone());
    invoke_signed(&ix, &infos, signer_seeds)?;
    Ok(())
}

// ── reading the role table ───────────────────────────────────────────────────

/// The id of the role whose Ed25519 authority is `signer`, if it holds one.
///
/// Walks the role table the way the account stores it: a 48-byte header whose
/// `roles` count sits at offset 34, then one 16-byte `Position` per role
/// (`authority_type u16`, `authority_length u16`, `num_actions u16`, pad,
/// `id u32` at 8, `boundary u32` at 12) followed by the authority bytes, an
/// Ed25519 authority's first 32 being its public key.
pub fn role_id_of(info: &AccountInfo, signer: &Pubkey) -> Result<Option<u32>> {
    require_keys_eq!(
        *info.owner,
        SWIG_PROGRAM_ID,
        crate::error::VaultError::NotSwigAccount
    );
    let data = info.try_borrow_data()?;
    require!(
        data.len() >= SWIG_HEADER_LEN && data[0] == SWIG_DISCRIMINATOR_CONFIG,
        crate::error::VaultError::NotSwigAccount
    );
    let roles = u16::from_le_bytes([data[SWIG_ROLES_OFFSET], data[SWIG_ROLES_OFFSET + 1]]) as usize;
    let table = &data[SWIG_HEADER_LEN..];
    let mut cursor = 0usize;
    for _ in 0..roles {
        require!(
            cursor + SWIG_POSITION_LEN <= table.len(),
            crate::error::VaultError::NotSwigAccount
        );
        let position = &table[cursor..cursor + SWIG_POSITION_LEN];
        let authority_type = u16::from_le_bytes([position[0], position[1]]);
        let authority_len = u16::from_le_bytes([position[2], position[3]]) as usize;
        let role_id = u32::from_le_bytes(position[8..12].try_into().unwrap());
        let boundary = u32::from_le_bytes(position[12..16].try_into().unwrap()) as usize;
        let authority_start = cursor + SWIG_POSITION_LEN;
        if (authority_type == SWIG_AUTHORITY_ED25519
            || authority_type == SWIG_AUTHORITY_ED25519_SESSION)
            && authority_len >= 32
            && authority_start + 32 <= table.len()
            && table[authority_start..authority_start + 32] == signer.to_bytes()
        {
            return Ok(Some(role_id));
        }
        require!(
            boundary > cursor && boundary <= table.len(),
            crate::error::VaultError::NotSwigAccount
        );
        cursor = boundary;
    }
    Ok(None)
}

/// How many roles the account carries. A finished wind-down leaves one.
pub fn role_count(info: &AccountInfo) -> Result<u16> {
    require_keys_eq!(
        *info.owner,
        SWIG_PROGRAM_ID,
        crate::error::VaultError::NotSwigAccount
    );
    let data = info.try_borrow_data()?;
    require!(
        data.len() >= SWIG_HEADER_LEN && data[0] == SWIG_DISCRIMINATOR_CONFIG,
        crate::error::VaultError::NotSwigAccount
    );
    Ok(u16::from_le_bytes([
        data[SWIG_ROLES_OFFSET],
        data[SWIG_ROLES_OFFSET + 1],
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Byte-for-byte against `getCreateSwigInstruction` from
    /// `@swig-wallet/classic` 2.1.0, for id = 32 × 0x07 and root authority
    /// `11111111111111111111111111111112` (bumps 254 / 255).
    #[test]
    fn create_v1_matches_the_sdk() {
        let id = [7u8; 32];
        let root = Pubkey::new_from_array({
            let mut b = [0u8; 32];
            b[31] = 1;
            b
        });
        let data = create_v1_data(&id, &root, 254, 255);
        let expected = "000001002000feff\
                        0707070707070707070707070707070707070707070707070707070707070707\
                        0000000000000000000000000000000000000000000000000000000000000001\
                        0700000008000000";
        assert_eq!(hex(&data), expected);
        assert_eq!(data.len(), 80);
    }

    #[test]
    fn the_delegate_role_is_all_but_manage_authority() {
        let delegate = Pubkey::new_from_array([2u8; 32]);
        let data = add_delegate_data(0, &delegate, 3);
        // header: disc 1, authority len 32, actions len 8, type 1, 1 action,
        // 3 pad, acting role 0
        assert_eq!(&data[..16], &[
            1, 0, 32, 0, 8, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0
        ]);
        assert_eq!(&data[16..48], delegate.as_ref());
        // Permission::AllButManageAuthority = 15, header-only action.
        assert_eq!(&data[48..56], &[15, 0, 0, 0, 8, 0, 0, 0]);
        assert_eq!(data[56], 3);
        assert_eq!(data.len(), 57);
    }

    #[test]
    fn remove_names_the_role_not_the_key() {
        let data = remove_authority_data(0, 1, 3);
        assert_eq!(data, vec![2, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 3]);
    }

    #[test]
    fn sign_v2_length_prefixes_the_compact_payload() {
        let inner = CompactInstruction {
            program_index: 3,
            account_indexes: vec![4, 1, 5],
            data: vec![0xaa, 0xbb],
        };
        let data = sign_v2_data(9, &[inner], 2);
        // disc 11, payload len, role 9
        assert_eq!(&data[..2], &[11, 0]);
        let payload_len = u16::from_le_bytes([data[2], data[3]]) as usize;
        assert_eq!(&data[4..8], &9u32.to_le_bytes());
        // 1 instruction: program 3, 3 accounts, indexes, u16 data len, data
        assert_eq!(&data[8..8 + payload_len], &[1, 3, 3, 4, 1, 5, 2, 0, 0xaa, 0xbb]);
        assert_eq!(payload_len, 10);
        assert_eq!(*data.last().unwrap(), 2);
    }

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }
}
