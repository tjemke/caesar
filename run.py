# Usage: python3 run.py <path/to/caesar> <family> <n1,n2,...> [timeout_s]
# Families: double fib flipdouble codouble coflipdouble (see gen.py).
# Writes <family>_<n>.heyvl into the current directory, runs caesar verify with
# and without --no-passify, and prints wall time and verdict for each.
import os
import re
import subprocess
import sys
import time

C = sys.argv[1]
fam = sys.argv[2]
ns = [int(x) for x in sys.argv[3].split(",")]
to = int(sys.argv[4]) if len(sys.argv) > 4 else 30
for n in ns:
    f = f"{fam}_{n}.heyvl"
    open(f, "w").write(
        subprocess.run(
            [
                "python3",
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "gen.py"),
                fam,
                str(n),
            ],
            capture_output=True,
            text=True,
        ).stdout
    )
    for mode in [[], ["--no-passify"], ["--skip-dist"], ["--encode-dist"]]:
        t = time.time()
        try:
            p = subprocess.run(
                [C, "verify", f, "--timeout", str(to)] + mode,
                capture_output=True,
                text=True,
                timeout=to + 30,
            )
            out = p.stdout + p.stderr
            m = re.search(
                r"Verified|Counter-example to verification found|[Tt]imeout|unknown|[Ee]rror.*",
                out,
            )
            r = m.group(0) if m else out.strip()[-80:]
        except subprocess.TimeoutExpired:
            r = "killed (wall timeout)"
        print(
            f"{fam:11} n={n:<3} {' '.join(mode) or 'passify':13} {time.time() - t:7.2f}s  {r}",
            flush=True,
        )
