"""Handler command Telegram interaktif untuk Gladius Aquila.

Command yang didukung:
    /status   — status agent saat ini (patroli aktif / idle, siklus terakhir)
    /scan     — picu pemindaian radar segera
    /report   — kirim laporan situasi lengkap
    /help     — daftar perintah

Handler berjalan di thread terpisah (loop polling ``getUpdates``). Bila bot
token tidak dikonfigurasi, handler menonaktifkan dirinya sendiri.

Desain:
- Satu thread daemon per agent (bukan per request).
- Polling timeout 20 detik agar CPU tidak terpakai terus.
- Setiap command dijalankan di thread executor agar tidak memblok poller.
- Anti-spam: command dari chat selain TELEGRAM_CHAT_ID diabaikan.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TelegramError

from character.persona import Persona
from config import Config
from utils.helpers import format_usd, utc_now
from utils.logger import get_logger

if TYPE_CHECKING:
    from agent import GladiusAquila, CycleResult

logger = get_logger(__name__)

_POLL_TIMEOUT = 20      # detik; lebih hemat dari long-polling penuh
_EXECUTOR_WORKERS = 2   # maksimum command berjalan bersamaan


class TelegramCommandHandler:
    """Menerima dan menjalankan command Telegram secara asinkron.

    Cara pakai:
        handler = TelegramCommandHandler(config, persona, agent)
        handler.start()   # non-blocking, jalan di daemon thread
        ...
        handler.stop()
    """

    def __init__(self, config: Config, persona: Persona, agent: "GladiusAquila") -> None:
        self._token    = config.telegram_bot_token
        self._chat_id  = str(config.telegram_chat_id)
        self._persona  = persona
        self._agent    = agent
        self._enabled  = config.telegram_configured
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=_EXECUTOR_WORKERS,
                                            thread_name_prefix="gladius-cmd")
        self._last_update_id: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._enabled:
            logger.info("Command handler dinonaktifkan (Telegram belum dikonfigurasi).")
            return
        self._thread = threading.Thread(
            target=self._poll_loop, name="gladius-cmd-poller", daemon=True
        )
        self._thread.start()
        logger.info("Telegram Command Handler berjalan.")

    def stop(self) -> None:
        self._stop_evt.set()
        self._executor.shutdown(wait=False)
        logger.info("Telegram Command Handler dihentikan.")

    # ------------------------------------------------------------------
    # Loop polling
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        """Thread utama: polling update Telegram setiap _POLL_TIMEOUT detik."""
        while not self._stop_evt.is_set():
            try:
                asyncio.run(self._poll_once())
            except Exception as exc:
                logger.warning("Command poller error: %s", exc)
                self._stop_evt.wait(5)  # cooldown sebelum retry

    async def _poll_once(self) -> None:
        async with Bot(token=self._token) as bot:
            try:
                updates = await bot.get_updates(
                    offset=self._last_update_id + 1,
                    timeout=_POLL_TIMEOUT,
                    allowed_updates=["message"],
                )
            except (NetworkError, RetryAfter) as exc:
                logger.debug("getUpdates network error: %s", exc)
                return
            for update in updates:
                self._last_update_id = update.update_id
                if update.message and update.message.text:
                    self._dispatch(update, bot)

    # ------------------------------------------------------------------
    # Dispatch command
    # ------------------------------------------------------------------
    def _dispatch(self, update: Update, bot: Bot) -> None:
        """Validasi pengirim dan jalankan handler command."""
        msg = update.message
        if msg is None:
            return
        chat_id = str(msg.chat_id)
        text = (msg.text or "").strip()

        # Hanya chat yang dikonfigurasi yang boleh kirim command
        if chat_id != self._chat_id:
            logger.warning("Command dari chat tidak dikenal (%s): %s", chat_id, text)
            return

        handlers: dict[str, Callable] = {
            "/start":  self._cmd_help,
            "/help":   self._cmd_help,
            "/status": self._cmd_status,
            "/scan":   self._cmd_scan,
            "/report": self._cmd_report,
        }

        # Ambil command (abaikan @botname suffix)
        cmd = text.split("@")[0].split()[0].lower()
        handler = handlers.get(cmd)
        if handler is None:
            return  # bukan command yang dikenal, abaikan

        logger.info("Command %s diterima dari chat %s", cmd, chat_id)
        # Jalankan di thread executor agar loop async tidak terblok
        self._executor.submit(self._run_command, handler, bot, chat_id)

    def _run_command(self, handler: Callable, bot: Bot, chat_id: str) -> None:
        try:
            reply = handler()
            asyncio.run(self._send_reply(bot, chat_id, reply))
        except Exception as exc:
            logger.error("Error menjalankan command: %s", exc)

    async def _send_reply(self, bot: Bot, chat_id: str, text: str) -> None:
        try:
            async with Bot(token=self._token) as b:
                await b.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_notification=True,
                )
        except TelegramError as exc:
            logger.error("Gagal kirim reply: %s", exc)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    def _cmd_help(self) -> str:
        return (
            "🦅 <b>GLADIUS AQUILA — Command Center</b>\n\n"
            "<b>Perintah tersedia:</b>\n"
            "• /status  — status patroli &amp; siklus terakhir\n"
            "• /scan    — picu pemindaian radar sekarang\n"
            "• /report  — laporan situasi lengkap\n"
            "• /help    — tampilkan pesan ini\n\n"
            "<i>Command hanya berfungsi dari chat yang dikonfigurasi.</i>"
        )

    def _cmd_status(self) -> str:
        from data.signal_store import SignalStore
        try:
            store = SignalStore()
            stats = store.get_stats()
            by_type = stats["last_24h_by_type"]
            total_24h = sum(by_type.values())
            top_sym = ", ".join(
                f"{s['symbol']}({s['cnt']}x)" for s in stats["top_symbols_24h"][:3]
            ) or "—"
        except Exception:
            total_24h = "?"
            top_sym   = "?"

        cycle     = self._agent._cycle
        last_scan = getattr(self._agent, "_last_scan_at", None)
        patrolling = not self._agent._stop.is_set()

        status_icon = "🟢 PATROLI AKTIF" if patrolling else "🟡 STANDBY"
        last_str = (
            last_scan.astimezone(timezone.utc).strftime("%d %b %H:%M UTC")
            if last_scan else "Belum ada"
        )
        return (
            f"🦅 <b>GLADIUS AQUILA — Status</b>\n\n"
            f"Mode     : <b>{status_icon}</b>\n"
            f"Siklus   : <b>#{cycle}</b>\n"
            f"Scan akhir: <b>{last_str}</b>\n\n"
            f"📊 Sinyal 24j terakhir: <b>{total_24h}</b>\n"
            f"🔝 Top simbol: <code>{top_sym}</code>\n\n"
            f"<i>Siap menerima perintah selanjutnya, Boss.</i>"
        )

    def _cmd_scan(self) -> str:
        """Picu satu siklus scan segera (blocking di thread executor)."""
        try:
            result: CycleResult = self._agent.run_cycle(force_report=False)
            n_sig = len(result.signals)
            n_alert = result.alerts_sent
            return (
                f"🦅 <b>Scan Manual Selesai</b>\n\n"
                f"Siklus: #{result.cycle}\n"
                f"Sinyal ditemukan: <b>{n_sig}</b>\n"
                f"Alert terkirim: <b>{n_alert}</b>\n\n"
                f"<i>Laporan penuh dikirim terpisah bila ada yang layak dilaporkan.</i>"
            )
        except Exception as exc:
            logger.error("cmd_scan error: %s", exc)
            return f"⚠️ Scan gagal: <code>{exc}</code>"

    def _cmd_report(self) -> str:
        """Picu satu siklus scan + laporan penuh."""
        try:
            result: CycleResult = self._agent.run_cycle(force_report=True)
            return (
                f"🦅 <b>Laporan Situasi Terkirim</b>\n\n"
                f"Siklus #{result.cycle} | "
                f"{len(result.signals)} sinyal | "
                f"Laporan: {'✅' if result.report_sent else '❌'}"
            )
        except Exception as exc:
            logger.error("cmd_report error: %s", exc)
            return f"⚠️ Report gagal: <code>{exc}</code>"
