"""Loop berita cepat: polling lebih sering saat event high-impact hampir rilis.

Logika:
- Normalnya GoldNewsMonitor dicek setiap SCAN_INTERVAL (30 menit default).
- FastNewsLoop aktif FAST_ACTIVATE_MINUTES sebelum rilis event High Impact.
- Saat aktif, polling dilakukan setiap FAST_INTERVAL_SECONDS (60 detik default).
- Setelah event rilis (atau sudah lewat POST_WINDOW), kembali ke mode normal.

Thread terpisah, daemon, berjalan selama agent hidup.
Karakter dan notifikasi tetap lewat TelegramNotifier + Persona.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from data.gold_news import GoldNewsMonitor, NewsCheckResult
from utils.helpers import utc_now
from utils.logger import get_logger

if TYPE_CHECKING:
    from agent import GladiusAquila
    from config import Config
    from character.persona import Persona
    from notifications.telegram_bot import TelegramNotifier

logger = get_logger(__name__)

# ─── Konstanta default (dapat di-override via Config) ─────────────────────
FAST_ACTIVATE_MINUTES  = 30    # mulai loop cepat N menit sebelum event
FAST_INTERVAL_SECONDS  = 60    # interval polling saat mode cepat aktif
NORMAL_SLEEP_SECONDS   = 120   # sleep antar cek "apakah perlu aktif?" di mode normal


class FastNewsLoop:
    """Loop berita kecepatan tinggi menjelang rilis event high-impact.

    Berjalan di daemon thread terpisah. Memanggil ``_agent.run_cycle()``
    tidak tepat sasaran karena terlalu berat; FastNewsLoop hanya memperbarui
    bagian berita dan mengirim alert melalui notifier.
    """

    def __init__(
        self,
        config: "Config",
        persona: "Persona",
        notifier: "TelegramNotifier",
        news_monitor: GoldNewsMonitor,
        news_sent_registry: set[str],  # berbagi referensi dengan GladiusAquila
    ) -> None:
        self._cfg         = config
        self._persona     = persona
        self._notifier    = notifier
        self._monitor     = news_monitor
        self._news_sent   = news_sent_registry
        self._stop        = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="gladius-fast-news", daemon=True
        )
        self._thread.start()
        logger.info("FastNewsLoop dimulai.")

    def stop(self) -> None:
        self._stop.set()
        logger.info("FastNewsLoop dihentikan.")

    # ------------------------------------------------------------------
    # Loop utama
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.warning("FastNewsLoop error: %s", exc)
            # Tidur singkat antar iterasi (mode normal akan tidur lebih lama di dalam)
            self._stop.wait(5)

    def _tick(self) -> None:
        result = self._monitor.check()

        if self._needs_fast_mode(result):
            logger.info("Mode Berita Cepat aktif — polling setiap %d detik.", FAST_INTERVAL_SECONDS)
            self._fast_mode_run(result)
        else:
            # Mode normal: cukup cek sekali, lalu tunggu
            self._dispatch_alerts(result)
            self._stop.wait(NORMAL_SLEEP_SECONDS)

    def _needs_fast_mode(self, result: NewsCheckResult) -> bool:
        """True bila ada event kurang dari FAST_ACTIVATE_MINUTES menit lagi."""
        now = utc_now()
        threshold = timedelta(minutes=FAST_ACTIVATE_MINUTES)
        for event in result.next_events:
            if timedelta(0) < event.time_utc - now <= threshold:
                return True
        return False

    def _fast_mode_run(self, initial_result: NewsCheckResult) -> None:
        """Polling cepat sampai semua event di jendela selesai."""
        self._dispatch_alerts(initial_result)

        while not self._stop.is_set():
            self._stop.wait(FAST_INTERVAL_SECONDS)
            if self._stop.is_set():
                break

            fresh = self._monitor.check()
            self._dispatch_alerts(fresh)

            # Keluar dari mode cepat bila sudah tidak ada event mendekat
            if not self._needs_fast_mode(fresh) and not fresh.upcoming:
                logger.info("Mode Berita Cepat selesai — kembali ke mode normal.")
                break

    # ------------------------------------------------------------------
    # Alert dispatch (sama dengan agent.py tapi hanya untuk berita)
    # ------------------------------------------------------------------
    def _dispatch_alerts(self, result: NewsCheckResult) -> None:
        sent = 0
        for phase, items in (("upcoming", result.upcoming), ("released", result.released)):
            for event, impact in items:
                key = f"{event.event_id}|{phase}"
                if key in self._news_sent:
                    continue
                msg = self._persona.gold_news_alert(event, impact, phase)
                if self._notifier.send_alert(msg):
                    self._news_sent.add(key)
                    sent += 1
        if sent:
            logger.info("FastNewsLoop: %d alert berita dikirim.", sent)
