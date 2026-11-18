# Passify: bugs fixed per commit

Branch `feat/passification`, every commit from Wednesday 11 Nov 2026 onwards (`c6f19e5` … `d84be1d`).
Written 2026-11-18. Companion files: `passify-bugs-and-skips.md` (bugs introduced, open bugs, where passify is
skipped) and `passified-integration-tests.md` (which tests actually exercise passify).

## How this was determined

- Every commit was built in a separate clone and `cargo test` was run on it (89 unit tests, 90 integration tests).
- A test only turns green once *every* bug hitting it is gone, so pass/fail per commit understates what each commit
  fixed. Each fix was therefore also **reverted on its own**, both at HEAD and at the commit that introduced it, and
  the suite was rerun. "N tests" below means: N tests fail when only that one fix is reverted.
- Fixes the suite does not cover were checked with small HeyVL programs; they are inlined in
  `passify-bugs-and-skips.md`.
- Logs and scripts are in `/work/target/claude-history/` (`results/<commit>`, `results/ab_*` for reverts at HEAD,
  `results/h_*` for reverts at the commit itself). It is inside the gitignored `target/`; delete it when no longer
  needed.

## Test results per commit

| Commit | Date | Unit failures | Integration failures | Change vs. previous |
|---|---|---|---|---|
| `f76110a` | upstream base | 0 | 0 | – |
| `597016c` | Tue 10 Nov (state at start of Wed) | 20 | 78 | – |
| `c6f19e5` | Wed 11 Nov | does not compile | | |
| `e5db7d0` | Thu 12 Nov | does not compile | | |
| `c00af65` | Sat 14 Nov | 23 | 77 | +1 (spurious, see below), −3 |
| `0d9251c` | Sun 15 Nov | 23 | 74 | +7, −4 |
| `f8ee2d5` | Sun 15 Nov | 19 | 72 | +6 |
| `9c98855` | Mon 16 Nov | no Rust change | | |
| `e5186c7` | Mon 16 Nov | 19 | 72 | ±0 |
| `4c16d8e` | Mon 16 Nov | 19 | 72 | ±0 |
| `c14ada9` | Mon 16 Nov | 18 | 72 | +2, −1 |
| `c0da6a3` | Mon 16 Nov | 18 | 72 | ±0 |
| `632a5db` | Mon 16 Nov | 14 | 64 | +12 |
| `6bf36d3` | Tue 17 Nov | 12 | 64 | +2 |
| `0dc4c80` | Tue 17 Nov | 4 | 22 | +50 |
| `a897618` | Tue 17 Nov | 1 | 5 | +20 |
| `b581562` | Tue 17 Nov | 1 | 5 | ±0 |
| `3c159c1` | Tue 17 Nov | 0 | 4 | +2 |
| `b8f1039` | Tue 17 Nov | 0 | 4 | ±0 |
| `e7febba` | Wed 18 Nov | 0 | 0 | +4 |
| `bbe78e1` | Wed 18 Nov | test files only | | |
| `d84be1d` | Wed 18 Nov | 0 | 0 | ±0 |

## Fixes per commit

### `c6f19e5` — Wed 11 Nov, "snapshot EOD 11.11."
Does not compile: `PassifyError` cannot go through `?` into `Result<(), ()>` in `CoreVerifyTask::passify`.
- The right-hand side of an assignment now gets the current substitution applied (it was a TODO).

### `e5db7d0` — Thu 12 Nov, "first version of the nondet encode"
Does not compile (same error).
- `passify_identifier` no longer reuses an existing passified variable, so every assignment gets a fresh one.

### `c00af65` — Sat 14 Nov, "the most temporary commit"
- Build fix (`?` → `.unwrap()`). Side effect: a direction mismatch now panicked, breaking 3 unit tests
  (fixed in `6bf36d3`).
- Branches of `if \cap` / `if \cup` were visited twice (once by `walk_stmt`, once by the branch encoder), so the
  join found no differences and the last branch's value leaked out. Not covered by the suite; a demonic choice that
  assigns in both branches gives a false counterexample without this fix.
- The new "Before/After passification" `println!`s made `mccarthy-fail` pass regardless of the result: its
  `grep -q "post #0 is part of the error"` matched the `@error_msg` in the printed program. It also passes without
  the prints (checked), and `d84be1d` removed them.

### `0d9251c` — Sun 15 Nov, "partly working first version"
- `?(x == e)` was typed `Bool` instead of `EUReal`, so SMT translation panicked ("expr is not of type EUReal",
  "illegal exprkind Embed"). This was the largest failure at the baseline; 26 tests need the fix at HEAD.
- The assume polarity was inverted (negated for `Down` instead of `Up`), which encodes x ≠ e instead of x = e
  (22 tests).
- `assert`, `assume`, `havoc` and `tick` were not rewritten and still referred to the original variables
  (assert 21 tests, assume 9, havoc 7; `tick` has no test, `x = 1; tick x` gives a false counterexample).
- Introduced a regression: the new `havoc` handling's `.expect(...)` panics on parameters and uninitialised
  variables (11 tests regressed). Worked around in `f8ee2d5`, fixed in `0dc4c80`.

### `f8ee2d5` — Sun 15 Nov, "fix some of the tests"
- Identity mapping for `var x` without initializer: works around the `havoc` panic (3 unit tests). Superseded by
  `0dc4c80`.
- `if` conditions were not substituted (`test_ast_function_conditions`; 12 tests at HEAD).
- `compare` was not substituted. Procedure calls are desugared into `assert pre; havoc outputs; compare post`, so
  every proc call was affected (`factorial`, `mccarthy`; 6 tests at HEAD).
- Introduced: `weigh e` rewritten as `tick e` (fixed in `e7febba`), and the `compare` direction overwrite that is
  still open (see the bugs file).

### `9c98855` — Mon 16 Nov, "fix the benchmarking"
Not covered by `cargo test`.
- All 14 `tests/…` paths in `benchmarks.txt` had been dead since upstream reorganised the tests (`7b693bd`,
  July 2025); `ohfive` was dropped because the file no longer exists.
- scooter ran `../target/release/caesar`, but `run-benchmarks.fish` changes into the repo root first.

### `e5186c7` — Mon 16 Nov, "ensure a deterministic order for our substitution"
- `HashMap` → `IndexMap`: the passified program differed from run to run (nested substitutions in a different
  order). `fibonacci.heyvl` produced 9 different passified programs in 10 runs before, 1 after. No pass/fail effect.

### `4c16d8e` — Mon 16 Nov, "fix which vars actually need to be passified"
- Fixed `x = x + 1` becoming `assume x_1 == x_1 + 1` (the left-hand side was renamed before the right-hand side was
  substituted). The fix excluded `x` from the substitution, so every later reassignment referred to the *original*
  `x` instead of its latest version (fixed properly in `632a5db`). No visible change at the time because other
  panics hid it; at HEAD the old bug breaks 5 tests, this intermediate version 12.

### `c14ada9` — Mon 16 Nov, "unify the branching statements"
- `if` branches were processed one after the other with shared state: the else-branch saw the then-branch's
  variables, the condition was substituted after both branches, and there was no join. `if` now uses the same
  branch/join logic as `\cap`/`\cup` (2 tests at the time, 12 at HEAD; `smol.heyvl` is this pattern).
- It also exposed the `havoc` panic in `mccarthy` (fixed in `0dc4c80`).

### `c0da6a3` — Mon 16 Nov, "refactor: we never needed the closure"
- No fix. Introduced `assume x_j == x_j` in the join for identity-mapped variables (fixed in `632a5db`).

### `632a5db` — Mon 16 Nov, "refactor: trim the havoc+assume logic down even further"
- Correct SSA order: substitute the right-hand side first, then create the fresh variable. Fixes both `4c16d8e`
  problems: 12 tests (`fibonacci`, `ast-flipflop`, `demonically_fair`, `negative_binomial`, `coupon_collector_core`,
  `rabin_hurd2004`, `kind_brp1`, `countdown_core`, and 4 unit tests).
- Removed `c0da6a3`'s `x_j == x_j` from the join (`mccarthy` needs this at HEAD).

### `6bf36d3` — Tue 17 Nov, "only passify when the direction matches"
- `--no-passify` was ignored; passify always ran.
- A direction mismatch (`\cup` in a proc, `\cap` in a coproc) panicked; it now falls back to the unpassified
  program (the 2 omega refutation tests). Passify now works on a copy, so a failure no longer leaves a half-rewritten
  block.

### `0dc4c80` — Tue 17 Nov, "a bunch of small bug fixes and improvements" (50 tests)
- An assignment whose right-hand side is a call (`flip`, `store`, …) panicked in `encode_assign(...).unwrap()`
  (33 tests).
- `havoc` of a variable that was never passified (parameters; the havocs from loop encodings and procedure calls)
  panicked in `.expect(...)` (51 tests).
- Dropping `f8ee2d5`'s identity mapping fixed no test, but it introduced a regression the suite does not cover
  (your `counter.heyvl::j`, fixed in `b581562`).
- Introduced: the skipped call assignment stays in place, but later uses still point at the variable's previous
  passified copy (fixed in `a897618`; your `past.heyvl` dump shows it).

### `a897618` — Tue 17 Nov, "don't try to work around procdecls for now and don't passify them"
- A call on the right-hand side of an assignment or `var` initializer now abandons passification for the whole
  task instead of leaving an outdated copy in use: 20 tests (`brp2`, `rabin2`, `unif_gen3`, `noisy_or`, several
  `refute-*_bmc`, `aiming-low-example39`, `test_ast_variant_at_exit`, `test_past_accepts_probabilistic_countdown`, …).

### `b581562` — Tue 17 Nov, "fix the branch join to include all passified vars"
- A variable assigned in only one branch was never joined. No suite change, but `counter.heyvl::j` gives a false
  counterexample without it; `mccarthy` needs it at HEAD. The join variable was still left unconstrained in the
  other branch (fixed in `e7febba`).

### `3c159c1` — Tue 17 Nov, "stop trying to fiddle with assignments right hand sides"
- Passify set `init = None` on the `var x = e` declaration, which copies of a loop body (and procedures generated by
  proof rules) share. When passification was abandoned, the fallback program read `var x: T` with no initializer.
  Fixes `list_ops` (`var i: UInt = 0` lost after bailing out on `res = store(…)`) and `test_ast_pre_and_post`
  (`var amount`, visible in your `ast_pre_and_post.heyvl`); `coupon-collector` also needs it at HEAD. Upstream
  SpecCall has the same bug (your `spec_call_var_init_unroll.heyvl`), still unfixed.

### `b8f1039` — Tue 17 Nov, "add the passified var's declaration to what we insert"
- Adds `var x_k` before each `havoc`. Changes no verification result anywhere (all tests and the repro programs
  give identical results without it); only the printed program changes.

### `e7febba` — Wed 18 Nov, "this contains way too many small changes…" (4 tests)
- Calls to user-defined `func`s hit `unreachable!("Everything but intrinsics…")` (`binary-tree-sum`,
  `triangular-theorem`).
- In a branch without its own version of a joined variable, the join variable is now set equal to the original
  variable instead of being left unconstrained (`mccarthy`).
- Variables of list type are no longer passified (`coupon-collector`, which otherwise ends in "Unknown result";
  your `hint_list_eq.heyvl` and `sum_lemma.heyvl` investigated this).
- The `weigh` → `tick` bug from `f8ee2d5` is fixed. It was unsound: `counter.heyvl::w` (`weigh 0`, pre 1)
  *verified* with the bug.

### `bbe78e1` — Wed 18 Nov, "alllllllll the test procs i had lying around"
- Test files only.

### `d84be1d` — Wed 18 Nov, "this cannot change anything pls no"
- Removes the before/after passification `println!`s from `c00af65`.
- Adds `warn!("repassified …")` whenever a variable gets a new version. That happens on every ordinary
  reassignment, so it is noise once `RUST_LOG=warn` is set; silent by default.
- Otherwise comments and Display helpers; no change to any passify decision, and the full suite still passes.
