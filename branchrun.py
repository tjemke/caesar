#!/usr/bin/env python3
"""Sweep branchgen.py over branch ratios / depths / seeds and time caesar on each.

Usage:
  python3 branchrun.py <path/to/caesar> [--ratios 0,0.25,0.5,0.75,1] [--depths 1,2]
                       [--gen-seeds 0,1,2] [--z3-seeds 1,2,3]
                       [--modes passify,--no-passify] [--timeout 30]
                       [--hyperfine N [--warmup W]]
                       [--out DIR] [--csv results.csv] -- <extra branchgen.py args>

Two different seeds:
  --gen-seeds  seeds of branchgen.py's RNG: each one is a *different program*
               (other branch conditions / operands). CSV column gen_seed,
               file name suffix _g<seed>.
  --z3-seeds   values for caesar's --z3-seed: the *same program*, solved with a
               differently seeded Z3. CSV column z3_seed ("default" = flag not
               passed). Use this to see how much runtime is solver noise.

Example:
  python3 branchrun.py ./caesar --ratios 0,0.5,1 --depths 1,3 -- -u 12 -k 6 --dup 2

For every (ratio, depth, gen seed) it writes DIR/<name>.heyvl, runs `caesar
verify` once per (mode, z3 seed) and appends one CSV row per run. Mode `passify` means no extra
flag; any other mode string is passed through as caesar flags (space separated,
e.g. "--no-passify" or "--skip-dist").

Besides time and verdict, every run is done with --print-core, and the variable
versions `vN_k` in the printed core are counted. That gives the *measured*
number of versions per variable after passification (ssa_meas_*), next to the
static prediction from branchgen (ssa_*) and the number of writes in the
unpassified program (writes / max_writes_per_var). If ssa_meas_total is 0 in a
passify run, passification bailed out (NotYetSupported / DirectionMismatch) and
the run measured the unpassified program.

With --hyperfine N, each file is additionally benchmarked with hyperfine
(N runs, --warmup W) after the single probe run above. The probe still provides
verdict and version counts (hyperfine discards caesar's output); modes whose
probe did not finish with a verdict (timeout, "error: Interrupted", killed,
...) are not handed to hyperfine (decided per
(mode, z3 seed)). All remaining (mode, z3 seed) pairs of one file go into a
single hyperfine invocation, without --print-core, and
the CSV gets hf_mean/hf_stddev/hf_median/hf_min/hf_max (wall clock, seconds)
and hf_mem_mb (mean peak RSS) per mode.

With more than one --z3-seeds value, each mode additionally gets a row with
z3_seed=pooled: hf_* statistics over all hyperfine runs of all z3 seeds
(hf_runs = runs x seeds), plus hf_seeds, hf_between_seed_stddev (stddev of the
per-seed means) and hf_seed_mean_min/max, i.e. how much the runtime moves just
by reseeding Z3. Seeds that timed out are excluded and named in the verdict. Needs hyperfine >= 2.0. The
raw hyperfine JSON is kept next to the .heyvl file. If stderr is a terminal,
hyperfine's progress bar is shown on it while it runs and erased afterwards.
hyperfine's warnings (e.g. "first run was significantly slower") are printed
on stderr either way. hyperfine must be on PATH,
e.g. run everything inside `nix shell nixpkgs#hyperfine -c ...`.
"""
import argparse
import csv
import fcntl
import os
import pty
import struct
import termios
import json
import re
import shlex
import shutil
import statistics
import subprocess
import tempfile
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERDICT = re.compile(
    r"Verified|Counter-example[^\n]*|[Tt]imeout|unknown|[Ee]rror.*"
)
def finished(verdict):
    """True if caesar came to a verdict. Anything else (timeout, which caesar
    reports either as "Timed out ..." or as "error: Interrupted", killed,
    other errors) has no meaningful runtime."""
    return bool(re.match(r"Verified|Counter-example", verdict))


ANSI = re.compile(r"\x1b\[[0-9;]*m")
VERSION = re.compile(r"\b(v\d+)_(\d+)\b")


def floats(s):
    return [float(x) for x in s.split(",")]


def ints(s):
    return [int(x) for x in s.split(",")]


def gen(args):
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "branchgen.py")] + args,
        capture_output=True, text=True, check=True,
    )
    return p.stdout


def static_stats(prog):
    return dict(
        m.groups() for m in re.finditer(r"^// stat (\w+)=(.*)$", prog, re.M)
    )


def measured_versions(core):
    seen = {}
    for base, idx in VERSION.findall(core):
        seen.setdefault(base, set()).add(idx)
    per = {b: len(s) for b, s in seen.items()}
    return (
        max(per.values(), default=0),
        sum(per.values()),
        ",".join(f"{b}:{per[b]}" for b in sorted(per, key=lambda b: int(b[1:]))),
    )


def mode_flags(mode, z3_seed=None):
    flags = [] if mode == "passify" else mode.split()
    if z3_seed is not None:
        flags += ["--z3-seed", str(z3_seed)]
    return flags


def z3_label(z3_seed):
    return "default" if z3_seed is None else str(z3_seed)


def run_name(mode, z3_seed):
    return f"{mode} z3={z3_label(z3_seed)}"


HF_FIELDS = ["hf_runs", "hf_mean", "hf_stddev", "hf_median", "hf_min", "hf_max", "hf_mem_mb",
             "hf_seeds", "hf_between_seed_stddev", "hf_seed_mean_min", "hf_seed_mean_max"]


def time_stats(times):
    return dict(
        hf_runs=len(times),
        hf_mean=f"{statistics.mean(times):.4f}",
        hf_stddev=f"{statistics.stdev(times) if len(times) > 1 else 0.0:.4f}",
        hf_median=f"{statistics.median(times):.4f}",
        hf_min=f"{min(times):.4f}",
        hf_max=f"{max(times):.4f}",
    )


def pooled_row(rows, times):
    """Aggregate the per-z3-seed rows of one mode into a z3_seed=pooled row.

    times: {run_name: [wall clock seconds per hyperfine run]}. Timing stats are
    over all runs of all seeds that hyperfine measured; hf_between_seed_* show
    how far the per-seed means are apart. Seeds that timed out in the probe run
    have no hyperfine times and are listed in the verdict instead."""
    per_seed = {r["z3_seed"]: times[run_name(r["mode"], r["_z3"])] for r in rows
                if run_name(r["mode"], r["_z3"]) in times}
    verdicts = sorted({r["verdict"] for r in rows})
    timed_out = [r["z3_seed"] for r in rows if r["z3_seed"] not in per_seed]
    verdict = verdicts[0] if len(verdicts) == 1 else "mixed: " + " | ".join(verdicts)
    if timed_out:
        verdict += f" (no hyperfine for z3 seeds {','.join(timed_out)})"
    row = dict(rows[0], z3_seed="pooled", _z3="pooled", verdict=verdict,
               time_s=f"{statistics.mean(float(r['time_s']) for r in rows):.3f}",
               **{k: "" for k in HF_FIELDS})
    if per_seed:
        means = [statistics.mean(t) for t in per_seed.values()]
        mems = [float(r["hf_mem_mb"]) for r in rows if r["hf_mem_mb"]]
        row.update(time_stats([x for t in per_seed.values() for x in t]))
        row.update(
            hf_seeds=len(per_seed),
            hf_between_seed_stddev=f"{statistics.stdev(means) if len(means) > 1 else 0.0:.4f}",
            hf_seed_mean_min=f"{min(means):.4f}",
            hf_seed_mean_max=f"{max(means):.4f}",
            hf_mem_mb=f"{statistics.mean(mems):.1f}" if mems else "",
        )
    return row


def run_with_progress(cmd):
    """Run hyperfine with its stderr on a pty sized like our terminal, so it
    draws its progress bar, and copy that to our stderr. hyperfine erases the
    bar (and its "Benchmark N: ..." header) itself when done; warnings stay."""
    master, slave = pty.openpty()
    try:
        size = os.get_terminal_size(sys.stderr.fileno())
        # struct winsize: rows, cols, xpixel, ypixel
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size.lines, size.columns, 0, 0))
    except OSError:
        pass
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=slave)
    os.close(slave)
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:  # EIO once hyperfine has exited
            break
        if not data:
            break
        sys.stderr.buffer.write(data)
        sys.stderr.flush()
    os.close(master)
    return p.wait()


def hyperfine(caesar, path, runs_, to, runs, warmup):
    """Benchmark all (mode, z3_seed) pairs of one file in one hyperfine call
    -> ({run_name: stats}, {run_name: [seconds per run]})."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        js = tf.name
    progress = sys.stderr.isatty()
    cmd = ["hyperfine", "-N", "-i", "--style", "full" if progress else "none", "--runs", str(runs),
           "--warmup", str(warmup), "--export-json", js]
    for m, z in runs_:
        cmd += [f"--command-name={run_name(m, z)}",
                shlex.join([caesar, "verify", path, "--timeout", str(to)] + mode_flags(m, z))]
    # hyperfine's stderr (progress bar on a terminal, warnings, errors) goes to ours
    if progress:
        code = run_with_progress(cmd)
    else:
        code = subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode
    if code:
        sys.exit(f"hyperfine failed (exit {code})")
    res = json.load(open(js))
    shutil.move(js, os.path.splitext(path)[0] + ".hyperfine.json")
    out, times = {}, {}
    for r in res["results"]:  # hyperfine >= 2.0 JSON layout
        t = r["summary"]["time_wall_clock"]
        mem = r["summary"].get("memory_peak_resident", {}).get("mean")
        out[r["name"]] = dict(
            hf_runs=t["count"],
            hf_mem_mb=f"{mem / 2**20:.1f}" if mem else "",
            **{f"hf_{k}": f"{t[k]:.4f}" for k in ["mean", "stddev", "median", "min", "max"]},
        )
        times[r["name"]] = [m["time_wall_clock"]["value"] for m in r["measurements"]]
    return out, times


def run(caesar, path, mode, z3_seed, to):
    flags = mode_flags(mode, z3_seed)
    t = time.time()
    try:
        p = subprocess.run(
            [caesar, "verify", path, "--timeout", str(to), "--print-core"] + flags,
            capture_output=True, text=True, timeout=to + 30,
        )
        out = ANSI.sub("", p.stdout + p.stderr)
        m = VERDICT.search(out)
        verdict = m.group(0) if m else out.strip()[-80:]
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        verdict = "killed (wall timeout)"
    return time.time() - t, verdict, measured_versions(out)


def main():
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caesar")
    ap.add_argument("--ratios", type=floats, default=floats("0,0.25,0.5,0.75,1"))
    ap.add_argument("--depths", type=ints, default=[1])
    ap.add_argument("--gen-seeds", type=ints, default=[0],
                    help="branchgen RNG seeds: each gives a different program (default 0)")
    ap.add_argument("--z3-seeds", type=ints, default=None,
                    help="values for caesar --z3-seed: same program, different solver seed "
                         "(default: don't pass the flag)")
    ap.add_argument("--modes", default="passify,--no-passify", help="comma separated")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--out", default="branch-bench", help="directory for generated .heyvl files")
    ap.add_argument("--csv", default=None, help="CSV file to append to (default <out>/results.csv)")
    ap.add_argument("--hyperfine", type=int, default=0, metavar="N", help="also benchmark each file with hyperfine, N runs per mode")
    ap.add_argument("--warmup", type=int, default=1, help="hyperfine warmup runs (default 1)")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    csv_path = a.csv or os.path.join(a.out, "results.csv")
    new = not os.path.exists(csv_path)
    fh = open(csv_path, "a", newline="")
    w = None
    modes = a.modes.split(",")
    z3_seeds = a.z3_seeds or [None]
    if a.hyperfine and not shutil.which("hyperfine"):
        sys.exit("hyperfine not on PATH; try: nix shell nixpkgs#hyperfine -c python3 branchrun.py ...")

    for depth in a.depths:
        for ratio in a.ratios:
            for gen_seed in a.gen_seeds:
                gargs = extra + ["-r", str(ratio), "-d", str(depth), "--gen-seed", str(gen_seed)]
                try:
                    prog = gen(gargs)
                except subprocess.CalledProcessError as e:
                    print(f"skip r={ratio} d={depth}: {e.stderr.strip()}", file=sys.stderr)
                    continue
                st = static_stats(prog)
                name = re.search(r"proc (\w+)\(", prog).group(1)
                path = os.path.join(a.out, f"{name}.heyvl")
                with open(path, "w") as f:
                    f.write(prog)
                rows = []
                for mode in modes:
                  for z3 in z3_seeds:
                    dt, verdict, (mmax, mtot, mper) = run(a.caesar, path, mode, z3, a.timeout)
                    rows.append(dict(
                        name=name, ratio=ratio, depth=depth, gen_seed=gen_seed,
                        z3_seed=z3_label(z3), _z3=z3,
                        genargs=" ".join(extra), mode=mode, time_s=f"{dt:.3f}",
                        verdict=verdict, ssa_meas_max=mmax, ssa_meas_total=mtot,
                        ssa_meas=mper, **{k: "" for k in HF_FIELDS}, **st,
                    ))
                if a.hyperfine:
                    ok = [(r["mode"], r["_z3"]) for r in rows
                          if finished(r["verdict"])]
                    if ok:
                        hf, times = hyperfine(a.caesar, path, ok, a.timeout, a.hyperfine, a.warmup)
                        for r in rows:
                            r.update(hf.get(run_name(r["mode"], r["_z3"]), {}))
                        if len(z3_seeds) > 1:
                            # after each mode's per-seed rows, one row pooled over the seeds
                            pooled = []
                            for mode in modes:
                                mrows = [r for r in rows if r["mode"] == mode]
                                pooled += mrows + [pooled_row(mrows, times)]
                            rows = pooled
                for row in rows:
                    del row["_z3"]
                    mode, verdict, mmax = row["mode"], row["verdict"], row["ssa_meas_max"]
                    dt = float(row["time_s"])
                    hfs = f"  hf {float(row['hf_mean']):7.3f}s ± {float(row['hf_stddev']):.3f}" if row["hf_mean"] else ""
                    if row["hf_seeds"]:
                        hfs += (f" over {row['hf_runs']} runs/{row['hf_seeds']} seeds, seed means "
                                f"{float(row['hf_seed_mean_min']):.3f}..{float(row['hf_seed_mean_max']):.3f}s")
                    if w is None:
                        w = csv.DictWriter(fh, fieldnames=list(row))
                        if new:
                            w.writeheader()
                        else:
                            with open(csv_path) as old:
                                hdr = old.readline().strip()
                            if hdr != ",".join(w.fieldnames):
                                sys.exit(f"{csv_path} has different columns (older format?); "
                                         "use a new --csv / --out")
                    w.writerow(row)
                    fh.flush()
                    print(
                        f"r={ratio:<5} d={depth} gen_seed={gen_seed} paths={st['paths']:>8} "
                        f"writes/var={st['max_writes_per_var']:>4} ssa/var={st['max_ssa_per_var']:>4} "
                        f"meas={mmax:>4}  {mode:13} z3_seed={row['z3_seed']:<7} {dt:7.2f}s{hfs}  {verdict}",
                        flush=True,
                    )
    print(f"results appended to {csv_path}")


if __name__ == "__main__":
    main()
