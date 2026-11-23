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
4. **Upper bounds (`coproc`) time out once a few flips are passified** (performance, not soundness). Found on
   `eb65f2f4` with the flip encoding enabled; see
   [Upper bounds time out on Z3](#upper-bounds-time-out-on-z3-2026-11-22).

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

## Upper bounds time out on Z3 (2026-11-22)

State: `eb65f2f4` plus the one-line change that calls `encode_intrinsic_proc` in `encode_assign`, so every `x = flip(p)`
becomes `if ⊕ { weigh p; havoc x_k; assume ?(x_k == true) } else { weigh 1 - p; havoc x_k; assume ?(x_k == false) }`.
Release build; Z3 4.15.1 (the version caesar links statically); seeds via `--z3-seed` (only sets `smt.random_seed`).
This section is newer than the rest of the file: open bug 2 and the `flip` skips above predate `c0077a0f`/`eb65f2f4`.

### Symptom
On flip-heavy loop-free programs, passify was slower than `--no-passify` in every case, and upper bounds collapse.
Every program below has an exact pre and is checked as both `proc` (`_lo`) and `coproc` (`_hi`). Median of seeds 1–5,
20 s timeout; all five seeds gave the same verdict everywhere:

| Program (n flips) | passify `_lo` | `--no-passify` `_lo` | passify `_hi` | `--no-passify` `_hi` |
|---|---|---|---|---|
| bits10: `x = 2 * x + ite(flip(1/2), 1, 0)` | 0.54 s | 0.25 s | timeout | 0.23 s |
| chain10: `b = flip(ite(b, 9/10, 1/5))` | 0.50 s | 0.24 s | timeout | 0.27 s |
| walk10: `if flip(1/2) { x = x + 1 } else { x = x - 1 }` | 0.54 s | 0.19 s | timeout | 0.17 s |
| bayesnet10: bias depends on the two previous nodes | 2.27 s | 0.60 s | timeout | 0.65 s |
| hashmix10: `h = (3 * h + ite(flip(1/3), 2, 1)) % 7` | timeout | 0.25 s | timeout | 0.26 s |
| det10: ten plain `if c_i { x = 2 * x + 1 } else { x = 2 * x }`, no flips | 1.20 s | 0.40 s | 1.82 s | 0.62 s |

`_hi` already times out at n = 5 (bits: n = 3 0.06 s, n = 4 5–8 s, n = 5 timeout). The results are right: each task was
confirmed to be passified (core contains `if +`, no `flip(`), and moving each pre by 1/100000 in the wrong direction
makes every n = 3 task fail with and without passify.

### Cause 1: the upper-bound assume is not boolified
For upper bounds `build_havoc_assume` emits `coassume !(?(x == e))` (the `Not` at `src/opt/passify.rs:109`).
Boolify only rewrites the bare embed: `?(b) → f` to `ite(b, f, ⊤)` (`src/opt/boolify.rs:45`) and `?(b) ← f` to
`ite(b, ⊥, f)` (`:64`). `Not(Embed(b))` falls through to the generic `coimplication` (`z3rro/src/orders.rs:165`),
`ite(a >= f, 0, f)`, which writes the continuation `f` twice at every coassume. Lower bounds never hit this only
because passify emits a bare `?(x == e)` there; a hand-written `assume !?(x != e)` is just as bad:

| (`--no-passify`, hand-written passified shape, bits) | n = 4 | n = 5 | n = 6 | n = 10 |
|---|---|---|---|---|
| `assume ?(x == e)` | 0.01 s | 0.01 s | 0.01 s | 0.06 s |
| `assume !?(x != e)` (same meaning) | 0.16 s | 0.22 s | 0.33 s | timeout |
| `coassume !?(x == e)` (what passify emits) | 1.87 s | timeout | timeout | |
| `coassume ?(x != e)` (same meaning, boolify matches it) | 1.16 s | timeout | timeout | |

(Times for the dumped queries on standalone Z3, `--smt-dir`, seeds 1–5.) Number of `<=` in the query, n = 4/5/6:
`assume ?(==)` 1/1/1, `assume !?(!=)` 123/251/507; `coassume !?(==)` 88/178/360, `coassume ?(!=)` 17/33/65 against
16/–/64 for `--no-passify`. So `?(x != e)` removes the duplication, but upper bounds still time out: there is a second
cause.

To handle it in boolify: `Not(Embed(b))` is `Embed(!b)`, so in the `Impl`/`CoImpl` arms (`!?(b) → f` is
`ite(b, ⊤, f)`, `!?(b) ← f` is `ite(b, f, ⊥)`) the existing rules apply with the `ite` branches swapped. The `Not` is
the left operand of the (co)implication, not around it, so the arm sees it one level down; visiting `lhs` first
(`:66`) leaves `Not(Embed(b))` unchanged. The outermost coassume sits directly under the goal `vc <= pre` and goes
through `visit_leq`'s `(?(a) <- b) <= rhs ⇒ a || (b <= rhs)` rule (`:133`), which needs the same variant
(`!a || (b <= rhs)`).

### Cause 2: in the upper direction the defining equations stay inside arithmetic
`qelim` gives every quantifier occurrence its own fresh variable (`elim_quant`, `src/opt/qelim.rs:124`). Because the
flip result and the join variables are `havoc`ed inside each branch, every path gets its own copy of the rest of the
program with its own variables (bits6: 127 distinct `x` variables with passify, none without; det6: 253). For an
expected value the per-outcome copies are unavoidable; reusing one variable across `⊕` branches would be unsound.
What makes them expensive only in the upper direction is the `EUReal` pair encoding (`z3rro/src/eureal/pair.rs`), where
a value is `(is_infinite, number)`, ∞ is `(true, 1)` and 0 is `(false, 0)`:

- `a + b` is `(a.inf ∨ b.inf, a.num + b.num)` (`pair.rs:162`) and `a <= b` is
  `b.inf ∨ (¬a.inf ∧ a.num <= b.num)` (`pair.rs:219`).
- Lower bound, goal `pre <= vc`: this is `vc.inf ∨ pre.num <= vc.num`, and `vc.inf` is the OR of "this path's guard is
  false" over all paths: purely Boolean. In the query all guards end up in one top-level disjunction
  `(or (not guard_1) … (not guard_k) (<= pre sum))`; the negated goal makes every guard a unit fact, every `ite`
  collapses and a single linear check remains.
- Upper bound, goal `vc <= pre`: this is `¬vc.inf ∧ vc.num <= pre.num`. A false guard sets its path to `(false, 0)`,
  which leaves the flag alone and only drops one summand from `vc.num`, a sum of `ite(guard_i, value_i, 0)`. Nothing
  reaches the Boolean level, so Z3 can only rule out guard combinations through arithmetic conflicts. bits, n = 4,
  standalone Z3: the lower-bound query is solved in preprocessing; the upper-bound query takes 32 290 decisions,
  22 794 conflicts (10 890 arithmetic) and 769 560 propagations.

Experiment (SMT level, no caesar change): the guards are the defining equations `v == e` of the eliminated variables.
Taking the dumped queries of the hand-written passified bits programs with 0/1 `UInt` coins (so every guard is an
`Int` equation; [up_direction](#up_direction)) and adding each eliminated variable's defining equation as a top-level
`assert` (exactly one per variable, 2^(n+2) − 3 of them), everything else unchanged. Median of seeds 1–5, 20 s:

| n | upper, original | upper, equations asserted | lower, original | lower, equations asserted |
|---|---|---|---|---|
| 3 | 0.02 s | 0.01 s | 0.01 s | 0.01 s |
| 4 | 1.19 s | 0.01 s | 0.01 s | 0.01 s |
| 5 | timeout | 0.01 s | 0.01 s | 0.01 s |
| 6 | timeout | 0.02 s | 0.01 s | 0.01 s |
| 8 | timeout | 0.03 s | 0.03 s | 0.02 s |
| 10 | timeout | 0.11 s | 0.08 s | 0.08 s |

With the equations asserted, bits n = 4 upper takes 1 decision, 2 conflicts, 120 propagations. Asserting them is the
one-point rule `sup v. ite(v == e, f, 0) = f[v := e]` (dually `inf v. ite(v == e, f, ∞)`), valid because each
eliminated variable is fresh, unique to its quantifier occurrence and only used under its own guard. Checked: with
the pre moved by 1/100000 in the wrong direction (n = 3, 4, 6, 8, both directions) the queries with the equations
asserted are still `sat` (counterexample).

Where a fix could go (neither tried in caesar):
- In `qelim_sup` (`src/opt/qelim.rs:77`): when the body of `sup v.` is `?(v != e) ← f` (or the `!?(v == e)` form),
  substitute `v := e` instead of making `v` free; the same for `inf v.` over `?(v == e) → f`.
- After `qelim`: collect each fresh variable's defining equation and add it as a hypothesis, as in the experiment.

Fixing cause 1 alone does not make upper bounds pass; see the regression tests below.

### Regression tests
- `tests/loopfree-prob/assume-negated-embed.heyvl`: lower bound, ten flips, `assume !?(v != e)`, `--timeout 5`.
  Times out now (seeds 1–3, exit code 2); with `assume ?(v == e)` it verifies in about 0.5 s. Should pass once
  cause 1 is fixed.
- `tests/loopfree-prob/coassume-negated-embed.heyvl`: upper bound, ten flips, `coassume !?(v == e)` (what passify
  emits), `--timeout 5`. Times out now; the `coassume ?(v != e)` form times out too, so it needs causes 1 and 2 fixed.

## Where passify pays off: `--no-passify` runs out of memory (2026-11-23)

State: `eb65f2f4` plus the uncommitted working tree (the `encode_intrinsic_proc` change and a boolify change that also
rewrites `!?(b) → f` / `!?(b) ← f`). Release build, one run per cell, caesar's `--timeout`. Programs generated by
`gen.py` and timed by `run.py` (both in the repo root, see [blowup](#blowup)).

### Symptom
When an assigned variable occurs more than once on the right-hand side, plain substitution doubles the vc at every
assignment, and `--no-passify` falls over long before Z3 is involved. Passify stays linear:

| Program | n | passify | `--no-passify` |
|---|---|---|---|
| `double`: `x = x + x + 1`, n times | 16 | 0.03 s | 0.28 s |
| | 20 | 0.03 s | 3.8 s, 1.6 GB peak |
| | 22 | 0.02 s | killed by the OOM killer at ~6 GB after 14 s |
| `fib`: `a = a + b; b = a + b`, n times | 15 | 0.03 s | 6.4 s |
| | 25 | 0.03 s | timeout (60 s) |
| `flipdouble`: `c = flip(1/2); x = x + x + ite(c, 1, 0)` | 10 | 0.49 s | 3.9 s |
| | 15 | 46 s | timeout (60 s) |
| `codouble` (`coproc` version of `double`) | 20 | 0.03 s | 3.8 s |
| `coflipdouble` (`coproc` version of `flipdouble`) | 4 | 3.7 s | 0.02 s |
| | 8 | timeout (30 s) | 0.29 s |

The last two rows are open bug 4 again: for probabilistic upper bounds passify is still the slower mode. Raising the
bound by one in `double` n = 22 and `flipdouble` n = 10 gives a counterexample with passify (and with `--no-passify`
for `flipdouble`; `double` runs out of memory there).

With `--timing`, every stage roughly quadruples from n to n + 2 (n = 16/18/20): `apply_subst` 153 ms / 595 ms / 2.28 s,
translation to Z3 18 ms / 67 ms / 270 ms; peak memory 137 MB / 432 MB / 1.6 GB.

### Cause: `VisitorMut` turns the shared DAG into a tree
The unfolder produces a compact DAG: `x + x + 1` points at the previous step's term twice. The first `VisitorMut` pass
after it copies every shared node:

- `Shared::deref_mut` is `Rc::make_mut` (`src/ast/shared.rs:49`): it clones the node when the reference count is > 1.
- `walk_expr` (`src/ast/visit.rs:171`) and `Subst::visit_expr` (`src/vc/subst.rs:115`) call `e.deref_mut()` on every
  node they visit, whether or not anything changes.
- For `P = A + A`, entering `P.left` clones `A` into `A'` (count 2), entering `P.right` mutates `A` in place (count is
  now 1). The result `A' + A` holds four references to `A`'s children, which are cloned on three of their four visits,
  and so on down. The copies are not temporary: they sit in the parents' child slots and *are* the new vc, so nothing
  can be freed. Only the (small) original DAG is dropped.

Measured with temporary logging of (tree size, distinct `Rc` nodes) after each stage, `double` n = 14, `--no-passify`:

| Stage | tree size | distinct nodes |
|---|---|---|
| vcgen output | 100 | 100 |
| after unfolding (`Unfolder`) | 65 546 | 56 |
| after `Qelim` | 65 546 | 56 |
| after `apply_subst` in `QuantVcProveTask::qelim` (`src/driver/quant_proof.rs:111`) | 65 546 | 65 546 |
| `trace_expr_stats`, `into_bool_vc`, `remove_parens`, `boolify` | ~65 546 | ~65 546 |

With passify the same program stays at 116–190 nodes throughout. After unfolding the vc contains no `Subst` nodes, so
that `apply_subst` changes nothing except un-sharing. Skipping it does not help: the next pass, `StatsVisitor` in
`trace_expr_stats` (`quant_proof.rs:118`), produces the same tree. Every later pass then has 2^n nodes to walk, but
does not copy again (all counts are 1). Heaptrack (n = 18) agrees: the 369 MB peak comes from ~2.1 M calls to
`clone_from_ref_in`, the copy inside `Rc::make_mut`; the stacks are too deep for heaptrack to name the pass.

Where a fix could go (not tried): take `deref_mut` only when a child actually changed and visit each shared node once
(memo on `Shared::as_ptr`), or have the passes after unfolding rebuild expressions instead of mutating in place.
Either touches every `VisitorMut` pass.

Consequence for cause 2 above: the first fix there (substituting `v := e` in `qelim_sup`) computes `f[v := e]`, which
is the unpassified vc. It would bring this blowup back on exactly the programs where passify pays off; the second fix
(asserting the defining equations) keeps the vc linear.

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

### up_direction
Open bug 4. The passified shape of five fair flips assembled into a number, written by hand so it runs with
`--no-passify`, in the boolify-friendly form `coassume ?(v != e)`. Run each procedure in its own file: `--timeout`
covers the whole run. Measured per procedure (seeds 1–5, `--no-passify --timeout 20`):

| Variant | n = 4 | n = 5 |
|---|---|---|
| as below, `coassume ?(v != e)` | verified, 2.0–2.2 s | timeout |
| `coassume !?(v == e)` instead (what passify emits) | verified, 4.3–5.6 s | timeout |

The lower-bound counterpart (`proc`, `havoc`, `assume ?(v == e)`, n = 10) verifies in about 0.5 s; with
`assume !?(v != e)` it times out. For n = 4 drop the two lines of flip 5 and use `x = x4` and `pre 15/2`.
```
coproc up_neq5() -> (x: UInt) pre 31/2 post x {
    var c1: UInt; var c2: UInt; var c3: UInt; var c4: UInt; var c5: UInt
    var x0: UInt; var x1: UInt; var x2: UInt; var x3: UInt; var x4: UInt; var x5: UInt
    cohavoc x0; coassume ?(x0 != 0)
    if \oplus { weigh 1/2; cohavoc c1; coassume ?(c1 != 1) } else { weigh 1/2; cohavoc c1; coassume ?(c1 != 0) }
    cohavoc x1; coassume ?(x1 != 2 * x0 + c1)
    if \oplus { weigh 1/2; cohavoc c2; coassume ?(c2 != 1) } else { weigh 1/2; cohavoc c2; coassume ?(c2 != 0) }
    cohavoc x2; coassume ?(x2 != 2 * x1 + c2)
    if \oplus { weigh 1/2; cohavoc c3; coassume ?(c3 != 1) } else { weigh 1/2; cohavoc c3; coassume ?(c3 != 0) }
    cohavoc x3; coassume ?(x3 != 2 * x2 + c3)
    if \oplus { weigh 1/2; cohavoc c4; coassume ?(c4 != 1) } else { weigh 1/2; cohavoc c4; coassume ?(c4 != 0) }
    cohavoc x4; coassume ?(x4 != 2 * x3 + c4)
    if \oplus { weigh 1/2; cohavoc c5; coassume ?(c5 != 1) } else { weigh 1/2; cohavoc c5; coassume ?(c5 != 0) }
    cohavoc x5; coassume ?(x5 != 2 * x4 + c5)
    x = x5
}
```

### blowup
[Where passify pays off](#where-passify-pays-off---no-passify-runs-out-of-memory-2026-11-23). `gen.py <family> <n>`
prints a program; `run.py <caesar> <family> <n1,n2,…> [timeout]` writes `<family>_<n>.heyvl` into the current
directory and runs it with and without `--no-passify`. Families: `double`, `fib`, `flipdouble`, `codouble`,
`coflipdouble`. Expected: Verified in both modes (up to the timeouts in the table). `double` with n = 3:
```
proc double3(x0: UInt) -> (x: UInt)
    pre ?(true)
    post ?(x >= 7)
{
    x = x0
    x = x + x + 1
    x = x + x + 1
    x = x + x + 1
}
```
