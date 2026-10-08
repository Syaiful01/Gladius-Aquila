"""Pengirim notifikasi Telegram (HTML) dengan retry dan mode dry-run.

Aturan karakter: ``send_message`` otomatis dibungkus persona. ``send_alert`` dan
``send_report`` menerima pesan yang SUDAH dirender ``Persona`` (template resmi),
sehingga tidak ada pesan yang lolos tanpa karakter Gladius Aquila.
"""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TelegramError

from character.persona import Persona
from config import Config
from utils.helpers import chunk_text
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 3
_TAG_RE = re.compile(r"<[^>]+>")


class TelegramNotifier:
    def __init__(self, config: Config, persona: Persona, dry_run: bool = False) -> None:
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._persona = persona
        self._dry_run = dry_run or not config.telegram_configured
        if self._dry_run:
            logger.warning("Notifier dalam mode DRY-RUN: pesan hanya dicetak ke console.")

    # ------------------------------------------------------------------
    # API publik
    # ------------------------------------------------------------------
    def send_message(self, text: str) -> bool:
        """Kirim teks bebas — otomatis dibungkus persona."""
        return self._deliver(self._persona.say(text), silent=False)

    def send_alert(self, message: str) -> bool:
        """Kirim alert (dengan bunyi notifikasi). ``message`` hasil template Persona."""
        return self._deliver(message, silent=False)

    def send_report(self, message: str) -> bool:
        """Kirim laporan rutin (senyap, tanpa bunyi). ``message`` hasil template Persona."""
        return self._deliver(message, silent=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _deliver(self, html_text: str, silent: bool) -> bool:
        if self._dry_run:
            print("\n" + "=" * 60 + "\n" + _TAG_RE.sub("", html_text).replace("&amp;", "&") + "\n" + "=" * 60)
            return True
        try:
            asyncio.run(self._send_async(chunk_text(html_text), silent))
            return True
        except Exception as exc:  # jangan sampai kegagalan Telegram menjatuhkan agent
            logger.error("Gagal mengirim ke Telegram: %s", exc)
            return False

    async def _send_async(self, chunks: list[str], silent: bool) -> None:
        # Bot dibuat per pengiriman agar aman dengan asyncio.run() berulang
        async with Bot(token=self._token) as bot:
            for chunk in chunks:
                await self._send_chunk(bot, chunk, silent)

    async def _send_chunk(self, bot: Bot, text: str, silent: bool) -> None:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_notification=silent,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            except RetryAfter as exc:  # kena flood-control Telegram
                delay = exc.retry_after
                delay = delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
                logger.warning("Telegram rate limit, tunggu %.0f detik.", delay)
                await asyncio.sleep(delay + 1)
            except NetworkError as exc:  # termasuk TimedOut
                if attempt == MAX_ATTEMPTS:
                    raise
                logger.warning("Jaringan Telegram bermasalah (%s), ulangi.", exc)
                await asyncio.sleep(2 ** attempt)
            except TelegramError:
                raise  # token/chat id salah, format HTML rusak, dsb. — tidak perlu retry
        raise TelegramError("Gagal mengirim pesan setelah beberapa percobaan.")
