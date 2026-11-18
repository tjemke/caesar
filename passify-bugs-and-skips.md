# Passify: bugs introduced, open bugs, and where passify is skipped

Branch `feat/passification`, state at `d84be1d` (2026-11-18). Companion files: `passify-fixes-by-commit.md`
(what each commit fixed) and `passified-integration-tests.md` (which tests actually exercise passify).
All claims below were checked by running caesar; the reproduction programs are at the end of this file.

## Open bugs

1. **`compare`/`cocompare` get the task's direction** (introduced in `f8ee2d5`). The `Compare` arm in
   `src/opt/passify.rs` writes `StmtKind::Compare(self.direction, …)` instead of keeping the statement's own direction.
   This is **unsound**: both programs in [compare_dir](#compare_dir) fail with `--no-passify` but *verify* with
   passification. The test suite only contains the `compare`s that procedure calls are desugared into, which already
   have the task's direction, so it never notices.
2. **`if ⊕` (additive choice, `StmtKind::Additive`) is never handled.** It falls through to `_ => {}`, so `walk_stmt`
   processes both branches one after the other with shared state and no join (the same bug `if` had before
   `c14ada9`). [additive](#additive) verifies with `--no-passify` but gets a false counterexample with passification.
   `c14ada9` unified `if`/`⊓`/`⊔` but left `⊕` out.
3. Cosmetic / minor:
   - The inserted `var x_k` declarations copy the original variable's initializer, because `clone_var` clones the
     whole declaration (`var x_1: UInt = 0; havoc x_1; assume …`). Harmless because of the immediate `havoc`, but
     misleading when reading the output. `d84be1d` has a TODO about it.
   - `Result<[Spanned<StmtKind>; 1 | 3], _>` is just `[_; 3]`: `1 | 3` is a bitwise or.
   - The `while` arm substitutes the condition after `walk_stmt` has already processed the body (same pattern as the
     old `if` bug). Loops are desugared before passify, so this does not trigger in practice.
   - The `warn!("repassified …")` added in `d84be1d` fires on every ordinary reassignment, so it is noise as soon as
     `RUST_LOG=warn` is set (silent by default).

Resolved in `d84be1d`: the before/after passification `println!`s on stdout (from `c00af65`).

Not yours (upstream), found along the way:
- SpecCall has the same shared-declaration bug that `3c159c1` fixed in passify: `spec_call_var_init_unroll.heyvl`
  (in the repo root) still fails with and without `--no-passify`.
- `--function-encoding define-fun-rec` reports "does not support disabling function strengthening" when
  strengthening is *on* (`src/driver/smt_proof.rs`). To use it you have to pass `--no-partial-strengthening`.

## Bugs introduced and later fixed

| Bug | Introduced | Fixed |
|---|---|---|
| `?(x == e)` typed `Bool` → SMT panics | `489aed5` (Mon 9 Nov) | `0d9251c` |
| Polarity inverted (encodes x ≠ e); `Not` typed `Bool` | `597016c` (Tue 10 Nov) | `0d9251c` |
| `encode_assign(...).unwrap()` panics when the right-hand side is a call | `597016c` | `0dc4c80` |
| `init = None` on the shared `var` declaration corrupts loop copies, rule-generated procs and the fallback program | `597016c` | `3c159c1` |
| `--no-passify` ignored | `4162aa5` | `6bf36d3` |
| Build broken (`PassifyError` through `?`); `c6f19e5` and `e5db7d0` never compiled | `c6f19e5` | `c00af65` |
| `passify_identifier` reused an existing passified variable | `c6f19e5` | `e5db7d0` |
| Left-hand side renamed before the right-hand side is substituted: `x = x + 1` → `assume x_1 == x_1 + 1` | `c6f19e5` | `4c16d8e` (partly), `632a5db` |
| `⊓`/`⊔` branches visited twice → no join, last branch's value leaks | `e5db7d0` | `c00af65` |
| `.unwrap()` turned a direction mismatch into a panic | `c00af65` | `6bf36d3` |
| Debug `println!` of the whole program on stdout (made `mccarthy-fail`'s grep pass regardless) | `c00af65` | `d84be1d` |
| `havoc` `.expect(...)` panics on parameters and uninitialised variables | `0d9251c` | `f8ee2d5` (workaround), `0dc4c80` |
| `weigh e` rewritten as `tick e` (unsound: `counter.heyvl::w` verified) | `f8ee2d5` | `e7febba` |
| `if` condition substituted after both branches were processed | `f8ee2d5` | `c14ada9` |
| Right-hand side refers to the original `x` instead of its latest version | `4c16d8e` | `632a5db` |
| Join emits `assume x_j == x_j` for identity-mapped variables | `c0da6a3` | `632a5db` |
| Skipped call assignment leaves later uses pointing at an outdated passified copy | `0dc4c80` | `a897618` |
| Dropping the identity mapping lost the join for `var y; if c { y = 5 } else {}` (`counter.heyvl::j` fails at `0dc4c80` and `a897618`) | `0dc4c80` | `b581562` |
| Join variable unconstrained in the branch that didn't assign it | `b581562` (incomplete fix) | `e7febba` |

## Where passify is skipped

### The whole task falls back to the unpassified program
A task is one procedure, or one of the procedures a proof rule generates. Any of these aborts passification for the
whole task (`CoreVerifyTask::passify` passifies a copy and keeps the original on error):

1. `--no-passify` is given.
2. **Direction mismatch:** `if ⊔` (angelic) in a lower-bound task (`proc`), or `if ⊓` (demonic) in an upper-bound
   task (`coproc`). → `PassifyError::DirectionMismatch`
3. **The right-hand side is a call**, in an assignment or a `var` initializer: built-in procedures like `flip`/`ber`,
   built-in functions like `len`/`select`/`store`, and user-defined `func`s. → `PassifyError::NotYetSupported`
   - Only when the call is the *entire* right-hand side; see the next section for nested calls.
   - Procedure calls are rewritten into `assert`/`havoc`/`compare` before passify runs, so they are passified.
   - The skip is required for `flip`/`ber`, since they are random. For pure functions it is only conservative.
4. **The assigned variable has a list type (`[]T`).** → `PassifyError::NotYetSupported`

### Statements left as they are while passification continues
- `var x: T` without an initializer.
- `havoc`/`cohavoc`: renamed to the variable's current copy without creating a new one (correct, because a later
  `havoc` of the same name shadows the earlier one).
- `negate`, `validate`, labels; annotations (their inner statement is still processed).
- `if ⊕` (open bug 2).
- `while`: only its condition.

### How often each case occurs in the test suite
Counted with a build that logs every bail-out, each test run with its own `RUN` command:

| | Tasks | Passified | Skipped because of … |
|---|---|---|---|
| Integration tests (90 files) | 155 | 61 (only 26 actually rename something) | 79 `flip`, 8 list variable, 2 `ber`, 2 `len`, 1 `select`, 1 `sum` (user `func`), 1 `get_value` (user `func`) |
| Unit tests | 265 | 235 | 25 `flip`, 3 `⊔` in a `proc`, 2 `⊓` in a `coproc` |

Most integration tasks never reach the passified code, so an all-green suite says less about passify than it seems.
Per-test details are in `passified-integration-tests.md`.

## Calls nested in a larger expression (e.g. `y = len(x) + 1`)

Passify treats the whole right-hand side as an ordinary expression: it creates a fresh variable and assumes it equals
the expression, with every variable inside renamed to its latest version, including variables inside call arguments:

```
y = len(l) + 1        →  { var y_0: UInt; havoc y_0; assume ?((y_0 == (len(l) + 1))) }
r = select(l, j) + 1  →  { var r_0: UInt; havoc r_0; assume ?((r_0 == ((select(l, j) + 1))[j -> j_1])) }
```

This is **sound**. `havoc v; assume ?(v == e)` (and `cohavoc v; coassume !?(v == e)` for upper bounds) has exactly
the meaning of `v := e` whenever `e` has a single value in a given state. Calls nested in an expression always have
that property, because the type checker rejects random calls there: `x = ite(flip(0.5), 1, 0)` and `x = !flip(0.5)`
both fail with "Cannot call proc `flip` nested inside of an expression".

Checked with [nested_calls](#nested_calls); every procedure there is actually passified (not skipped):

| Case | `--no-passify` | passify on |
|---|---|---|
| `y = len(l) + 1` | ok: Verified, bad: counterexample | same |
| `select(l, j) + 1` after reassigning `j` | ok: Verified, bad: counterexample | same |
| `x = f(x) + 0` twice (user `func`) | ok: Verified, bad: counterexample | same |
| `r = fib(n) + 0` (recursive `func`) | ok: Verified, bad: timeout | same |
| `r = ∞; r = r + 1` (`EUReal`) | ok: Verified, bad: counterexample | same |

The false `fib` claim could not be refuted in either mode with Z3 seeds 1–8, every `--function-encoding`, or
`--max-fuel 6`: always timeout or "unknown" in both modes, never "Verified". That is Z3 struggling with recursive
functions, not passify.

One possible cost, of completeness rather than soundness: the `*-computation` function encodings can evaluate calls
whose arguments are literals, and passification turns `fib(5)` into `fib(n_0)` with `n_0 == 5`. In these tests that
never changed a result.

## Reproduction programs

`counter.heyvl` (procedures `j` and `w`) is in the repo root. Expected results are the `--no-passify` results.

### nondet_join
Fixed in `c00af65` (branches walked twice). Expected: Verified.
```
proc nd(c: Bool) -> (r: UInt)
    pre [c]
    post [r == 0]
{
    var b0: UInt
    if \cap { assume ?(c); b0 = 0 } else { assume ?(!c); b0 = 1 }
    r = b0
}
```

### tick_subst
Fixed in `0d9251c` (`tick` not rewritten). Expected: Verified.
```
proc t() -> () pre 1 post 0 {
    var x: UInt = 0
    x = 1
    tick x
}
```

### additive
Open bug 2. Expected: Verified (vc = 1 + 2 = 3); with passification: counterexample.
```
proc a() -> (r: UInt) pre 3 post r {
    if \oplus { r = 1 } else { r = 2 }
}
```

### compare_dir
Open bug 1. `a ↘ b` is ∞ if a ≤ b, else 0; `a ↖ b` is 0 if a ≥ b, else ∞, so they differ when a = b.
Expected: both fail; with passification both verify.
```
proc cocompare_in_proc() -> () pre 1 post 1 { cocompare 1 }
coproc compare_in_coproc() -> () pre 1 post 1 { compare 1 }
```

### nested_calls
Calls nested in a larger right-hand side. `_ok` procedures verify, `_bad` ones fail (`fib_bad` times out), with and
without passification.
```
domain D {
    func f(n: UInt): UInt = n + 2
    func fib(n: UInt): UInt = ite(n <= 1, n, fib(n - 1) + fib(n - 2))
}
proc len_ok(l: []UInt) -> (y: UInt) pre ?(true) post ?(y == len(l) + 1) { y = len(l) + 1 }
proc len_bad(l: []UInt) -> (y: UInt) pre ?(true) post ?(y == len(l)) { y = len(l) + 1 }
proc sel_ok(l: []UInt, i: UInt) -> (r: UInt) pre ?(true) post ?(r == select(l, i + 1) + 1) {
    var j: UInt = i
    j = j + 1
    r = select(l, j) + 1
}
proc sel_bad(l: []UInt, i: UInt) -> (r: UInt) pre ?(true) post ?(r == select(l, i) + 1) {
    var j: UInt = i
    j = j + 1
    r = select(l, j) + 1
}
proc func_ok(n: UInt) -> (x: UInt) pre ?(true) post ?(x == n + 4) { x = n; x = f(x) + 0; x = f(x) + 0 }
proc func_bad(n: UInt) -> (x: UInt) pre ?(true) post ?(x == n + 2) { x = n; x = f(x) + 0; x = f(x) + 0 }
proc fib_ok() -> (r: UInt) pre ?(true) post ?(r == 5) { var n: UInt = 5; r = fib(n) + 0 }
proc fib_bad() -> (r: UInt) pre ?(true) post ?(r == 4) { var n: UInt = 5; r = fib(n) + 0 }
proc inf_ok() -> (r: EUReal) pre ∞ post r { r = ∞; r = r + 1 }
proc inf_bad() -> (r: EUReal) pre ∞ post r { r = 5; r = r + 1 }
```
