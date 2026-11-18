# Integration tests where passification is applied

Branch `feat/passification` at `bbe78e1`, generated 2026-11-18. Still accurate for `d84be1d`, which changes no passify decision. Companion files: `passify-fixes-by-commit.md` and `passify-bugs-and-skips.md`.

Each file in `tests/` was run with its own `RUN` command, using a build of caesar that logs, for every verification task, whether passify succeeded and how many fresh variables it created. A task is one procedure, or one of the procedures a proof rule generates. Passify either rewrites a whole task or leaves it unpassified.

- 90 files, 155 verification tasks
- **61 tasks passified**, of which **26 actually rewrite something**; the other 35 contain no assignments, so passify changes nothing
- 21 files where passify rewrites at least one task, 16 files where it succeeds but changes nothing, 52 files where every task is skipped, 1 ignored by the suite
- The 4 `XFAIL: timeout` tests time out as expected; their single task was recorded before the timeout.

## Passify rewrites at least one task (21)

Number in parentheses = fresh variables passify created in that task (one per assignment, plus one per variable joined after a branch).

| Test | Passified tasks | Skipped tasks |
|---|---|---|
| `tests/boolean/computing_with_smt_2014/factorial.heyvl` | lemmaFactorialStep (5), theoremFactorialEquiv (2) | – |
| `tests/boolean/computing_with_smt_2014/triangular.heyvl` | TriangleProperty (2) | – |
| `tests/boolean/procs/proc-smoke.heyvl` | xyz (2) | – |
| `tests/boolean/reasoning_about_comprehensions_2009/sum.heyvl` | Sum1 (6) | Sum2: `len(…)` assignment |
| `tests/boolean/reasoning_about_comprehensions_2009/sum_assume.heyvl` | Sum1 (6) | Sum2: `len(…)` assignment |
| `tests/boolean/well_known/fibonacci.heyvl` | iter_fib (11) | – |
| `tests/boolean/well_known/mccarthy-fail.heyvl` | M (5) | – |
| `tests/boolean/well_known/mccarthy.heyvl` | M (5) | – |
| `tests/boolean/well_known/triangular-theorem.heyvl` | gauss (2) | main: `sum(…)` assignment (user `func`) |
| `tests/case-studies/coupon-collector.heyvl` | outer_loop (8), inner_loop (6), harmonic_lemma (0) | sum_lemma: assignment to a list variable |
| `tests/domains/refute-issue90.heyvl` | producer_consumer (9) | – |
| `tests/loop-rules/induction/coupon_collector_core.heyvl` | harmonic_sum (6) | – |
| `tests/loop-rules/omega_invariants/countdown.heyvl` | omega (6) | – |
| `tests/loop-rules/omega_invariants/countdown_core.heyvl` | monotonically_increasing (0), condition_1 (4), condition_2 (4) | – |
| `tests/loop-rules/omega_invariants/fail-ert-base-state.heyvl` | omega_ert_base_state (6) | – |
| `tests/loop-rules/omega_invariants/fail-wlp-base-state.heyvl` | omega_wlp_base_state (6) | – |
| `tests/loop-rules/omega_invariants/fail-wlp-induction-state.heyvl` | omega_wlp_induction_state (6) | – |
| `tests/loop-rules/omega_invariants/fail-wp-base-state.heyvl` | omega_wp_base_state (6) | – |
| `tests/loop-rules/omega_invariants/fail-wp-induction-state.heyvl` | omega_wp_induction_state (6) | – |
| `tests/loop-rules/omega_invariants/valid-families.heyvl` | omega_wp_countdown (6), omega_wlp_countdown (6), omega_wlp_nonterminating (0), omega_ert_countdown (6) | – |
| `tests/loop-rules/ost2019/ost_cdb_guard_states.heyvl` | biasdir (2), biasdir_lt_infinity_0 (0), biasdir_invariant_finite_0 (0) | biasdir_past_0: `flip(…)` assignment, biasdir_conditional_difference_bounded_0: `flip(…)` assignment, biasdir_lower_bound_0: `flip(…)` assignment |

## Passify succeeds but has nothing to rename (16)

| Test | Passified tasks | Skipped tasks |
|---|---|---|
| `tests/boolean/computing_with_smt_2014/peano.heyvl` | test_toNat, test_plus, test_mult, test_factorial0, test_factorial1 | – |
| `tests/boolean/procs/list_eq_extensionality.heyvl` | empty_lists_are_equal_extensionally | – |
| `tests/boolean/procs/procs-no-spec.heyvl` | bla | – |
| `tests/boolean/procs/quantifier_free_var_scope.heyvl` | quantifier_free_var_scope | – |
| `tests/boolean/smoke/always_false.heyvl` | main | – |
| `tests/domains/array_decl.heyvl` | get_put_check | – |
| `tests/loop-rules/ast2018/ast-flipflop.heyvl` | main, main_prob_conditions_0, main_decrease_conditions_0 | main_I_wp_subinvariant_0: `flip(…)` assignment, main_V_awp_superinvariant_0: `flip(…)` assignment, main_progress_condition_0: `flip(…)` assignment |
| `tests/loop-rules/ast2018/ast-flipflop_core.heyvl` | termination_condition | I_wp_subinvariant: `flip(…)` assignment, V_wp_superinvariant: `flip(…)` assignment, progress_condition: `flip(…)` assignment |
| `tests/loop-rules/ast2018/ast-state-machine.heyvl` | termination_condition | I_wp_subinvariant: `flip(…)` assignment, V_wp_superinvariant: `flip(…)` assignment, progress_condition: `flip(…)` assignment |
| `tests/loop-rules/ast2018/demonically_fair.heyvl` | demonically_fair, demonically_fair_prob_conditions_0, demonically_fair_decrease_conditions_0 | demonically_fair_I_wp_subinvariant_0: `flip(…)` assignment, demonically_fair_V_awp_superinvariant_0: `flip(…)` assignment, demonically_fair_progress_condition_0: `flip(…)` assignment |
| `tests/loop-rules/ast2018/escaping_spline.heyvl` | escaping_spline, escaping_spline_prob_conditions_0, escaping_spline_decrease_conditions_0 | escaping_spline_I_wp_subinvariant_0: `flip(…)` assignment, escaping_spline_V_awp_superinvariant_0: `flip(…)` assignment, escaping_spline_progress_condition_0: `flip(…)` assignment |
| `tests/loop-rules/ast2018/fair_in_the_limit.heyvl` | termination_condition | I_wp_subinvariant: `flip(…)` assignment, V_wp_superinvariant: `flip(…)` assignment, progress_condition: `flip(…)` assignment |
| `tests/loop-rules/ast2018/negative_binomial.heyvl` | escaping_spline, escaping_spline_prob_conditions_0, escaping_spline_decrease_conditions_0 | escaping_spline_I_wp_subinvariant_0: `flip(…)` assignment, escaping_spline_V_awp_superinvariant_0: `flip(…)` assignment, escaping_spline_progress_condition_0: `flip(…)` assignment |
| `tests/loop-rules/omega_invariants/fail-wlp-zero-seed.heyvl` | omega_wlp_zero_seed | – |
| `tests/loop-rules/ost2019/aiming-low-example39.heyvl` | lt_infinity, harmonize_I_f | past: `flip(…)` assignment, conditional_difference_bounded: `flip(…)` assignment, lower_bound: `flip(…)` assignment, loopiter_lt_infty: `flip(…)` assignment |
| `tests/loop-rules/past2013/past.heyvl` | condition_1, condition_2 | condition_3: `flip(…)` assignment |

## Not passified (52)

**`flip(…)` assignment** (48)

- `tests/case-studies/geo-recursive.heyvl`
- `tests/case-studies/zeroconf.heyvl`
- `tests/core/brp2.heyvl`
- `tests/core/rabin2.heyvl`
- `tests/core/unif_gen3.heyvl`
- `tests/loop-rules/bmc/bmc_brp5-fail.heyvl`
- `tests/loop-rules/bmc/refute-brp5_bmc.heyvl`
- `tests/loop-rules/bmc/refute-geo2_bmc.heyvl`
- `tests/loop-rules/bmc/refute-geo3_bmc.heyvl`
- `tests/loop-rules/bmc/refute-rabin3_bmc.heyvl`
- `tests/loop-rules/bmc/refute-rabin4_bmc.heyvl`
- `tests/loop-rules/bmc/refute-rabin5_bmc.heyvl`
- `tests/loop-rules/bmc/refute-unif_gen1_bmc.heyvl`
- `tests/loop-rules/induction/2drwalk.heyvl`
- `tests/loop-rules/induction/bayesian_network.heyvl`
- `tests/loop-rules/induction/chain/lossy_list.heyvl`
- `tests/loop-rules/induction/condand.heyvl`
- `tests/loop-rules/induction/fcall.heyvl`
- `tests/loop-rules/induction/hyper.heyvl`
- `tests/loop-rules/induction/linear01.heyvl`
- `tests/loop-rules/induction/prdwalk.heyvl`
- `tests/loop-rules/induction/prspeed.heyvl`
- `tests/loop-rules/induction/rabin1.heyvl`
- `tests/loop-rules/induction/rabin1_wlp.heyvl`
- `tests/loop-rules/induction/rdspeed.heyvl`
- `tests/loop-rules/induction/rdwalk.heyvl`
- `tests/loop-rules/induction/sprdwalk.heyvl`
- `tests/loop-rules/k_induction/C4B_t303.heyvl`
- `tests/loop-rules/k_induction/brp1.heyvl`
- `tests/loop-rules/k_induction/brp2.heyvl`
- `tests/loop-rules/k_induction/brp3.heyvl`
- `tests/loop-rules/k_induction/fail-geo3.heyvl`
- `tests/loop-rules/k_induction/geo1.heyvl`
- `tests/loop-rules/k_induction/kind_brp1.heyvl`
- `tests/loop-rules/k_induction/kind_linear01.heyvl`
- `tests/loop-rules/k_induction/rabin2.heyvl`
- `tests/loop-rules/k_induction/rabin2_wlp.heyvl`
- `tests/loop-rules/k_induction/rabin3_wlp.heyvl`
- `tests/loop-rules/k_induction/unif_gen1.heyvl`
- `tests/loop-rules/k_induction/unif_gen1_wlp.heyvl`
- `tests/loop-rules/k_induction/unif_gen2.heyvl`
- `tests/loop-rules/k_induction/unif_gen2_wlp.heyvl`
- `tests/loop-rules/k_induction/unif_gen3.heyvl`
- `tests/loop-rules/k_induction/unif_gen3_wlp.heyvl`
- `tests/loop-rules/k_induction/unif_gen4.heyvl`
- `tests/loop-rules/k_induction/unif_gen4_wlp.heyvl`
- `tests/loopfree-prob/noisy_or.heyvl`
- `tests/loopfree-prob/six-sided-die.heyvl`

**`ber(…)` assignment** (2)

- `tests/loop-rules/induction/chain/chain.heyvl`
- `tests/loop-rules/induction/rabin_hurd2004.heyvl`

**`select(…)` assignment; assignment to a list variable** (1)

- `tests/boolean/procs/list_ops.heyvl`

**`get_value(…)` assignment (user `func`)** (1)

- `tests/boolean/well_known/binary-tree-sum.heyvl`

## Ignored by the suite (1)

- `tests/case-studies/pgf.heyvl`

## Plain list: files where passify rewrites at least one task

```
tests/boolean/computing_with_smt_2014/factorial.heyvl
tests/boolean/computing_with_smt_2014/triangular.heyvl
tests/boolean/procs/proc-smoke.heyvl
tests/boolean/reasoning_about_comprehensions_2009/sum.heyvl
tests/boolean/reasoning_about_comprehensions_2009/sum_assume.heyvl
tests/boolean/well_known/fibonacci.heyvl
tests/boolean/well_known/mccarthy-fail.heyvl
tests/boolean/well_known/mccarthy.heyvl
tests/boolean/well_known/triangular-theorem.heyvl
tests/case-studies/coupon-collector.heyvl
tests/domains/refute-issue90.heyvl
tests/loop-rules/induction/coupon_collector_core.heyvl
tests/loop-rules/omega_invariants/countdown.heyvl
tests/loop-rules/omega_invariants/countdown_core.heyvl
tests/loop-rules/omega_invariants/fail-ert-base-state.heyvl
tests/loop-rules/omega_invariants/fail-wlp-base-state.heyvl
tests/loop-rules/omega_invariants/fail-wlp-induction-state.heyvl
tests/loop-rules/omega_invariants/fail-wp-base-state.heyvl
tests/loop-rules/omega_invariants/fail-wp-induction-state.heyvl
tests/loop-rules/omega_invariants/valid-families.heyvl
tests/loop-rules/ost2019/ost_cdb_guard_states.heyvl
```
