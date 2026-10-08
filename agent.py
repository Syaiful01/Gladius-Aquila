"""Otak Gladius Aquila: menjalankan siklus riset dan loop 24 jam."""
from __future__ import annotations

import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from character.persona import Persona
from config import Config
from data.crypto_scanner import CoinSignal, SignalType, scan_market
from data.fast_news_loop import FastNewsLoop
from data.futures_data import FuturesDataClient
from data.gold_news import GoldNewsMonitor, NewsCheckResult
from data.market_data import MarketDataClient
from data.signal_store import SignalStore
from notifications.telegram_bot import TelegramNotifier
from notifications.telegram_commands import TelegramCommandHandler
from utils.helpers import utc_now
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CycleResult:
    """Ringkasan satu siklus (berguna untuk tes & debugging)."""

    cycle: int
    signals: list[CoinSignal] = field(default_factory=list)
    news: NewsCheckResult = field(default_factory=NewsCheckResult)
    alerts_sent: int = 0
    report_sent: bool = False


class GladiusAquila:
    """Agent riset trading: scanner crypto + monitor berita Gold + notifikasi Telegram."""

    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self._cfg = config
        self._persona = Persona(config)
        self._notifier = TelegramNotifier(config, self._persona, dry_run=dry_run)
        self._market = MarketDataClient(config)
        self._news = GoldNewsMonitor(config) if config.news_enabled else None
        self._signal_store = SignalStore()
        self._futures = FuturesDataClient()

        self._cycle = 0
        self._last_report_at = None
        self._last_scan_at = None
        self._alert_history: dict[str, object] = {}   # "SYMBOL|TYPE" -> waktu alert terakhir
        self._news_sent: set[str] = set()              # "event_id|pre|post"
        self._error_history: dict[str, object] = {}    # konteks -> waktu error terakhir dikirim
        self._stop = threading.Event()

        # Komponen tambahan (Fase 1)
        self._cmd_handler = TelegramCommandHandler(config, self._persona, self)
        self._fast_news: FastNewsLoop | None = (
            FastNewsLoop(
                config=config,
                persona=self._persona,
                notifier=self._notifier,
                news_monitor=self._news,
                news_sent_registry=self._news_sent,
            )
            if self._news is not None else None
        )

    # ------------------------------------------------------------------
    # Siklus utama
    # ------------------------------------------------------------------
    def run_cycle(self, force_report: bool = False) -> CycleResult:
        """Satu putaran: scan crypto -> cek berita -> kirim alert -> (opsional) laporan."""
        self._cycle += 1
        result = CycleResult(cycle=self._cycle)
        logger.info("=== Siklus #%d dimulai ===", self._cycle)

        try:
            result.signals = scan_market(self._market, self._cfg)
            # Fase 2: Simpan sinyal ke SQLite
            if result.signals:
                self._signal_store.save_many(result.signals)
        except Exception as exc:
            self._handle_error("pemindaian crypto", exc)

        if self._news is not None:
            try:
                result.news = self._news.check()
            except Exception as exc:
                self._handle_error("kalender berita Gold", exc)

        result.alerts_sent += self._dispatch_news_alerts(result.news)
        result.alerts_sent += self._dispatch_crypto_alerts(result.signals)

        if force_report or self._report_due():
            result.report_sent = self._send_report(result)

        self._last_scan_at = utc_now()
        logger.info("=== Siklus #%d selesai (alert: %d, laporan: %s) ===",
                    self._cycle, result.alerts_sent, result.report_sent)
        return result

    def start(self) -> None:
        """Loop 24 jam sampai menerima SIGINT/SIGTERM (Ctrl+C / systemctl stop)."""
        self._install_signal_handlers()
        self._notifier.send_alert(self._persona.online(self._cfg.scan_interval_minutes))

        # Fase 1: Mulai command handler dan fast news loop
        self._cmd_handler.start()
        if self._fast_news:
            self._fast_news.start()

        # Purge sinyal lama sekali saat startup
        try:
            self._signal_store.purge_old(keep_days=30)
        except Exception as exc:
            logger.warning("Purge signal store gagal: %s", exc)

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.run_cycle()
            except Exception as exc:  # jaring pengaman terakhir — loop tidak boleh mati
                self._handle_error("siklus utama", exc)
            wait = max(5.0, self._cfg.scan_interval_seconds - (time.monotonic() - started))
            logger.info("Patroli berikutnya dalam %.0f detik.", wait)
            self._stop.wait(wait)

        # Shutdown berurutan
        self._cmd_handler.stop()
        if self._fast_news:
            self._fast_news.stop()
        self._notifier.send_alert(self._persona.shutdown())
        logger.info("Gladius Aquila berhenti dengan tertib.")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Alert
    # ------------------------------------------------------------------
    def _dispatch_news_alerts(self, news: NewsCheckResult) -> int:
        sent = 0
        for phase, items in (("upcoming", news.upcoming), ("released", news.released)):
            for event, impact in items:
                key = f"{event.event_id}|{phase}"
                if key in self._news_sent:
                    continue
                if self._notifier.send_alert(self._persona.gold_news_alert(event, impact, phase)):
                    self._news_sent.add(key)
                    sent += 1
        return sent

    def _dispatch_crypto_alerts(self, signals: list[CoinSignal]) -> int:
        renderers = {
            SignalType.HIGH_VOLUME: self._persona.volume_alert,
            SignalType.STRONG_MOMENTUM: self._persona.momentum_alert,
            SignalType.EXHAUSTION: self._persona.exhaustion_alert,
        }
        sent = 0
        for signal_ in signals:  # sudah terurut skor tertinggi
            if sent >= self._cfg.max_alerts_per_cycle:
                break
            if signal_.score < self._cfg.min_alert_score:
                continue
            key = f"{signal_.symbol}|{signal_.signal_type.value}"
            if self._in_cooldown(self._alert_history, key, self._cfg.alert_cooldown_minutes):
                continue
            if self._notifier.send_alert(renderers[signal_.signal_type](signal_)):
                self._alert_history[key] = utc_now()
                sent += 1
        return sent

    # ------------------------------------------------------------------
    # Laporan & error
    # ------------------------------------------------------------------
    def _report_due(self) -> bool:
        if self._last_report_at is None:
            return True
        return utc_now() - self._last_report_at >= timedelta(minutes=self._cfg.report_interval_minutes)

    def _send_report(self, result: CycleResult) -> bool:
        try:
            gold_proxy = self._market.fetch_gold_proxy()
        except Exception as exc:
            logger.warning("Proksi emas gagal: %s", exc)
            gold_proxy = None
        message = self._persona.routine_report(
            cycle=result.cycle,
            signals=result.signals,
            next_events=result.news.next_events,
            gold_proxy=gold_proxy,
        )
        ok = self._notifier.send_report(message)
        if ok:
            self._last_report_at = utc_now()
        return ok

    def _handle_error(self, context: str, exc: BaseException) -> None:
        logger.exception("Error pada %s: %s", context, exc)
        # Batasi spam: satu pesan error per konteks per rentang cooldown
        if self._in_cooldown(self._error_history, context, self._cfg.error_cooldown_minutes):
            return
        if self._notifier.send_alert(self._persona.error(context, exc)):
            self._error_history[context] = utc_now()

    # ------------------------------------------------------------------
    # Util
    # ------------------------------------------------------------------
    @staticmethod
    def _in_cooldown(history: dict[str, object], key: str, minutes: int) -> bool:
        last = history.get(key)
        return last is not None and utc_now() - last < timedelta(minutes=minutes)  # type: ignore[operator]

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: object) -> None:
            logger.info("Sinyal %s diterima — menghentikan agent...", signum)
            self._stop.set()

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except ValueError:  # bukan di main thread
            logger.debug("Signal handler tidak dipasang (bukan main thread).")

    # ------------------------------------------------------------------
    # Akses publik untuk web & command handler
    # ------------------------------------------------------------------
    @property
    def signal_store(self) -> SignalStore:
        return self._signal_store

    @property
    def futures_client(self) -> FuturesDataClient:
        return self._futures
