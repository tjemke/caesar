#!/usr/bin/env python3
"""Generate loop-free HeyVL procs with a tunable ratio of branching to sequential code.

Usage: python3 branchgen.py [options] > out.heyvl     (see --help)

Shape of a generated program
----------------------------
The body is a sequence of `--units` units. Each unit is either

  * sequential: `--path-len` assignments in a row, or
  * a branch:   a binary branch tree of depth `--depth`; every root-to-leaf path
                through it also contains exactly `--path-len` assignments
                (split evenly over the levels).

So every execution path through the whole program has the same number of
assignments (units * path-len), no matter how much branching there is. Only the
*shape* changes: `--branch-ratio 0` is straight-line code, `--branch-ratio 1` is
nothing but branches. Branch units are spread evenly between the sequential ones.

Assignments have the form  v_t = v_t + v_a + v_b + ... + 1  (`--dup` operands
besides v_t). With dup >= 1 the plain wp substitution grows the terms
(exponentially for dup >= 1 when operands keep feeding each other), which is
what passification avoids. `--touch` bounds how many distinct variables a branch
arm writes; every such variable needs a join (havoc + assume) after the branch
in the passified program.

`--const-ratio` / `--copy-ratio` turn that fraction of assignments into
`v = c` / `v = u` instead (rabin2-style `phase = 1`, `n = i`). Those shrink or
keep the substituted term, while passify still pays a fresh variable for each
of them plus the joins: the regime where --no-passify can come out ahead.

All updates are monotone, so the default post `[sum(v) >= sum(init) + B]` with B
the componentwise-min lower bound over all branch arms (evaluated from the zero
input) holds on every path for every input; procs use `pre 1`, coprocs `pre 0`
with the negated post. `--falsify` takes B = (value of one concrete path from
the zero input) + 1, which is violated on that path -> counterexample.

Static statistics (paths, writes per variable before/after passification,
joins, ...) are printed as a leading `// stat key=value` comment block; run with
`--stats-only` to just get those as `key=value` lines.
"""
import argparse
import random
import sys


# ---------------------------------------------------------------- program model
# A block is a list of statements:
#   ("assign", target, [operands], self, const)   target = [target +] operands + const
#   ("branch", kind, cond, block_a, block_b)   kind in if|flip|nondet
#   ("flip", var, p)                            c = flip(p)


def evenly_spaced(n_total, n_pick):
    """Indices of n_pick positions spread evenly over range(n_total)."""
    if n_pick <= 0:
        return set()
    return {int((i + 0.5) * n_total / n_pick) for i in range(n_pick)}


class Gen:
    def __init__(self, a):
        self.a = a
        self.rng = random.Random(a.gen_seed)
        self.vars = [f"v{i}" for i in range(a.vars)]
        self.rr = 0  # round-robin target counter for deterministic variety
        self.n_coins = 0

    def assignment(self, allowed):
        a = self.a
        t = allowed[self.rr % len(allowed)]
        self.rr += 1
        others = [v for v in self.vars if v != t] or [t]
        if a.random_operands:
            ops = [self.rng.choice(others) for _ in range(a.dup)]
        else:
            k = self.vars.index(t)
            ops = [others[(k + j) % len(others)] for j in range(a.dup)]
        r = self.rng.random()
        if r < a.const_ratio:  # v = c: shrinks the substituted term
            return ("assign", t, [], False, self.rng.randint(0, 3))
        if r < a.const_ratio + a.copy_ratio:  # v = u: plain copy
            return ("assign", t, [others[self.rng.randrange(len(others))]], False, 0)
        return ("assign", t, ops, True, 1)

    def seq(self, n, allowed=None):
        return [self.assignment(allowed or self.vars) for _ in range(n)]

    def arm_vars(self):
        """Variables a branch arm may write (`--touch` of them)."""
        t = min(self.a.touch, len(self.vars)) if self.a.touch else len(self.vars)
        return self.rng.sample(self.vars, t)

    def condition(self):
        """Pick the branch kind and produce (prefix_stmts, kind, cond)."""
        a = self.a
        r = self.rng.random()
        if r < a.flip_ratio:
            c = f"c{self.n_coins}"
            self.n_coins += 1
            return [("flip", c, a.flip_p)], "flip", c
        if r < a.flip_ratio + a.nondet_ratio:
            return [], "nondet", None
        x, y = self.rng.sample(self.vars, 2) if len(self.vars) > 1 else (self.vars[0],) * 2
        cond = ("even", x) if x == y else ("lt", x, y)
        return [], "if", cond

    def branch(self, depth, path_len):
        # split path_len over depth levels; front levels get the remainder
        here = path_len // depth + (1 if path_len % depth else 0)
        rest = path_len - here
        pre, kind, cond = self.condition()

        def arm():
            body = self.seq(here, self.arm_vars())
            if depth > 1:
                body += self.branch(depth - 1, rest)
            return body

        return pre + [("branch", kind, cond, arm(), arm())]

    def program(self):
        a = self.a
        n_branch = round(a.branch_ratio * a.units)
        where = evenly_spaced(a.units, n_branch)
        body = []
        for u in range(a.units):
            if u in where:
                body += self.branch(a.depth, a.path_len)
            else:
                body += self.seq(a.path_len)
        return body


# ---------------------------------------------------------------- statistics


def written(block):
    """Set of variables written somewhere in block (flip coins excluded)."""
    s = set()
    for st in block:
        if st[0] == "assign":
            s.add(st[1])
        elif st[0] == "branch":
            s |= written(st[3]) | written(st[4])
    return s


def stats(block, vars_):
    """Static numbers about the program and its passified form.

    writes_*      -- assignments per variable in the program text
    ssa_*         -- fresh versions per variable after passification
                     (one per assignment + one per branch join on that var)
    joins         -- total join assignments inserted (2 per joined var: one per arm)
    """
    st = dict(assigns=0, branches=0, ifs=0, flips=0, nondets=0, max_depth=0, joins=0)
    writes = {v: 0 for v in vars_}
    ssa = {v: 0 for v in vars_}

    def go(b, d):
        paths = 1
        for s in b:
            if s[0] == "assign":
                st["assigns"] += 1
                writes[s[1]] += 1
                ssa[s[1]] += 1
            elif s[0] == "branch":
                st["branches"] += 1
                st[{"if": "ifs", "flip": "flips", "nondet": "nondets"}[s[1]]] += 1
                st["max_depth"] = max(st["max_depth"], d + 1)
                pa = go(s[3], d + 1)
                pb = go(s[4], d + 1)
                paths *= pa + pb
                # passify joins every var whose version differs between the arms;
                # every assignment makes a fresh version, so that is all vars
                # written in either arm.
                for v in written(s[3]) | written(s[4]):
                    ssa[v] += 1
                    st["joins"] += 2
        return paths

    for v in vars_:  # the initial `v = init_v` also gets a version
        ssa[v] += 1
    st["paths"] = go(block, 0)
    st["vars"] = len(vars_)
    st["max_writes_per_var"] = max(writes.values())
    st["max_ssa_per_var"] = max(ssa.values())
    st["total_ssa"] = sum(ssa.values())
    st["writes"] = ",".join(f"{v}:{writes[v]}" for v in vars_)
    st["ssa"] = ",".join(f"{v}:{ssa[v]}" for v in vars_)
    return st


# ---------------------------------------------------------------- bounds
# All updates are monotone (v_t += sum(operands) + 1 over UInt), so
#  * the componentwise minimum of the arm states at each join is a lower bound
#    for every path, for every input (inputs >= 0, increase minimal at 0), and
#  * running one concrete path from the all-zero input gives a value the
#    program actually reaches, so bound+1 is violated there.


def assign_value(s, st):
    _, t, ops, self_, c = s
    return (st[t] if self_ else 0) + sum(st[o] for o in ops) + c


def cond_holds(cond, st):
    return st[cond[1]] % 2 == 0 if cond[0] == "even" else st[cond[1]] < st[cond[2]]


def lower_bound(block, st):
    for s in block:
        if s[0] == "assign":
            st[s[1]] = assign_value(s, st)
        elif s[0] == "branch":
            sa = lower_bound(s[3], dict(st))
            sb = lower_bound(s[4], dict(st))
            st = {v: min(sa[v], sb[v]) for v in st}
    return st


def concrete_path(block, st):
    """Follow `if`s by their condition, flips/nondets always take the first arm."""
    for s in block:
        if s[0] == "assign":
            st[s[1]] = assign_value(s, st)
        elif s[0] == "branch":
            take_a = cond_holds(s[2], st) if s[1] == "if" else True
            st = concrete_path(s[3] if take_a else s[4], st)
    return st


# ---------------------------------------------------------------- printing


def render(block, ind, a):
    out = []
    p = "    " * ind
    for s in block:
        if s[0] == "assign":
            _, t, ops, self_, c = s
            terms = ([t] if self_ else []) + ops
            rhs = " + ".join(terms + ([str(c)] if c or not terms else []))
            out.append(f"{p}{s[1]} = {rhs}")
        elif s[0] == "flip":
            out.append(f"{p}{s[1]} = flip({s[2]})")
        else:
            _, kind, cond, ba, bb = s
            if kind == "nondet":
                head = "if ⊔" if a.coproc else "if ⊓"
            elif kind == "flip":
                head = f"if {cond}"
            elif cond[0] == "even":
                head = f"if {cond[1]} % 2 == 0"
            else:
                head = f"if {cond[1]} < {cond[2]}"
            out.append(f"{p}{head} {{")
            out += render(ba, ind + 1, a)
            out.append(f"{p}}} else {{")
            out += render(bb, ind + 1, a)
            out.append(f"{p}}}")
    return out


def emit(a):
    g = Gen(a)
    body = g.program()
    st = stats(body, g.vars)
    zero = {v: 0 for v in g.vars}
    if a.falsify:
        bound = sum(concrete_path(body, dict(zero)).values()) + 1
    else:
        bound = sum(lower_bound(body, dict(zero)).values())
    st["bound"] = bound
    inits = [f"init_{v}" for v in g.vars]
    if a.const_ratio or a.copy_ratio:
        # v = c / v = u forget the input, so only the absolute bound is valid
        # (all updates are monotone, so the zero input gives the minimum)
        cond = f"{' + '.join(g.vars)} >= {bound}"
    else:
        cond = f"{' + '.join(g.vars)} >= {' + '.join(inits)} + {bound}"
    name = a.name or (
        f"br_r{int(round(a.branch_ratio * 100))}_d{a.depth}_u{a.units}_k{a.path_len}_g{a.gen_seed}"
    )
    kw = "coproc" if a.coproc else "proc"
    # Quantitative so that a single failing flip branch is visible:
    #   proc:   1 <= wp[C]([cond])    iff cond holds on every (demonic) path
    #   coproc: 0 >= wp[C]([!cond])   iff cond holds on every (angelic) path
    if a.coproc:
        pre, post = "0", f"[!({cond})]"
    else:
        pre, post = "1", f"[{cond}]"
    params = ", ".join(f"{i}: UInt" for i in inits)
    rets = ", ".join(f"{v}: UInt" for v in g.vars)
    lines = [f"// generated by: branchgen.py {' '.join(sys.argv[1:])}"]
    lines += [f"// stat {k}={v}" for k, v in st.items()]
    lines += [f"{kw} {name}({params}) -> ({rets})", f"    pre {pre}", f"    post {post}", "{"]
    lines += [f"    var c{i}: Bool" for i in range(g.n_coins)]
    lines += [f"    {v} = init_{v}" for v in g.vars]
    lines += render(body, 1, a)
    lines.append("}")
    return "\n".join(lines) + "\n", st


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--units", "-u", type=int, default=10, help="number of units (default 10)")
    p.add_argument("--path-len", "-k", type=int, default=4, help="assignments per unit along any path (default 4)")
    p.add_argument("--branch-ratio", "-r", type=float, default=0.5, help="fraction of units that are branches, 0..1 (default 0.5)")
    p.add_argument("--depth", "-d", type=int, default=1, help="nesting depth of each branch unit (default 1)")
    p.add_argument("--vars", "-v", type=int, default=3, help="number of program variables (default 3)")
    p.add_argument("--dup", type=int, default=1, help="operands besides the target on each rhs (default 1); drives term growth under substitution")
    p.add_argument("--touch", type=int, default=0, help="max distinct vars written per branch arm (0 = all, default)")
    p.add_argument("--flip-ratio", type=float, default=0.0, help="fraction of branches that are coin flips (c = flip(p); if c)")
    p.add_argument("--flip-p", default="1/2", help="coin probability (default 1/2)")
    p.add_argument("--nondet-ratio", type=float, default=0.0, help="fraction of branches that are nondeterministic (⊓ for proc, ⊔ for coproc)")
    p.add_argument("--const-ratio", type=float, default=0.0,
                   help="fraction of assignments of the form v = c (rabin2's `phase = 1`)")
    p.add_argument("--copy-ratio", type=float, default=0.0,
                   help="fraction of assignments of the form v = u (rabin2's `n = i`)")
    p.add_argument("--random-operands", action="store_true", help="pick rhs operands randomly instead of round-robin")
    p.add_argument("--coproc", action="store_true", help="emit a coproc (upper bound) instead of a proc")
    p.add_argument("--falsify", action="store_true", help="make the post one too strong -> counterexample expected")
    p.add_argument("--gen-seed", type=int, default=0,
                   help="RNG seed of this generator (branch conditions, operands, arm vars). "
                        "Unrelated to Z3's seed, which is caesar's --z3-seed (see branchrun.py --z3-seeds)")
    p.add_argument("--name", help="proc name (default derived from parameters)")
    p.add_argument("--stats-only", action="store_true", help="print key=value statistics instead of the program")
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    if a.depth < 1 or a.path_len < a.depth:
        sys.exit("need depth >= 1 and path-len >= depth (every branch level needs an assignment)")
    if not 0 <= a.branch_ratio <= 1 or a.flip_ratio + a.nondet_ratio > 1:
        sys.exit("ratios must lie in [0, 1] and flip-ratio + nondet-ratio <= 1")
    if a.const_ratio < 0 or a.copy_ratio < 0 or a.const_ratio + a.copy_ratio > 1:
        sys.exit("const-ratio + copy-ratio must lie in [0, 1]")
    prog, st = emit(a)
    if a.stats_only:
        print("\n".join(f"{k}={v}" for k, v in st.items()))
    else:
        sys.stdout.write(prog)


if __name__ == "__main__":
    main()
