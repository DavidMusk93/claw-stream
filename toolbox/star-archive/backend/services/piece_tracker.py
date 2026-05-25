#!/usrusr/bin/env python3
"""PieceStateTracker — piece-level download state management.

Phase 3.6 architecture:
- 3 x Python int bitmaps encode 4 states (NOT_DOWNLOADED implied by all-zero)
- O(1) head_ready via pre-computed moov mask + POPCNT (int.bit_count)
- O(1) verified_count via POPCNT
- All bit ops run at C speed (single CPU instruction for POPCNT)

Replaces filesystem-level hole scanning with O(1) piece state queries.
Syncs with libtorrent alerts to maintain accurate have_piece knowledge,
avoiding the stale-bitmap trap after service restarts.
"""
from __future__ import annotations

import enum
import os
from typing import Any

import libtorrent as lt

from core import get_logger

log = get_logger("piece-tracker")


class PieceState(enum.IntEnum):
    """Download state of a single piece."""

    NOT_DOWNLOADED = 0
    DOWNLOADING = 1      # priority/deadline set, waiting for peers
    VERIFIED = 2         # hash-checked by libtorrent OR filesystem-confirmed
    CORRUPT = 3          # hash failed, needs re-download


class PieceStateTracker:
    """Track per-piece state for a single video torrent.

    Responsibilities:
    1. Initialise state from filesystem (SEEK_HOLE scan of video file range).
    2. Sync with libtorrent alerts (piece_finished, hash_failed).
    3. Provide O(1) head_ready queries via pre-computed moov mask.
    4. Provide O(1) verified_count via POPCNT.
    5. Manage play-window piece priorities.
    """

    def __init__(
        self,
        handle: lt.torrent_handle,
        video_idx: int,
        video_size: int,
        path: str,
    ) -> None:
        self.handle = handle
        self.video_idx = video_idx
        self.video_size = video_size
        self.path = path

        ti = handle.torrent_file()
        fs = ti.files()
        self.piece_length = ti.piece_length()
        self.file_offset = fs.file_offset(video_idx)
        self.num_pieces = ti.num_pieces()
        self.start_piece = self.file_offset // self.piece_length
        self.end_piece = min(
            self.num_pieces - 1,
            (self.file_offset + self.video_size) // self.piece_length,
        )

        # ── Bitmaps: 3 ints encode 4 states ─────────────────────
        # bit p = 1 in _verified   → VERIFIED
        # bit p = 1 in _corrupt    → CORRUPT
        # bit p = 1 in _downloading → DOWNLOADING
        # all 0 for a piece → NOT_DOWNLOADED
        self._verified = 0
        self._corrupt = 0
        self._downloading = 0

        # Pre-computed masks (set by set_moov_range / set_head_tail_counts)
        self._moov_mask = 0          # moov-covered pieces
        self._moov_pc = 0            # moov piece count
        self._moov_vc = 0            # verified moov piece count
        self._head_mask = 0          # head pieces (for reset_priorities)
        self._tail_mask = 0          # tail pieces

        # Scan filesystem once to bootstrap verified pieces
        self._bootstrap_from_filesystem()

        # Sync with libtorrent have_piece.
        # If torrent is NOT checking_files, have_piece is reliable after recheck.
        # Use strict=True to override false VERIFIED from zero-filled blocks.
        # If still checking, have_piece may be stale — use incremental sync only.
        status = handle.status()
        checking = getattr(status, "state", None) == lt.torrent_status.checking_files
        self._overlay_have_piece(strict=not checking)

        log.debug(
            "PieceStateTracker init",
            extra={
                "start_piece": self.start_piece,
                "end_piece": self.end_piece,
                "video_pieces": self.end_piece - self.start_piece + 1,
                "verified": self.verified_count(),
            },
        )

    # ── Pre-computation ─────────────────────────────────────

    def set_moov_range(self, moov_start: int, moov_end: int) -> None:
        """Set moov range once after _on_metadata scan. Enables O(1) head_ready."""
        sp = (self.file_offset + moov_start) // self.piece_length
        ep = (self.file_offset + moov_end) // self.piece_length
        self._moov_mask = ((1 << (ep - sp + 1)) - 1) << sp
        self._moov_pc = ep - sp + 1
        self._moov_vc = (self._verified & self._moov_mask).bit_count()

    def set_head_tail_counts(self, head_count: int, tail_count: int) -> None:
        """Pre-compute head/tail masks for fast reset_priorities."""
        head_end = min(self.start_piece + head_count, self.end_piece)
        tail_start = max(self.start_piece, self.end_piece - tail_count + 1)
        self._head_mask = ((1 << (head_end - self.start_piece + 1)) - 1) << self.start_piece
        self._tail_mask = ((1 << (self.end_piece - tail_start + 1)) - 1) << tail_start

    # ── Internal helpers ────────────────────────────────────

    def _set_verified(self, piece: int) -> None:
        """Mark a single piece as VERIFIED and update moov counter."""
        bit = 1 << piece
        if self._verified & bit:
            return
        self._verified |= bit
        self._corrupt &= ~bit
        self._downloading &= ~bit
        if bit & self._moov_mask:
            self._moov_vc += 1

    def _set_corrupt(self, piece: int) -> None:
        """Mark a single piece as CORRUPT."""
        bit = 1 << piece
        was_verified = bool(self._verified & bit)
        self._corrupt |= bit
        self._verified &= ~bit
        self._downloading &= ~bit
        if was_verified and (bit & self._moov_mask):
            self._moov_vc -= 1

    def _set_downloading(self, piece: int) -> None:
        """Mark a single piece as DOWNLOADING."""
        bit = 1 << piece
        was_verified = bool(self._verified & bit)
        self._downloading |= bit
        self._verified &= ~bit
        self._corrupt &= ~bit
        if was_verified and (bit & self._moov_mask):
            self._moov_vc -= 1

    # ── Bootstrap ───────────────────────────────────────────

    def _bootstrap_from_filesystem(self) -> None:
        """Use SEEK_HOLE to mark pieces that already have data on disk.

        Scanning resets all states first — libtorrent recheck zeros pieces
        on disk, which SEEK_HOLE sees as data. Without reset, previously
        verified pieces that turned into holes stay falsely VERIFIED.
        A piece is VERIFIED only if its *entire* range has no hole.
        """
        if not os.path.exists(self.path):
            return

        self._verified = 0
        self._corrupt = 0
        self._downloading = 0
        self._moov_vc = 0

        fd = os.open(self.path, os.O_RDONLY)
        try:
            offset = self.file_offset
            file_end = self.file_offset + self.video_size
            piece_len = self.piece_length

            while offset < file_end:
                piece = offset // piece_len
                piece_end = min((piece + 1) * piece_len, file_end)
                # fd is the video file; lseek offset must be relative to
                # the video file, not the torrent's absolute offset.
                offset_in_file = offset - self.file_offset
                piece_end_in_file = piece_end - self.file_offset

                try:
                    hole = os.lseek(fd, offset_in_file, os.SEEK_HOLE)
                except OSError:
                    break

                if hole >= piece_end_in_file:
                    # Entire piece has data — safe to mark VERIFIED
                    self._verified |= (1 << piece)
                # Partial data is NOT marked: libtorrent may have zeroed
                # the rest during recheck, and reading that region returns
                # zeros which causes Safari/Chrome demuxer stutter.

                offset = piece_end
        finally:
            os.close(fd)

    def _overlay_have_piece(self, strict: bool = False) -> None:
        """将 libtorrent have_piece 同步到 tracker 状态。

        默认模式下仅在 piece 当前为 NOT_DOWNLOADED 时覆盖（增量同步），
        避免破坏 tracker 自身的 DOWNLOADING / VERIFIED / CORRUPT 状态。

        strict=True 时做双向同步：have_piece 为 false 的 piece 强制重置为
        NOT_DOWNLOADED；have_piece 为 true 但 _bootstrap 未标记的 piece
        不强制标记（防止 recheck 时 page cache 误报）。
        用于 torrent_checked_alert 之后。
        """
        for p in range(self.start_piece, self.end_piece + 1):
            if self.handle.have_piece(p):
                if not (self._verified & (1 << p)):
                    # Strict mode: only trust have_piece if filesystem scan
                    # already confirmed data. Prevents page-cache false positives
                    # where libtorrent recheck reads stale cached zeros as valid.
                    if strict:
                        continue
                    self._set_verified(p)
            elif strict and (self._verified & (1 << p)):
                # recheck 完成后 have_piece 为 false 说明该 piece 未通过校验
                # 或仍是 zeros，不能信任 SEEK_HOLE 的扫描结果
                bit = 1 << p
                self._verified &= ~bit
                if bit & self._moov_mask:
                    self._moov_vc -= 1

    # ── Alert sync ──────────────────────────────────────────

    def on_piece_finished(self, piece: int) -> None:
        """Libtorrent confirmed piece hash is valid."""
        if self.start_piece <= piece <= self.end_piece:
            self._set_verified(piece)
            log.debug(
                "piece verified",
                extra={"piece": piece, "state": "verified"},
            )

    def on_hash_failed(self, piece: int) -> None:
        """Libtorrent hash check failed — data is corrupt."""
        if self.start_piece <= piece <= self.end_piece:
            self._set_corrupt(piece)
            log.warning(
                "piece corrupt",
                extra={"piece": piece},
            )

    # ── Queries ─────────────────────────────────────────────

    def is_verified(self, piece: int) -> bool:
        return bool(self._verified & (1 << piece))

    def piece_state(self, piece: int) -> PieceState:
        bit = 1 << piece
        if self._verified & bit:
            return PieceState.VERIFIED
        if self._corrupt & bit:
            return PieceState.CORRUPT
        if self._downloading & bit:
            return PieceState.DOWNLOADING
        return PieceState.NOT_DOWNLOADED

    def verified_count(self) -> int:
        """O(1) via POPCNT (int.bit_count)."""
        return self._verified.bit_count()

    def head_ready(self) -> bool:
        """O(1) via pre-computed moov mask + POPCNT.

        Requires set_moov_range() to have been called after _on_metadata.
        If not set, falls back to False (conservative).
        """
        if self._moov_pc == 0:
            return False
        return self._moov_vc == self._moov_pc

    # ── Actions ─────────────────────────────────────────────

    def request_pieces(self, start_piece: int, end_piece: int) -> int:
        """Set urgency on pieces that are NOT_DOWNLOADED.

        Returns number of pieces newly marked DOWNLOADING.
        """
        start = max(start_piece, self.start_piece)
        end = min(end_piece, self.end_piece)
        if start > end:
            return 0

        mask = ((1 << (end - start + 1)) - 1) << start
        # CORRUPT pieces ARE re-requested (they need re-download)
        unavailable = (self._verified | self._downloading) & mask
        need = (mask & ~unavailable) >> start  # align LSB to piece 'start'

        # Batch set priorities via prioritize_pieces — piece_priority() is
        # unreliable in libtorrent Python bindings (often silently ignored).
        pieces_to_set = []
        p = start
        temp = need
        while temp:
            if temp & 1:
                pieces_to_set.append(p)
            temp >>= 1
            p += 1

        if pieces_to_set:
            prios = list(self.handle.piece_priorities())
            for p in pieces_to_set:
                self.handle.set_piece_deadline(p, 0)
                if prios[p] != 7:
                    prios[p] = 7
            self.handle.prioritize_pieces(prios)

        count = 0
        p = start
        while need:
            if need & 1:
                # If libtorrent already has this piece (e.g. recheck left it
                # complete), mark VERIFIED immediately instead of DOWNLOADING.
                if self.handle.have_piece(p):
                    self._set_verified(p)
                else:
                    self._set_downloading(p)
                count += 1
            need >>= 1
            p += 1
        return count

    def request_head_tail(self, head_count: int = 30, tail_count: int = 30) -> int:
        """Request head + tail pieces (used when play starts)."""
        end = min(self.start_piece + head_count, self.end_piece)
        tail_start = max(self.start_piece, self.end_piece - tail_count + 1)

        count = self.request_pieces(self.start_piece, end)
        count += self.request_pieces(tail_start, self.end_piece)
        return count

    def request_window(self, center_piece: int, window_size: int = 30) -> int:
        """Request a sliding window around the current playback position."""
        win_start = max(self.start_piece, center_piece - window_size)
        win_end = min(self.end_piece, center_piece + window_size)
        return self.request_pieces(win_start, win_end)

    def reset_priorities(self) -> None:
        """Reset all video pieces to priority 0 (used on pause/stop)."""
        prios = list(self.handle.piece_priorities())
        changed = False
        for p in range(self.start_piece, self.end_piece + 1):
            if prios[p] > 0:
                prios[p] = 0
                changed = True
        if changed:
            self.handle.prioritize_pieces(prios)
        self._downloading = 0
