#!/usr/bin/env python3
"""Performance benchmark: CPython vs PyPy

Usage:
    uv run --python 3.14 benchmark.py
    uv run --python pypy@3.11 benchmark.py
"""
import sys
import time
import statistics
from simulation import build_simulation

WARMUP_RUNS = 3
TIMED_RUNS  = 20
HORIZON     = 60
SEED        = 42


def single_run(seed: int = SEED) -> None:
    m = build_simulation(seed=seed, horizon_years=HORIZON, enable_cats=True)
    m.run()


def main() -> None:
    impl    = sys.implementation.name          # 'cpython' or 'pypy'
    version = ".".join(str(x) for x in sys.version_info[:3])

    print(f"Interpreter : {impl} {version}")
    print(f"Warmup runs : {WARMUP_RUNS}")
    print(f"Timed runs  : {TIMED_RUNS}  ×  {HORIZON}-year simulation (cats=True, seed={SEED})")
    print()

    print("Warming up...", end=" ", flush=True)
    for i in range(WARMUP_RUNS):
        single_run(seed=i)
    print("done\n")

    times: list[float] = []
    for _ in range(TIMED_RUNS):
        t0 = time.perf_counter()
        single_run()
        times.append(time.perf_counter() - t0)

    mean_ms  = statistics.mean(times)  * 1_000
    min_ms   = min(times)              * 1_000
    max_ms   = max(times)              * 1_000
    stdev_ms = statistics.stdev(times) * 1_000

    print(f"Results ({TIMED_RUNS} timed runs):")
    print(f"  Mean   : {mean_ms:8.1f} ms")
    print(f"  Min    : {min_ms:8.1f} ms")
    print(f"  Max    : {max_ms:8.1f} ms")
    print(f"  Stdev  : {stdev_ms:8.1f} ms")
    print(f"  Runs/s : {1_000 / mean_ms:8.2f}")


if __name__ == "__main__":
    main()
