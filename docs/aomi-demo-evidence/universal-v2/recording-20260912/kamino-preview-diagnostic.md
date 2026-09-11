# Kamino preview investigation — 2026-09-12

The initial preview failed before commit with Anchor `InvalidProgramExecutable` (3009), account `klend_program`, in instruction 1 (Kamino vault Deposit). Source was LiteSVM; 36,253 compute units. `commit_attempted` and `committed` were both false. The preceding associated token-account instruction succeeded. Private executor details remain in the separate private artifact and are not reproduced here.

The saved Deposit instruction names `KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD` at account index 8. Read-only checks after the failure showed this account executable on both the local fork (18899) and credential-safe upstream relay (18897), owned by the upgradeable BPF loader. Its ProgramData deployment slot was 440486775 on both, with matching ELF SHA-256 `9db16dd4b7bbfe4f13df850bf880bfc4522fcece06717c0626d625746a3cc85b`. Kamino vault ProgramData also matched: deployment slot 432874668, ELF SHA-256 `abe18b58539e82ed54d33f1c09451a584ecbb35b3a24995336b16fbe695a4689`.

Source inspection at backend checkout 8513f88c0:
- `aomi/crates/anvil/src/svm/clone.rs`: executable CPI account metas route through `clone_program`; deduplicated account traversal prevents a second direct overwrite for the same key. Missing accounts are skipped, while fetched nonexecutable accounts are installed as returned.
- LiteSVM 0.11.0 `add_program_internal` explicitly installs upgradeable program accounts with executable=true. Program cache stores ELF plus loader; each simulation installs those into a fresh VM.
- `aomi/crates/svm/src/tx/ltesvm/mod.rs`: program and touched-account cloning precede simulation. Failure envelopes preserve logs and compute, but not initial account snapshots.

No deterministic loader defect was established. A transient missing/nonexecutable account response during initial fork prefetch remains plausible. Later executable=true reads cannot prove what the simulator received initially. Process-lifetime ELF caching does not track deployment changes, but identical current fork/upstream hashes and old deployment slots do not support a recent upgrade as this incident's cause.

No code edit, service restart, transaction submission, or simulation retry was performed for this investigation. Parent will perform a fresh UI preview serially after completing the PumpSwap exit. Retain the failed preview as evidence rather than treating a later success as proof of the initial cause.

Follow-up: fresh UI preview `79vXvDTpYymtbwCaYvWyiMnfMKuPtC4JVvxAZsBXNHng` passed on the same backend and mirror without code changes or restart, after the read-only program inspection. The initial 3009 failure did not reproduce on that next actual preview. This does not establish the precise cause.
