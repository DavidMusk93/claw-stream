#!/usrusr/bin/env python3
"""PieceStateTracker — piece-level download state management.

Replaces filesystem-level hole scanning with O(1) piece state queries.
Syncs with libtorrent alerts to maintain accurate have_piece knowledge,
avoiding the stale-bitmap trap after service restarts.

Phase 3.5 architecture: head_ready is a piece-state query, not a scan.
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
    3. Provide O(1) head_ready queries.
    4. Manage play-window piece priorities.
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

        # piece index (global) -> PieceState
        self._states: list[PieceState] = [PieceState.NOT_DOWNLOADED] * self.num_pieces

        # Scan filesystem once to bootstrap verified pieces
        self._bootstrap_from_filesystem()

        # Overlay libtorrent have_piece (may be stale, but gives a baseline)
        self._overlay_have_piece()

        log.debug(
            "PieceStateTracker init",
            extra={
                "start_piece": self.start_piece,
                "end_piece": self.end_piece,
                "video_pieces": self.end_piece - self.start_piece + 1,
                "verified": sum(1 for s in self._states if s == PieceState.VERIFIED),
            },
        )

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

        # Reset before scan: recheck may have turned verified pieces into holes
        for p in range(self.start_piece, self.end_piece + 1):
            self._states[p] = PieceState.NOT_DOWNLOADED

        fd = os.open(self.path, os.O_RDONLY)
        try:
            offset = self.file_offset
            file_end = self.file_offset + self.video_size
            piece_len = self.piece_length

            while offset < file_end:
                piece = offset // piece_len
                piece_end = min((piece + 1) * piece_len, file_end)

                try:
                    hole = os.lseek(fd, offset, os.SEEK_HOLE)
                except OSError:
                    break

                if hole >= piece_end:
                    # Entire piece has data — safe to mark VERIFIED
                    self._states[piece] = PieceState.VERIFIED
                # Partial data is NOT marked: libtorrent may have zeroed
                # the rest during recheck, and reading that region returns
                # zeros which causes Safari/Chrome demuxer stutter.

                offset = piece_end
        finally:
            os.close(fd)

    def _overlay_have_piece(self) -> None:
        """Augment with libtorrent have_piece (used as optimistic hint)."""
        for p in range(self.start_piece, self.end_piece + 1):
            if self._states[p] == PieceState.NOT_DOWNLOADED:
                if self.handle.have_piece(p):
                    self._states[p] = PieceState.VERIFIED

    # ── Alert sync ──────────────────────────────────────────

    def on_piece_finished(self, piece: int) -> None:
        """Libtorrent confirmed piece hash is valid."""
        if self.start_piece <= piece <= self.end_piece:
            self._states[piece] = PieceState.VERIFIED
            log.debug(
                "piece verified",
                extra={"piece": piece, "state": "verified"},
            )

    def on_hash_failed(self, piece: int) -> None:
        """Libtorrent hash check failed — data is corrupt."""
        if self.start_piece <= piece <= self.end_piece:
            self._states[piece] = PieceState.CORRUPT
            log.warning(
                "piece corrupt",
                extra={"piece": piece},
            )

    # ── Queries ─────────────────────────────────────────────

    def is_verified(self, piece: int) -> bool:
        return self._states[piece] == PieceState.VERIFIED

    def piece_state(self, piece: int) -> PieceState:
        return self._states[piece]

    def verified_count(self) -> int:
        return sum(
            1 for p in range(self.start_piece, self.end_piece + 1)
            if self._states[p] == PieceState.VERIFIED
        )

    def head_ready(
        self,
        moov_start: int,
        moov_end: int,
    ) -> bool:
        """O(pieces_in_moov) query — no filesystem scan.

        moov_start/moov_end are *file byte offsets* (same units as video file).
        We map them to piece indices and check all covering pieces are VERIFIED.
        """
        moov_start_piece = (self.file_offset + moov_start) // self.piece_length
        moov_end_piece = (self.file_offset + moov_end) // self.piece_length

        for p in range(moov_start_piece, min(moov_end_piece + 1, self.num_pieces)):
            if self._states[p] != PieceState.VERIFIED:
                return False
        return True

    # ── Actions ─────────────────────────────────────────────

    def request_pieces(self, start_piece: int, end_piece: int) -> int:
        """Set urgency on pieces that are NOT_DOWNLOADED.

        Returns number of pieces newly marked DOWNLOADING.
        """
        count = 0
        for p in range(max(start_piece, self.start_piece),
                       min(end_piece, self.end_piece) + 1):
            if self._states[p] in (PieceState.NOT_DOWNLOADED, PieceState.CORRUPT):
                self.handle.set_piece_deadline(p, 0)
                old = self.handle.piece_priority(p)
                if old != 7:
                    self.handle.piece_priority(p, 7)
                self._states[p] = PieceState.DOWNLOADING
                count += 1
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
        for p in range(self.start_piece, self.end_piece + 1):
            self.handle.piece_priority(p, 0)
            if self._states[p] == PieceState.DOWNLOADING:
                self._states[p] = PieceState.NOT_DOWNLOADED
