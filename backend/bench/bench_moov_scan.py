"""Benchmark: repeated _scan_mp4_moov vs cached moov.

Measures the I/O overhead eliminated by caching moov into info + tracker.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.services.torrent_engine import _scan_mp4_moov

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "torrent")
SAMPLE_PATH = os.path.join(
    CACHE_DIR, "c2fe9437eef243096ce5789a8d5a435df6ee5fa3",
    "SNOS-171", "hhd800.com@SNOS-171.mp4"
)


def bench_moov_scan_vs_cache(iterations: int = 1000) -> None:
    """Compare raw _scan_mp4_moov (16MB disk read) vs cached lookup."""
    if not os.path.exists(SAMPLE_PATH):
        print(f"SKIP: sample not found: {SAMPLE_PATH}")
        return

    # Clear any module-level cache first
    from backend.services.torrent_engine import _MOOV_CACHE
    _MOOV_CACHE.clear()

    # Warmup disk cache
    for _ in range(3):
        _scan_mp4_moov(SAMPLE_PATH)
    _MOOV_CACHE.clear()

    # Benchmark raw scan (simulates old get_status behavior)
    t0 = time.perf_counter()
    for _ in range(iterations):
        result = _scan_mp4_moov(SAMPLE_PATH)
        # Simulate the second scan that happened inside get_status
        result2 = _scan_mp4_moov(SAMPLE_PATH)
    t_old = time.perf_counter() - t0

    # Benchmark cached lookup (new behavior)
    # Pre-populate cache as _on_metadata would do
    cached = _scan_mp4_moov(SAMPLE_PATH)
    t0 = time.perf_counter()
    for _ in range(iterations):
        result = cached  # direct dict lookup
    t_new = time.perf_counter() - t0

    print(f"  moov scan vs cache ({iterations:,} iterations)")
    print(f"    old (2×_scan_mp4_moov): {t_old*1000:.3f} ms  ({t_old/iterations*1000:.3f} ms/req)")
    print(f"    new (cached lookup):    {t_new*1000:.3f} ms  ({t_new/iterations*1000:.3f} ms/req)")
    print(f"    speedup:                {t_old/t_new:.1f}x")
    print(f"    disk read saved:        {iterations * 32 / 1024:.1f} MB total")


def bench_get_status_overhead() -> None:
    """Estimate real-world /api/check/ latency improvement.

    Frontend polls once per second.  Old path does 2× moov scan per call.
    """
    if not os.path.exists(SAMPLE_PATH):
        print(f"SKIP: sample not found: {SAMPLE_PATH}")
        return

    from backend.services.torrent_engine import _MOOV_CACHE
    _MOOV_CACHE.clear()

    # Measure single _scan_mp4_moov latency (cold cache)
    t0 = time.perf_counter()
    for _ in range(10):
        _MOOV_CACHE.clear()
        _scan_mp4_moov(SAMPLE_PATH)
    t_scan_cold = (time.perf_counter() - t0) / 10

    # Measure cached lookup
    _scan_mp4_moov(SAMPLE_PATH)  # warm
    t0 = time.perf_counter()
    for _ in range(10_000):
        _MOOV_CACHE.get(SAMPLE_PATH)
    t_lookup = (time.perf_counter() - t0) / 10_000

    print(f"  /api/check/ latency per poll")
    print(f"    old (2× cold scan):  {t_scan_cold*2*1000:.3f} ms")
    print(f"    new (cached):        {t_lookup*1000:.3f} ms")
    print(f"    improvement:         {t_scan_cold*2*1000 - t_lookup*1000:.3f} ms saved per request")
    print(f"    at 1 req/s:          {(t_scan_cold*2*1000 - t_lookup*1000)*3600/1000:.1f} seconds saved per hour")


if __name__ == "__main__":
    print("=== Moov Scan Benchmark ===")
    print()
    bench_moov_scan_vs_cache()
    print()
    bench_get_status_overhead()
