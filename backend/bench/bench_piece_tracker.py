"""Benchmark: list-based state vs int bitmap state.

Measures the core operations that moved from O(n) Python loops
to O(1) C-level bit ops (POPCNT).
"""
from __future__ import annotations

import enum
import time


class OldPieceState(enum.IntEnum):
    NOT_DOWNLOADED = 0
    DOWNLOADING = 1
    VERIFIED = 2
    CORRUPT = 3


def bench_head_ready(num_pieces: int = 5000, moov_pieces: int = 5, iterations: int = 100_000) -> None:
    """Compare old O(moov_pieces) loop vs new O(1) POPCNT."""
    # Old: list of states
    old_states = [OldPieceState.NOT_DOWNLOADED] * num_pieces
    for p in range(moov_pieces):
        old_states[p] = OldPieceState.VERIFIED

    # New: int bitmap + pre-computed counters
    new_verified = (1 << moov_pieces) - 1
    new_moov_pc = moov_pieces
    new_moov_vc = new_verified.bit_count()

    # Warmup
    for _ in range(1000):
        all(old_states[p] == OldPieceState.VERIFIED for p in range(moov_pieces))
        new_moov_vc == new_moov_pc

    t0 = time.perf_counter()
    for _ in range(iterations):
        result = all(old_states[p] == OldPieceState.VERIFIED for p in range(moov_pieces))
    t_old = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(iterations):
        result = new_moov_vc == new_moov_pc
    t_new = time.perf_counter() - t0

    print(f"  head_ready ({moov_pieces} moov pieces, {iterations:,} iterations)")
    print(f"    old (list loop): {t_old*1000:.3f} ms  ({t_old/iterations*1e9:.1f} ns/op)")
    print(f"    new (int cmp):   {t_new*1000:.3f} ms  ({t_new/iterations*1e9:.1f} ns/op)")
    print(f"    speedup:         {t_old/t_new:.1f}x")


def bench_verified_count(num_pieces: int = 5000, verified_pieces: int = 2500, iterations: int = 100_000) -> None:
    """Compare old O(n) sum loop vs new O(1) POPCNT."""
    # Old: list
    old_states = [OldPieceState.NOT_DOWNLOADED] * num_pieces
    for p in range(verified_pieces):
        old_states[p] = OldPieceState.VERIFIED

    # New: int bitmap
    new_verified = (1 << verified_pieces) - 1

    # Warmup
    for _ in range(1000):
        sum(1 for s in old_states if s == OldPieceState.VERIFIED)
        new_verified.bit_count()

    t0 = time.perf_counter()
    for _ in range(iterations):
        result = sum(1 for s in old_states if s == OldPieceState.VERIFIED)
    t_old = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(iterations):
        result = new_verified.bit_count()
    t_new = time.perf_counter() - t0

    print(f"  verified_count ({num_pieces} pieces, {verified_pieces} verified, {iterations:,} iterations)")
    print(f"    old (list sum): {t_old*1000:.3f} ms  ({t_old/iterations*1e9:.1f} ns/op)")
    print(f"    new (POPCNT):   {t_new*1000:.3f} ms  ({t_new/iterations*1e9:.1f} ns/op)")
    print(f"    speedup:        {t_old/t_new:.1f}x")


def bench_overlay_have_piece(num_pieces: int = 5000, have_pieces: int = 2500, iterations: int = 10_000) -> None:
    """Compare old list overlay vs new bitmap overlay.

    Both must iterate all pieces (libtorrent API limitation: have_piece()
    is per-piece).  The gain comes from faster state check inside the loop.
    """
    # Simulate have_piece bitmap
    have_mask = (1 << have_pieces) - 1

    # Old: list
    old_states = [OldPieceState.NOT_DOWNLOADED] * num_pieces

    # New: int bitmap
    new_verified = 0

    # Warmup
    for p in range(num_pieces):
        if (have_mask >> p) & 1:
            if old_states[p] == OldPieceState.NOT_DOWNLOADED:
                old_states[p] = OldPieceState.VERIFIED
        elif True and old_states[p] == OldPieceState.VERIFIED:
            old_states[p] = OldPieceState.NOT_DOWNLOADED

    new_v = 0
    for p in range(num_pieces):
        if (have_mask >> p) & 1:
            if not (new_v & (1 << p)):
                new_v |= (1 << p)
        elif True and (new_v & (1 << p)):
            new_v &= ~(1 << p)

    t0 = time.perf_counter()
    for _ in range(iterations):
        states = [OldPieceState.NOT_DOWNLOADED] * num_pieces
        for p in range(num_pieces):
            if (have_mask >> p) & 1:
                if states[p] == OldPieceState.NOT_DOWNLOADED:
                    states[p] = OldPieceState.VERIFIED
            elif True and states[p] == OldPieceState.VERIFIED:
                states[p] = OldPieceState.NOT_DOWNLOADED
    t_old = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(iterations):
        verified = 0
        for p in range(num_pieces):
            if (have_mask >> p) & 1:
                if not (verified & (1 << p)):
                    verified |= (1 << p)
            elif True and (verified & (1 << p)):
                verified &= ~(1 << p)
    t_new = time.perf_counter() - t0

    print(f"  _overlay_have_piece ({num_pieces} pieces, strict=True, {iterations:,} iterations)")
    print(f"    old (list):    {t_old*1000:.3f} ms  ({t_old/iterations*1000:.3f} ms/iter)")
    print(f"    new (bitmap):  {t_new*1000:.3f} ms  ({t_new/iterations*1000:.3f} ms/iter)")
    print(f"    speedup:       {t_old/t_new:.1f}x")


if __name__ == "__main__":
    print("=== PieceStateTracker Benchmark ===")
    print()
    bench_head_ready()
    print()
    bench_verified_count()
    print()
    bench_overlay_have_piece()
