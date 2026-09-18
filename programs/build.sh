#!/usr/bin/env bash
# Builds condor_vaults: the IDL through `anchor idl build`, then the program
# itself through `cargo build-sbf` with the default platform-tools.
#
# Two toolchain traps, both of which cost the prototype a day:
#
#   * `anchor idl build` resolves the toolchain OUTSIDE the crate directory, so
#     it ignores rust-toolchain.toml and picks rustup's `stable`. On a machine
#     whose stable is behind, the solana-* crates refuse to compile.
#     RUSTUP_TOOLCHAIN pins it explicitly.
#   * Anchor 1.0.2's CLI pins platform-tools v1.52, and a v1.52 build of an
#     Anchor 1.0 program crashes at entry on a local Solana 4.1 validator
#     ("Access violation in unknown section", a few hundred CU in). The .so is
#     therefore built here with the DEFAULT platform-tools. `anchor idl build`
#     is a host build and leaves no SBF output, so the two cannot mix — but
#     `anchor build` run by hand does, and its v1.52 rlibs land in
#     target/sbpf-solana-solana under the same rustc version cargo would report
#     for the default. Cargo then reuses them and the .so crashes at entry even
#     though this script built it. If that happens:
#     `rm -rf target/sbpf-solana-solana` and build again.
#
#   programs/build.sh            IDL + program
#   programs/build.sh --program  program only (IDL untouched)
set -euo pipefail
cd "$(dirname "$0")/.."   # the Anchor workspace root
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$PATH"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.91.0}"
if [ "${1:-}" != "--program" ]; then
  mkdir -p target/idl   # anchor writes the file but will not create its directory
  (cd programs/condor_vaults && anchor idl build -o ../../target/idl/condor_vaults.json)
fi
# Force a relink: both platform-tools report the same rustc version, so cargo
# would otherwise reuse whatever is already in target/.
touch programs/condor_vaults/src/lib.rs
cargo build-sbf --manifest-path programs/condor_vaults/Cargo.toml --sbf-out-dir target/deploy
