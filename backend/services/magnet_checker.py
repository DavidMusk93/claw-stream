"""backend/services/magnet_checker.py — Magnet liveness checker

Verifies whether a magnet link's swarm is actually alive by adding it to a
dedicated libtorrent session (completely isolated from TorrentEngine's
streaming session) and waiting for metadata to arrive. No payload data is
ever downloaded: torrents are added with default_dont_download and removed
immediately after the check.

Sync class driven by its own thread pool — the FastAPI layer calls it via
asyncio.to_thread, and scripts/check_magnets.py can use it directly.
"""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import libtorrent as lt

from core.logger import get_logger

log = get_logger("magnet-checker")

DEFAULT_TIMEOUT_SEC = float(os.environ.get("MAGNET_CHECK_TIMEOUT", "60"))
# A swarm with peers but slow metadata gets one grace period before being
# declared dead — avoids killing live-but-slow swarms.
GRACE_TIMEOUT_SEC = float(os.environ.get("MAGNET_CHECK_GRACE_TIMEOUT", "90"))
DEFAULT_CONCURRENCY = int(os.environ.get("MAGNET_CHECK_CONCURRENCY", "32"))
POLL_INTERVAL_SEC = 0.5

_HASH_RE = re.compile(r"xt=urn:btih:([0-9a-fA-F]{40})")


def extract_hash(magnet: str) -> str | None:
    m = _HASH_RE.search(magnet or "")
    return m.group(1).lower() if m else None


def has_cached_torrent(cache_dir: str, hash_str: str) -> bool:
    """Disk-is-truth shortcut: a cached .torrent proves the swarm once worked."""
    return os.path.exists(os.path.join(cache_dir, hash_str, f"{hash_str}.torrent"))


class MagnetChecker:
    """Batch magnet liveness checker backed by a private libtorrent session."""

    def __init__(
        self,
        work_dir: str,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        grace_timeout: float = GRACE_TIMEOUT_SEC,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self.work_dir = work_dir
        self.timeout = timeout
        self.grace_timeout = grace_timeout
        self.concurrency = concurrency
        os.makedirs(self.work_dir, exist_ok=True)

        self._session: lt.session | None = None
        self._session_lock = threading.Lock()
        # Hashes currently being checked — libtorrent rejects duplicate adds.
        self._in_flight: set[str] = set()
        self._cancel = threading.Event()

    def _get_session(self) -> lt.session:
        with self._session_lock:
            if self._session is None:
                session = lt.session()
                settings = session.get_settings()
                settings["alert_mask"] = int(lt.alert.category_t.status_notification)
                settings["connections_limit"] = 100
                # Metadata-only checks never download payloads; keep upload
                # near zero so checking never competes with HTTP streaming.
                settings["upload_rate_limit"] = 32 * 1024
                settings["download_rate_limit"] = 512 * 1024
                session.apply_settings(settings)
                self._session = session
                log.info("magnet checker session created")
            return self._session

    def check_one(
        self,
        magnet: str,
        timeout: float | None = None,
        extra_peers: list[tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        """Check a single magnet. Returns {"alive", "peers", "elapsed"}.

        alive = metadata received within `timeout`. If peers exist but
        metadata is slow, one grace period (`grace_timeout`) is granted.
        Any error is reported as dead — batch runs must never abort.
        """
        timeout = self.timeout if timeout is None else timeout
        hash_str = extract_hash(magnet)
        if not hash_str:
            return {"alive": False, "peers": 0, "elapsed": 0.0, "error": "bad magnet"}

        with self._session_lock:
            if hash_str in self._in_flight:
                return {"alive": False, "peers": 0, "elapsed": 0.0, "error": "duplicate"}
            self._in_flight.add(hash_str)

        start = time.time()
        handle = None
        try:
            session = self._get_session()
            params = lt.parse_magnet_uri(magnet)
            params.save_path = self.work_dir
            params.flags &= ~lt.torrent_flags.auto_managed
            params.flags &= ~lt.torrent_flags.seed_mode
            # Add paused, then resume: with default_dont_download libtorrent
            # fetches metadata only and never creates payload files.
            params.flags |= lt.torrent_flags.paused
            params.flags |= lt.torrent_flags.default_dont_download
            handle = session.add_torrent(params)
            for peer in extra_peers or []:
                try:
                    handle.connect_peer(peer, 0)
                except Exception:
                    pass
            handle.resume()

            deadline = start + timeout
            peers = 0
            while time.time() < deadline and not self._cancel.is_set():
                st = handle.status()
                peers = max(peers, st.num_peers)
                if st.has_metadata:
                    return {"alive": True, "peers": peers, "elapsed": round(time.time() - start, 1)}
                time.sleep(POLL_INTERVAL_SEC)

            if peers > 0 and not self._cancel.is_set():
                # Live swarm, slow metadata — grant one grace period.
                log.info(f"grace period for {hash_str[:12]}... (peers={peers})")
                deadline = time.time() + self.grace_timeout
                while time.time() < deadline and not self._cancel.is_set():
                    st = handle.status()
                    peers = max(peers, st.num_peers)
                    if st.has_metadata:
                        return {"alive": True, "peers": peers, "elapsed": round(time.time() - start, 1)}
                    time.sleep(POLL_INTERVAL_SEC)

            return {"alive": False, "peers": peers, "elapsed": round(time.time() - start, 1)}
        except Exception as e:
            log.warning(f"check failed for {hash_str[:12]}...: {type(e).__name__}: {e}")
            return {"alive": False, "peers": 0, "elapsed": round(time.time() - start, 1), "error": str(e)[:200]}
        finally:
            if handle is not None:
                try:
                    self._get_session().remove_torrent(handle)
                except Exception:
                    pass
            with self._session_lock:
                self._in_flight.discard(hash_str)

    def check_batch(
        self,
        items: list[dict[str, Any]],
        process: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_result: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        """Check many magnets concurrently.

        items: list of {"magnet": str, ...} — extra keys are passed through
        to on_result. process(item) produces the per-item outcome (defaults
        to a plain primary-magnet check); on_result(item, outcome) runs on a
        worker thread — keep it fast and thread-safe.
        Outcomes with outcome["alive"] truthy count as alive.
        Returns {"total", "alive", "dead"}.
        """
        if process is None:
            process = lambda item: self.check_one(item["magnet"])  # noqa: E731
        # Deduplicate by hash — a title's candidates can share info-hashes.
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in items:
            h = extract_hash(item.get("magnet") or "")
            if not h or h in seen:
                continue
            seen.add(h)
            unique.append(item)

        stats = {"total": len(unique), "alive": 0, "dead": 0}
        stats_lock = threading.Lock()
        self._cancel.clear()

        def _work(item: dict[str, Any]) -> None:
            if self._cancel.is_set():
                return
            result = process(item)
            with stats_lock:
                stats["alive" if result["alive"] else "dead"] += 1
            if on_result is not None:
                try:
                    on_result(item, result)
                except Exception:
                    log.exception("on_result callback failed")

        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="magnet-check") as pool:
            list(pool.map(_work, unique))

        return stats

    def cancel(self) -> None:
        self._cancel.set()

    def shutdown(self) -> None:
        self._cancel.set()
        with self._session_lock:
            self._session = None
        log.info("magnet checker shut down")
