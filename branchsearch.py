#!/usr/bin/env python3
"""Naive search over branchgen.py's parameters for programs where one caesar
mode beats the other.

Usage:
  python3 branchsearch.py <path/to/caesar> [--samples 40] [--climb 40]
        [--objective no-passify|passify|balanced|crossover [--target S] [--ladder 1,2,...]]
        [--end-passify "<branchgen args>"] [--end-no-passify "<branchgen args>"]
        [--timeout 20] [--search-seed 0]
        [--start "<branchgen args>"] [--space key=v1,v2,...]...
        [--confirm K [--z3-seeds 1,2,3] [--hyperfine 5]] [--out DIR]

Search:
  1. evaluate the --start config (if given) and --samples random configs drawn
     from the parameter space,
  2. hill-climb --climb evaluations from the best config found so far: change
     one or two parameters to a neighbouring value of their list (sometimes a
     random one) and keep the change if the score did not get worse; with
     --jobs N, each round tries N such mutations in parallel and moves to the
     best of them,
  3. optionally (--confirm K) re-measure the K best configs with hyperfine across
     --z3-seeds via branchrun.py's machinery, so a single lucky/unlucky Z3 run
     does not decide the ranking.

Score, depending on --objective:
  no-passify  log2(t_passify / t_no_passify): programs where --no-passify wins
              (default)
  passify     the negation: programs where passify wins
  balanced    programs that are slow in *both* modes with about equal times.
              The search then runs over program *shapes* (every parameter but
              units). Each shape is grown along --ladder (units 1, 2, 3, 4, 6, ...)
              until its slower mode takes at least --target seconds, and is
              scored there by  -|log2(t_passify / t_no_passify)|  (0 = equal,
              -1 = 2x apart). A shape that never gets there (too cheap even at
              the last rung, or a run times out / exceeds --max-assigns first)
              ranks below every shape that did, ordered by how close its slower
              mode came to the target, which steers the climb towards bigger
              programs. Pick --timeout well above --target (default target:
              timeout / 3). Without the ladder nearly every random config is
              either trivial (startup noise, nothing to compare) or times out.
  crossover   bisection between the two sides of the spectrum. Endpoint A is
              a shape where passify is much faster, endpoint B one where
              --no-passify is (given with --end-passify / --end-no-passify, or
              taken as the two extremes of --samples random shapes). Every
              parameter is interpolated linearly from A (t=0) to B (t=1); the
              *_ratio parameters stay continuous, integers are rounded,
              gen_seed and coproc are pinned to one endpoint's value. The
              endpoints are grown along the size ladder like balanced shapes;
              the whole path is then measured at ONE size (--cross-units,
              default: the larger of the two endpoint sizes where the slower
              mode reached --target or a run timed out), because which mode
              wins also depends on the size (substituted terms grow
              exponentially with program length). At each point the sign of
              s = log2(t_passify / t_no_passify)  says which mode wins (known
              also if exactly one mode timed out). Every round evaluates --jobs points inside
              the current interval [lo, hi] and keeps the sub-interval where s
              changes sign, for --rounds rounds or until the interval is a
              single discrete step (an integer parameter jumping). Each round
              shrinks the interval by a factor of jobs + 1. Results:
              crossover.csv (all points on the path, ordered by t) and a top
              list of the points closest to s = 0, which --confirm re-measures.
For no-passify/passify, timed-out runs count as --timeout seconds (the score is
then a lower bound) and times are floored at 0.05s against startup noise. A run
counts as timed out whenever caesar gives no verdict (it reports timeouts as
"Timed out ..." or as "error: Interrupted"; running out of --mem, which is how
--no-passify usually fails, counts the same);
configs where both modes time out are excluded. Configs with more than
--max-assigns assignments in the program text are skipped without running
caesar.

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
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # no __pycache__ in the repo from importing the siblings
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
LADDER = [1, 2, 3, 4, 6, 8, 11, 16, 23, 32, 45, 64, 91, 128, 181]
UNREACHED = -100.0  # balanced: shapes that never reach --target rank below all others


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


RATIO_KEYS = [k for k in SPACE if k.endswith("_ratio")]
PINNED = ("gen_seed", "coproc")  # interpolating these would just swap programs


def interp(a, b, t):
    """Config at position t on the straight line from config a (t=0) to b (t=1)."""
    c = {}
    for k in SPACE:
        if k in PINNED or isinstance(a[k], bool):
            c[k] = a[k]
        elif k in RATIO_KEYS:
            c[k] = round(a[k] + t * (b[k] - a[k]), 4)
        else:
            c[k] = int(round(a[k] + t * (b[k] - a[k])))
    return repair(c)


def signed(r):
    """log2(t_passify / t_no_passify) of a run record; timeouts count as --timeout."""
    return math.log2(max(r["t_passify"], FLOOR) / max(r["t_no_passify"], FLOOR))


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
        self.shapes = {}  # balanced: shape key -> result at its final ladder rung
        self.lock = threading.RLock()  # cache, shapes, counter, CSV and stdout
        self.n = 0
        os.makedirs(a.out, exist_ok=True)
        self.csv_path = os.path.join(a.out, "search.csv")
        self.fh = open(self.csv_path, "a", newline="")
        self.w = None

    def random_cfg(self):
        return repair({k: self.rng.choice(vals) for k, (_, vals) in self.space.items()})

    def mutate(self, cfg):
        cfg = dict(cfg)
        # balanced: units is set by the size ladder, not searched
        fixed = ("units",) if self.laddered() else ()
        for k in self.rng.sample([k for k in self.space if len(self.space[k][1]) > 1 and k not in fixed],
                                 self.rng.choice([1, 1, 2])):
            vals = self.space[k][1]
            if self.rng.random() < 0.2 or cfg[k] not in vals:
                cfg[k] = self.rng.choice(vals)
            else:
                i = vals.index(cfg[k]) + self.rng.choice([-1, 1])
                cfg[k] = vals[max(0, min(len(vals) - 1, i))]
        return repair(cfg)

    def score(self, tp, tn):
        if self.laddered():  # per rung; the shape score is set in evaluate()
            return -abs(math.log2(max(tp, FLOOR) / max(tn, FLOOR)))
        s = math.log2(max(tp, FLOOR) / max(tn, FLOOR))
        return s if self.a.objective == "no-passify" else -s

    def laddered(self):
        return self.a.objective in ("balanced", "crossover")

    def evaluate(self, cfg, phase):
        if not self.laddered():
            return self.run_cfg(cfg, phase)
        # Size ladder: the shape (all params but units) decides the ratio of the
        # two modes, the size decides the runtime. Grow units until one mode
        # reaches --target seconds or a run times out / gets too big; the shape
        # is judged at the largest size where both modes finished.
        k = self.eval_key(cfg)
        with self.lock:
            if k in self.shapes:
                return self.shapes[k]
        last, reached, stop = None, False, None
        for u in self.a.ladder:
            r = self.run_cfg(dict(cfg, units=u), f"{phase}/u{u}")
            if r is None or r["excluded"]:
                stop = r
                break
            last = r
            if max(r["t_passify"], r["t_no_passify"]) >= self.a.target:
                reached = True
                break
        # Which mode wins at (about) --target: from the final rung, or from the
        # rung where exactly one mode timed out before the target was reached
        # (then the winner is clear, the ratio a lower bound).
        decisive = None
        if stop and not reached and (branchrun.finished(stop["v_passify"])
                                     != branchrun.finished(stop["v_no_passify"])):
            decisive = stop
        elif last:
            decisive = last
        if last is None:  # not even the smallest rung finished in both modes
            shape = None
            if decisive:  # ...but one mode clearly wins: usable for crossover
                shape = dict(decisive, reached=False, excluded=True, signed=signed(decisive),
                             score=UNREACHED, decisive=decisive)
            with self.lock:
                self.shapes[k] = shape
            return shape
        slow = max(last["t_passify"], last["t_no_passify"])
        shape = dict(last, reached=reached, signed=signed(decisive),
                     score=last["score"] if reached else UNREACHED + math.log2(max(slow, FLOOR)))
        with self.lock:
            self.shapes[k] = shape
        shape["decisive"] = decisive
        self.log(f"    shape {'reached' if reached else 'did NOT reach'} --target {self.a.target:g}s"
                 f" at units={last['cfg']['units']}: score {shape['score']:+.2f}; at units="
                 f"{decisive['cfg']['units']} passify {decisive['t_passify']:.2f}s no-passify "
                 f"{decisive['t_no_passify']:.2f}s, log2 ratio {shape['signed']:+.2f}"
                 f"  {describe(dict(cfg, units=0))}")
        return shape

    def eval_key(self, cfg):
        """What evaluate() deduplicates on: the shape for balanced, else the config."""
        return key(dict(cfg, units=None)) if self.laddered() else key(cfg)

    def log(self, msg):
        with self.lock:
            print(msg, flush=True)

    def run_cfg(self, cfg, phase):
        k = key(cfg)
        with self.lock:
            if k in self.cache:
                return self.cache[k]
            self.n += 1
            n = self.n  # local: other workers move self.n on
        a = self.a
        try:
            prog = branchrun.gen(to_args(cfg))
        except subprocess.CalledProcessError as e:
            with self.lock:
                self.cache[k] = None
            self.log(f"[{n}] invalid: {describe(cfg)}: {e.stderr.strip()}")
            return None
        st = branchrun.static_stats(prog)
        if int(st["assigns"]) > a.max_assigns:
            with self.lock:
                self.cache[k] = None
            self.log(f"[{n}] skip ({st['assigns']} assigns > --max-assigns): {describe(cfg)}")
            return None
        name = f"s{n:04d}_" + re.search(r"proc (\w+)\(", prog).group(1)
        path = os.path.join(a.out, f"{name}.heyvl")
        with open(path, "w") as f:
            f.write(prog)
        res = {}
        for mode in ["passify", "--no-passify"]:
            dt, verdict, (mmax, _, _) = branchrun.run(a.caesar, path, mode, None, a.timeout)
            timed_out = not branchrun.finished(verdict)
            res[mode] = (a.timeout if timed_out else dt, verdict, timed_out, mmax)
        (tp, vp, top, ssa_meas), (tn, vn, ton, _) = res["passify"], res["--no-passify"]
        # "excluded": not comparable, never counts as best / as an improvement
        excluded = (top or ton) if self.laddered() else (top and ton)
        both_out = top and ton
        sc = 0.0 if excluded else self.score(tp, tn)
        r = dict(cfg=cfg, path=path, score=sc, t_passify=tp, t_no_passify=tn,
                 v_passify=vp, v_no_passify=vn, excluded=excluded)
        row = dict(n=n, phase=phase, score=f"{sc:.3f}", t_passify=f"{tp:.3f}",
                   t_no_passify=f"{tn:.3f}", passify_timed_out=top, no_passify_timed_out=ton,
                   v_passify=vp, v_no_passify=vn, ssa_meas_max=ssa_meas, file=path,
                   genargs=describe(cfg), **{k2: cfg[k2] for k2 in SPACE},
                   **{s: st[s] for s in ["assigns", "branches", "paths", "max_writes_per_var",
                                         "max_ssa_per_var", "total_ssa", "joins"]})
        mark = (" (both timed out)" if both_out else " (timed out, excluded)") if excluded else ""
        with self.lock:
            self.cache[k] = r
            if self.w is None:
                self.w = csv.DictWriter(self.fh, fieldnames=list(row))
                if self.fh.tell() == 0:
                    self.w.writeheader()
            self.w.writerow(row)
            self.fh.flush()
            print(f"[{n}] {phase:6} score={sc:+6.2f}  passify {tp:6.2f}s{'*' if top else ' '} "
                  f"no-passify {tn:6.2f}s{'*' if ton else ' '}{mark}  {describe(cfg)}", flush=True)
        return r

    def best(self, k=1):
        pool = self.shapes if self.laddered() else self.cache
        with self.lock:
            rs = [r for r in pool.values() if r and not r["excluded"]]
        return sorted(rs, key=lambda r: -r["score"])[:k]


class _Done(Exception):
    """crossover finished; skip the sampling/climbing of the other objectives."""


def rung_signed(r):
    """Sign information of a single run record, or None if not comparable."""
    if r is None or not (branchrun.finished(r["v_passify"]) or branchrun.finished(r["v_no_passify"])):
        return None
    return signed(r)


def crossover(a, s, run_batch):
    known = lambda r: r is not None and r.get("signed") is not None  # noqa: E731

    # 1. endpoints, each grown along the size ladder
    ends = {}
    if a.end_passify:
        ends["A"] = from_args(a.end_passify)
    if a.end_no_passify:
        ends["B"] = from_args(a.end_no_passify)
    if len(ends) < 2:
        rs = [r for r in run_batch([s.random_cfg() for _ in range(a.samples)], "random") if known(r)]
        if not rs:
            sys.exit("no random shape gave a usable measurement; raise --timeout or --samples")
        rs.sort(key=lambda r: r["signed"])
        ends.setdefault("A", rs[0]["cfg"])
        ends.setdefault("B", rs[-1]["cfg"])
    A, B = ends["A"], ends["B"]

    # 2. pin gen_seed/coproc to one endpoint's values; the pinned endpoints must
    #    still lie on opposite sides
    pair = None
    for a_, b_ in [(A, B), (A, dict(B, **{k: A[k] for k in PINNED})),
                   (dict(A, **{k: B[k] for k in PINNED}), B)]:
        if any(a_[k] != b_[k] for k in PINNED):
            continue
        ra, rb = run_batch([a_, b_], "end")
        if known(ra) and known(rb) and ra["signed"] < 0 < rb["signed"]:
            pair = (a_, b_, ra, rb)
            break
        s.log(f"endpoint pair not on opposite sides: {ra and ra.get('signed')} / {rb and rb.get('signed')}")
    if pair is None:
        sys.exit("could not find endpoints with passify winning at one and --no-passify at the other "
                 "(with gen_seed/coproc pinned); give them with --end-passify / --end-no-passify")
    A, B, ra, rb = pair

    # 3. one size for the whole path, then make sure the endpoints still disagree there
    U = a.cross_units or max(ra["decisive"]["cfg"]["units"], rb["decisive"]["cfg"]["units"])
    A, B = dict(A, units=U), dict(B, units=U)
    ra, rb = run_batch([A, B], "end", fixed_size=True)
    sa, sb = rung_signed(ra), rung_signed(rb)
    if sa is None or sb is None or not sa < 0 < sb:
        sys.exit(f"at --cross-units {U} the endpoints do not lie on opposite sides "
                 f"(log2 ratios {sa} / {sb}); try another --cross-units")
    s.log(f"\ncrossover at units={U} between\n"
          f"  A (t=0, passify {2**-sa:.1f}x faster):     {describe(A)}\n"
          f"  B (t=1, --no-passify {2**sb:.1f}x faster): {describe(B)}")

    # 4. bisection (N-section with --jobs points per round)
    points = {0.0: ra, 1.0: rb}
    lo, hi = 0.0, 1.0
    for rnd in range(a.rounds):
        n = max(1, a.jobs)
        ts = [lo + (hi - lo) * i / (n + 1) for i in range(1, n + 1)]
        cfgs = [dict(interp(A, B, t), units=U) for t in ts]
        ends_k = (key(dict(interp(A, B, lo), units=U)), key(dict(interp(A, B, hi), units=U)))
        if all(key(c) in ends_k for c in cfgs):
            s.log("interval is a single discrete step, stopping")
            break
        run_batch(cfgs, f"bisect{rnd}", fixed_size=True)
        for t, c in zip(ts, cfgs):
            points[t] = s.cache.get(key(c))
        # new interval: first sign change from passify-wins to no-passify-wins
        seq = [(t, rung_signed(points[t])) for t in sorted(points) if lo <= t <= hi]
        seq = [(t, v) for t, v in seq if v is not None]
        for (t0, v0), (t1, v1) in zip(seq, seq[1:]):
            if v0 < 0 <= v1:
                lo, hi = t0, t1
                break
        s.log(f"round {rnd}: interval t in [{lo:.4f}, {hi:.4f}]  "
              f"log2 ratio {rung_signed(points[lo]):+.2f} .. {rung_signed(points[hi]):+.2f}")

    # 5. report the path
    path = os.path.join(a.out, "crossover.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "units", "log2_tp_over_tn", "t_passify", "t_no_passify",
                    "v_passify", "v_no_passify", "file", "genargs"])
        for t in sorted(points):
            r, v = points[t], rung_signed(points[t])
            if r is None:
                w.writerow([f"{t:.5f}", U, "", "", "", "", "", "", describe(dict(interp(A, B, t), units=U))])
                continue
            w.writerow([f"{t:.5f}", U, "" if v is None else f"{v:.3f}", f"{r['t_passify']:.3f}",
                        f"{r['t_no_passify']:.3f}", r["v_passify"], r["v_no_passify"], r["path"],
                        describe(r["cfg"])])
    print(f"\npath from A (t=0) to B (t=1) at units={U}, log2(t_passify / t_no_passify), * = timed out:")
    for t in sorted(points):
        r, v = points[t], rung_signed(points[t])
        if v is None:
            print(f"  t={t:.4f}  (no measurement)")
            continue
        sp = "*" if not branchrun.finished(r["v_passify"]) else " "
        sn = "*" if not branchrun.finished(r["v_no_passify"]) else " "
        print(f"  t={t:.4f}  {v:+6.2f}  passify {r['t_passify']:6.2f}s{sp} no-passify {r['t_no_passify']:6.2f}s{sn}")
    clo, chi = interp(A, B, lo), interp(A, B, hi)
    diff = [f"{k}: {clo[k]} -> {chi[k]}" for k in SPACE if k != "units" and clo[k] != chi[k]]
    print(f"final interval t in [{lo:.4f}, {hi:.4f}]; parameters that still differ: {', '.join(diff) or 'none'}")
    print(f"path written to {path}")

    # points on the path ranked by closeness to equal runtime; timeouts last
    ranked = []
    for t, r in points.items():
        v = rung_signed(r)
        if v is None:
            continue
        both = branchrun.finished(r["v_passify"]) and branchrun.finished(r["v_no_passify"])
        ranked.append(dict(r, score=-abs(v) if both else UNREACHED - abs(v)))
    return sorted(ranked, key=lambda r: -r["score"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caesar")
    ap.add_argument("--samples", type=int, default=40, help="random configs to evaluate (default 40)")
    ap.add_argument("--climb", type=int, default=40, help="hill-climbing steps afterwards (default 40)")
    ap.add_argument("--objective", choices=["no-passify", "passify", "balanced", "crossover"], default="no-passify",
                    help="which mode should win; balanced: both slow and about equal (hill climbing); "
                         "crossover: bisect between a passify-wins and a no-passify-wins shape (default: no-passify)")
    ap.add_argument("--end-passify", metavar="ARGS",
                    help="crossover: branchgen args of a shape where passify wins (default: best of --samples)")
    ap.add_argument("--end-no-passify", metavar="ARGS",
                    help="crossover: branchgen args of a shape where --no-passify wins (default: best of --samples)")
    ap.add_argument("--rounds", type=int, default=8, help="crossover: bisection rounds (default 8)")
    ap.add_argument("--cross-units", type=int, default=None,
                    help="crossover: --units for every point on the path (default: from the endpoints' ladders)")
    ap.add_argument("--ladder", type=branchrun.ints, default=LADDER,
                    help=f"balanced/crossover: --units values tried per shape, smallest first "
                         f"(default {','.join(map(str, LADDER))}, ~sqrt(2) steps)")
    ap.add_argument("--target", type=float, default=None,
                    help="balanced/crossover: stop growing a shape once a mode takes this long (default --timeout / 3)")
    ap.add_argument("--timeout", type=int, default=20, help="caesar --timeout per run (default 20)")
    ap.add_argument("--mem", type=int, default=None,
                    help="caesar --mem (MB) per run (caesar's default: 8192). --no-passify typically "
                         "fails by exhausting memory, so with --jobs keep jobs x mem below the RAM")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="configs evaluated in parallel (default 1). caesar uses one core per run, so "
                         "up to the number of *physical* cores minus one; --confirm always runs serially")
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

    if a.target is None:
        a.target = a.timeout / 3
    if a.mem:
        branchrun.EXTRA_FLAGS[:] = ["--mem", str(a.mem)]
    s = Search(a, space)
    ex = ThreadPoolExecutor(max_workers=a.jobs)

    def run_batch(cfgs, phase, fixed_size=False):
        """Evaluate configs on the worker pool (deduplicated, configs drawn in
        this thread so the search seed alone decides what gets tried).
        fixed_size: one run per config at its own units (no ladder)."""
        uniq = {}
        for c in cfgs:
            uniq.setdefault(key(c) if fixed_size else s.eval_key(c), c)
        fn = s.run_cfg if fixed_size else s.evaluate
        return list(ex.map(lambda c: fn(c, phase), uniq.values()))

    cross_top = None
    try:
        if a.objective == "crossover":
            cross_top = crossover(a, s, run_batch)
            raise _Done
        if a.start:
            run_batch([from_args(a.start)], "start")
        run_batch([s.random_cfg() for _ in range(a.samples)], "random")
        cur, done = s.best(), 0
        while done < a.climb:
            n = min(a.jobs, a.climb - done)
            done += n
            if not cur:
                run_batch([s.random_cfg() for _ in range(n)], "random")
                cur = s.best()
                continue
            # one round: --jobs mutations of the current best in parallel, move
            # to the best of them; >= lets the climb drift across plateaus
            rs = [r for r in run_batch([s.mutate(cur[0]["cfg"]) for _ in range(n)], "climb")
                  if r and not r["excluded"]]
            if rs:
                b = max(rs, key=lambda r: r["score"])
                if b["score"] >= cur[0]["score"]:
                    cur = [b]
    except KeyboardInterrupt:
        print("\ninterrupted, results so far:", file=sys.stderr)
        ex.shutdown(wait=False, cancel_futures=True)
    except _Done:
        ex.shutdown()
    else:
        ex.shutdown()

    top = s.best(a.top)
    if cross_top is not None:
        top = cross_top[:a.top]
        print(f"\ntop {len(top)} points on the path, closest to equal runtime "
              f"(score = -|log2 time ratio|, 0 = equal; * = a run timed out):")
    elif s.laddered():
        print(f"\ntop {len(top)} shapes, at the size where the slower mode first took >= {a.target:g}s "
              f"(score = -|log2 time ratio|, 0 = equal; <= {UNREACHED:g}: target not reached):")
    else:
        winner = "--no-passify" if a.objective == "no-passify" else "passify"
        print(f"\ntop {len(top)} configs where {winner} wins most (score = log2 speedup; * = lower bound, a run timed out):")
    for r in top:
        star = " " if branchrun.finished(r["v_passify"]) and branchrun.finished(r["v_no_passify"]) else "*"
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
                 *(["--mem", str(a.mem)] if a.mem else []),
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
