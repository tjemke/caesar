#!/usr/bin/env python3
"""Naive search over branchgen.py's parameters for programs where one caesar
mode beats the other.

Usage:
  python3 branchsearch.py <path/to/caesar> [--samples 40] [--climb 40]
        [--objective no-passify|passify] [--timeout 20] [--search-seed 0]
        [--start "<branchgen args>"] [--space key=v1,v2,...]...
        [--confirm K [--z3-seeds 1,2,3] [--hyperfine 5]] [--out DIR]

Search:
  1. evaluate the --start config (if given) and --samples random configs drawn
     from the parameter space,
  2. hill-climb --climb steps from the best config found so far: change one or
     two parameters to a neighbouring value of their list (sometimes a random
     one) and keep the change if the score improved,
  3. optionally (--confirm K) re-measure the K best configs with hyperfine across
     --z3-seeds via branchrun.py's machinery, so a single lucky/unlucky Z3 run
     does not decide the ranking.

Score = log2(t_passify / t_no_passify) for --objective no-passify (default:
look for programs where --no-passify wins), the negation for --objective
passify. Timed-out runs count as --timeout seconds (so the score is a lower
bound), times are floored at 0.05s against startup noise. Configs where both
modes time out score 0 and are marked; configs with more than --max-assigns
assignments in the program text are skipped without running caesar.

Three different seeds are in play, keep them apart:
  --search-seed   this script's RNG (which configs get sampled/mutated)
  gen_seed        a search *parameter*: branchgen.py's --gen-seed (the program)
  --z3-seeds      caesar --z3-seed values, only used by --confirm

Parameter space (override any entry with --space key=v1,v2,..., e.g.
--space depth=2,3 --space const_ratio=0.5,0.75):
"""
import argparse
import csv
import math
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import branchgen  # noqa: E402
import branchrun  # noqa: E402

# key -> (branchgen flag, candidate values). Flags without a value (coproc) use bools.
SPACE = {
    "units": ("--units", [5, 10, 20, 30, 40]),
    "path_len": ("--path-len", [2, 4, 6, 8]),
    "branch_ratio": ("--branch-ratio", [0, 0.25, 0.5, 0.75, 1]),
    "depth": ("--depth", [1, 2, 3, 4]),
    "vars": ("--vars", [2, 3, 6, 10, 16]),
    "dup": ("--dup", [0, 1, 2]),
    "touch": ("--touch", [0, 1, 2, 4]),
    "const_ratio": ("--const-ratio", [0, 0.25, 0.5, 0.75]),
    "copy_ratio": ("--copy-ratio", [0, 0.2, 0.4]),
    "flip_ratio": ("--flip-ratio", [0, 0.5, 1]),
    "nondet_ratio": ("--nondet-ratio", [0, 0.5]),
    "coproc": ("--coproc", [False, True]),
    "gen_seed": ("--gen-seed", [0, 1, 2, 3, 4]),
}
__doc__ += "\n".join(f"  {k:13} {v[1]}" for k, v in SPACE.items()) + "\n"

FLAG_TO_KEY = {flag: k for k, (flag, _) in SPACE.items()}
FLOOR = 0.05


def parse_value(s):
    if s in ("true", "false", "True", "False"):
        return s.lower() == "true"
    return float(s) if "." in s else int(s)


def repair(cfg):
    """Make a config valid for branchgen (clip instead of rejecting)."""
    cfg = dict(cfg)
    cfg["depth"] = min(cfg["depth"], cfg["path_len"])
    if cfg["const_ratio"] + cfg["copy_ratio"] > 1:
        cfg["copy_ratio"] = round(1 - cfg["const_ratio"], 3)
    if cfg["flip_ratio"] + cfg["nondet_ratio"] > 1:
        cfg["nondet_ratio"] = round(1 - cfg["flip_ratio"], 3)
    return cfg


def to_args(cfg, skip=()):
    args = []
    for k, (flag, _) in SPACE.items():
        if k in skip:
            continue
        v = cfg[k]
        if isinstance(v, bool):
            args += [flag] if v else []
        else:
            args += [flag, str(v)]
    return args


def key(cfg):
    return tuple(cfg[k] for k in SPACE)


def describe(cfg):
    return " ".join(to_args(cfg))


def from_args(argstr):
    """Config from a branchgen argument string (unset params get branchgen's defaults)."""
    a = branchgen.parser().parse_args(argstr.split())
    cfg = {}
    for k, (flag, _) in SPACE.items():
        cfg[k] = getattr(a, flag.lstrip("-").replace("-", "_"))
    return repair(cfg)


class Search:
    def __init__(self, a, space):
        self.a = a
        self.space = space
        self.rng = random.Random(a.search_seed)
        self.cache = {}
        self.n = 0
        os.makedirs(a.out, exist_ok=True)
        self.csv_path = os.path.join(a.out, "search.csv")
        self.fh = open(self.csv_path, "a", newline="")
        self.w = None

    def random_cfg(self):
        return repair({k: self.rng.choice(vals) for k, (_, vals) in self.space.items()})

    def mutate(self, cfg):
        cfg = dict(cfg)
        for k in self.rng.sample([k for k in self.space if len(self.space[k][1]) > 1],
                                 self.rng.choice([1, 1, 2])):
            vals = self.space[k][1]
            if self.rng.random() < 0.2 or cfg[k] not in vals:
                cfg[k] = self.rng.choice(vals)
            else:
                i = vals.index(cfg[k]) + self.rng.choice([-1, 1])
                cfg[k] = vals[max(0, min(len(vals) - 1, i))]
        return repair(cfg)

    def score(self, tp, tn):
        s = math.log2(max(tp, FLOOR) / max(tn, FLOOR))
        return s if self.a.objective == "no-passify" else -s

    def evaluate(self, cfg, phase):
        k = key(cfg)
        if k in self.cache:
            return self.cache[k]
        self.n += 1
        a = self.a
        try:
            prog = branchrun.gen(to_args(cfg))
        except subprocess.CalledProcessError as e:
            self.cache[k] = None
            print(f"[{self.n}] invalid: {describe(cfg)}: {e.stderr.strip()}", flush=True)
            return None
        st = branchrun.static_stats(prog)
        if int(st["assigns"]) > a.max_assigns:
            self.cache[k] = None
            print(f"[{self.n}] skip ({st['assigns']} assigns > --max-assigns): {describe(cfg)}", flush=True)
            return None
        name = f"s{self.n:04d}_" + re.search(r"proc (\w+)\(", prog).group(1)
        path = os.path.join(a.out, f"{name}.heyvl")
        with open(path, "w") as f:
            f.write(prog)
        res = {}
        for mode in ["passify", "--no-passify"]:
            dt, verdict, (mmax, _, _) = branchrun.run(a.caesar, path, mode, None, a.timeout)
            timed_out = bool(re.search(r"[Tt]ime|killed", verdict))
            res[mode] = (a.timeout if timed_out else dt, verdict, timed_out, mmax)
        (tp, vp, top, ssa_meas), (tn, vn, ton, _) = res["passify"], res["--no-passify"]
        both_out = top and ton
        sc = 0.0 if both_out else self.score(tp, tn)
        r = dict(cfg=cfg, path=path, score=sc, t_passify=tp, t_no_passify=tn,
                 v_passify=vp, v_no_passify=vn, both_timed_out=both_out)
        self.cache[k] = r
        row = dict(n=self.n, phase=phase, score=f"{sc:.3f}", t_passify=f"{tp:.3f}",
                   t_no_passify=f"{tn:.3f}", passify_timed_out=top, no_passify_timed_out=ton,
                   v_passify=vp, v_no_passify=vn, ssa_meas_max=ssa_meas, file=path,
                   genargs=describe(cfg), **{k2: cfg[k2] for k2 in SPACE},
                   **{s: st[s] for s in ["assigns", "branches", "paths", "max_writes_per_var",
                                         "max_ssa_per_var", "total_ssa", "joins"]})
        if self.w is None:
            self.w = csv.DictWriter(self.fh, fieldnames=list(row))
            if self.fh.tell() == 0:
                self.w.writeheader()
        self.w.writerow(row)
        self.fh.flush()
        mark = " (both timed out)" if both_out else ""
        print(f"[{self.n}] {phase:6} score={sc:+6.2f}  passify {tp:6.2f}s{'*' if top else ' '} "
              f"no-passify {tn:6.2f}s{'*' if ton else ' '}{mark}  {describe(cfg)}", flush=True)
        return r

    def best(self, k=1):
        rs = [r for r in self.cache.values() if r and not r["both_timed_out"]]
        return sorted(rs, key=lambda r: -r["score"])[:k]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caesar")
    ap.add_argument("--samples", type=int, default=40, help="random configs to evaluate (default 40)")
    ap.add_argument("--climb", type=int, default=40, help="hill-climbing steps afterwards (default 40)")
    ap.add_argument("--objective", choices=["no-passify", "passify"], default="no-passify",
                    help="which mode should win (default: no-passify)")
    ap.add_argument("--timeout", type=int, default=20, help="caesar --timeout per run (default 20)")
    ap.add_argument("--max-assigns", type=int, default=3000,
                    help="skip programs with more assignments than this (default 3000)")
    ap.add_argument("--search-seed", type=int, default=0, help="RNG seed of the search itself")
    ap.add_argument("--start", help='branchgen args to start from, e.g. "-u 20 -k 4 -r 1 -d 3 --const-ratio 0.5"')
    ap.add_argument("--space", action="append", default=[], metavar="KEY=V1,V2,...",
                    help="override the candidate values of one parameter")
    ap.add_argument("--confirm", type=int, default=0, metavar="K",
                    help="re-measure the K best configs with hyperfine across --z3-seeds")
    ap.add_argument("--z3-seeds", type=branchrun.ints, default=[1, 2, 3])
    ap.add_argument("--hyperfine", type=int, default=5, help="hyperfine runs per (mode, z3 seed) for --confirm")
    ap.add_argument("--top", type=int, default=10, help="how many configs to list at the end")
    ap.add_argument("--out", default="branch-search")
    a = ap.parse_args()

    space = dict(SPACE)
    for o in a.space:
        k, _, vals = o.partition("=")
        if k not in SPACE:
            sys.exit(f"unknown parameter {k}; known: {', '.join(SPACE)}")
        space[k] = (SPACE[k][0], [parse_value(v) for v in vals.split(",")])
    if a.confirm and not __import__("shutil").which("hyperfine"):
        sys.exit("--confirm needs hyperfine on PATH; try: nix shell nixpkgs#hyperfine -c python3 branchsearch.py ...")

    s = Search(a, space)
    try:
        if a.start:
            s.evaluate(from_args(a.start), "start")
        for _ in range(a.samples):
            s.evaluate(s.random_cfg(), "random")
        cur = s.best()
        for _ in range(a.climb):
            if not cur:
                cur = s.best() or [s.evaluate(s.random_cfg(), "random")]
                cur = [c for c in cur if c]
                continue
            r = s.evaluate(s.mutate(cur[0]["cfg"]), "climb")
            if r and not r["both_timed_out"] and r["score"] > cur[0]["score"]:
                cur = [r]
    except KeyboardInterrupt:
        print("\ninterrupted, results so far:", file=sys.stderr)

    top = s.best(a.top)
    winner = "--no-passify" if a.objective == "no-passify" else "passify"
    print(f"\ntop {len(top)} configs where {winner} wins most (score = log2 speedup; * = lower bound, a run timed out):")
    for r in top:
        star = "*" if re.search(r"[Tt]ime|killed", r["v_passify"] + r["v_no_passify"]) else " "
        print(f"  {r['score']:+6.2f}{star} passify {r['t_passify']:6.2f}s  no-passify {r['t_no_passify']:6.2f}s"
              f"  {describe(r['cfg'])}")
    print(f"all evaluations: {s.csv_path}", flush=True)

    if a.confirm and top:
        print(f"\nconfirming top {min(a.confirm, len(top))} with hyperfine ({a.hyperfine} runs x z3 seeds {a.z3_seeds}):", flush=True)
        conf_csv = os.path.join(a.out, "confirm.csv")
        for r in top[:a.confirm]:
            subprocess.run(
                [sys.executable, os.path.join(HERE, "branchrun.py"), a.caesar,
                 "--timeout", str(a.timeout), "--hyperfine", str(a.hyperfine),
                 "--z3-seeds", ",".join(map(str, a.z3_seeds)), "--out", a.out, "--csv", conf_csv,
                 "--ratios", str(r["cfg"]["branch_ratio"]), "--depths", str(r["cfg"]["depth"]),
                 "--gen-seeds", str(r["cfg"]["gen_seed"]), "--"]
                # branchrun sets these three itself from --ratios/--depths/--gen-seeds
                + to_args(r["cfg"], skip=("branch_ratio", "depth", "gen_seed")),
                check=False,
            )
        print(f"confirmation results: {conf_csv}")


if __name__ == "__main__":
    main()
