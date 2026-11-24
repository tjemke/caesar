# Usage: python3 gen.py <family> <n>  -- prints a HeyVL program to stdout.
import sys
# Families of loop-free programs whose wp blows up under plain substitution.
def double(n):   # x used twice per step: substituted term has 2^n leaves
    body = "\n".join("    x = x + x + 1" for _ in range(n))
    return f"proc double{n}(x0: UInt) -> (x: UInt)\n    pre ?(true)\n    post ?(x >= {2**n - 1})\n{{\n    x = x0\n{body}\n}}\n"
def fib(n):      # a, b mixed: term sizes grow like Fibonacci
    body = "\n".join("    a = a + b\n    b = a + b" for _ in range(n))
    return f"proc fib{n}(a0: UInt, b0: UInt) -> (a: UInt, b: UInt)\n    pre ?(true)\n    post ?(b >= a)\n{{\n    a = a0\n    b = b0\n{body}\n}}\n"
def flipdouble(n):  # same as double, but each step adds a coin: probabilistic expectation
    body = "\n".join("    c = flip(1/2)\n    x = x + x + ite(c, 1, 0)" for _ in range(n))
    return (f"proc flipdouble{n}(x0: UInt) -> (x: UInt)\n    pre [x0 == 0] * ({2**n - 1}/2)\n    post x\n{{\n"
            f"    var c: Bool\n    x = x0\n{body}\n}}\n")
def codouble(n):
    body = "\n".join("    x = x + x + 1" for _ in range(n))
    return f"coproc codouble{n}(x0: UInt) -> (x: UInt)\n    pre {2**n} * x0 + {2**n - 1}\n    post x\n{{\n    x = x0\n{body}\n}}\n"
def coflipdouble(n):
    return flipdouble(n).replace("proc ", "coproc co", 1).replace(f"[x0 == 0] * ({2**n - 1}/2)", f"{2**n} * x0 + ({2**n - 1}/2)")
fam, n = sys.argv[1], int(sys.argv[2])
print({"double": double, "fib": fib, "flipdouble": flipdouble, "codouble": codouble, "coflipdouble": coflipdouble}[fam](n))
