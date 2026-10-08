"""Web Command Center Backend untuk Gladius Aquila."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import GladiusAquila, CycleResult
from config import Config
from data.signal_store import SignalStore
from data.futures_data import FuturesDataClient
from utils.helpers import utc_now
from utils.logger import get_logger

logger = get_logger("gladius.web")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# ─── State global controller ───────────────────────────────────────────────
class AgentState:
    def __init__(self):
        self.is_patrolling: bool = False
        self.last_scan_at: str | None = None
        self.cycle_count: int = 0
        self.latest_result: dict[str, Any] = {
            "signals": [],
            "news_upcoming": [],
            "news_released": [],
            "news_next": [],
            "gold_proxy": None,
            "alerts_sent": 0,
            "report_sent": False,
        }
        self.dialogue: str = "Boss, Gladius Aquila standby di Tactical Command Center. Siap memindai radar pasar."
        self.dialogue_type: str = "info"   # "info" | "alert" | "scanning" | "report"
        self.logs: list[dict[str, str]] = []
        self.agent_instance: GladiusAquila | None = None
        self.patrol_thread: threading.Thread | None = None
        self.lock = threading.Lock()

        # Fase 4: WebSocket connection manager
        self.ws_clients: list[WebSocket] = []
        self.ws_lock = threading.Lock()

state = AgentState()

# ─── Singleton helpers ─────────────────────────────────────────────────────
_signal_store: SignalStore | None = None
_futures_client: FuturesDataClient | None = None


def get_signal_store() -> SignalStore:
    global _signal_store
    if _signal_store is None:
        _signal_store = SignalStore()
    return _signal_store


def get_futures_client() -> FuturesDataClient:
    global _futures_client
    if _futures_client is None:
        _futures_client = FuturesDataClient()
    return _futures_client


# ─── Logging helper ─────────────────────────────────────────────────────────
def log_event(level: str, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    with state.lock:
        state.logs.append({"time": timestamp, "level": level, "message": message})
        if len(state.logs) > 100:
            state.logs.pop(0)


# ─── WebSocket broadcast ────────────────────────────────────────────────────
async def broadcast_state(data: dict):
    """Kirim update state ke semua WebSocket client yang terhubung."""
    with state.ws_lock:
        dead: list[WebSocket] = []
        for ws in state.ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.ws_clients.remove(ws)


# ─── Core scan logic ────────────────────────────────────────────────────────
def run_agent_scan(force_report: bool = False) -> dict[str, Any]:
    with state.lock:
        state.dialogue = "Radar diaktifkan. Memindai volume, momentum crypto, dan kalender XAUUSD..."
        state.dialogue_type = "scanning"

    log_event("INFO", "Memulai pemindaian radar pasar...")
    cfg = Config.from_env()
    if state.agent_instance is None:
        state.agent_instance = GladiusAquila(cfg)

    cycle_res: CycleResult = state.agent_instance.run_cycle(force_report=force_report)

    # Format signals
    signals_data = []
    for s in cycle_res.signals:
        signals_data.append({
            "symbol": s.symbol,
            "base": s.symbol.split("/")[0] if "/" in s.symbol else s.symbol,
            "price": s.price,
            "type": s.signal_type.value,
            "score": s.score,
            "reasons": list(s.reasons),
            "volume_24h_usd": s.quote_volume_24h,
            "change_24h_pct": s.change_24h_pct,
            "rsi": s.rsi,
        })

    # Format berita mendatang
    news_up = []
    for ev, imp in cycle_res.news.upcoming:
        news_up.append({
            "title": ev.title, "currency": ev.currency, "impact": ev.impact,
            "time_utc": ev.time_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "bias": imp.bias.value, "reason": imp.reason,
            "forecast": ev.forecast, "previous": ev.previous, "actual": ev.actual,
        })

    # Format berita yang sudah rilis
    news_rel = []
    for ev, imp in cycle_res.news.released:
        news_rel.append({
            "title": ev.title, "currency": ev.currency, "impact": ev.impact,
            "time_utc": ev.time_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "bias": imp.bias.value, "reason": imp.reason,
            "forecast": ev.forecast, "previous": ev.previous, "actual": ev.actual,
        })

    # Format event berikutnya
    news_nxt = []
    for ev in cycle_res.news.next_events:
        news_nxt.append({
            "title": ev.title, "currency": ev.currency, "impact": ev.impact,
            "time_utc": ev.time_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "forecast": ev.forecast, "previous": ev.previous, "actual": ev.actual,
        })

    # Gold proxy
    gold_info = None
    try:
        gp = state.agent_instance._market.fetch_gold_proxy()
        if gp:
            gold_info = {
                "symbol": gp.symbol, "price": gp.price,
                "change_24h_pct": gp.change_24h_pct, "source": gp.source,
            }
    except Exception:
        pass

    with state.lock:
        state.cycle_count = cycle_res.cycle
        state.last_scan_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state.latest_result = {
            "signals": signals_data,
            "news_upcoming": news_up,
            "news_released": news_rel,
            "news_next": news_nxt,
            "gold_proxy": gold_info,
            "alerts_sent": cycle_res.alerts_sent,
            "report_sent": cycle_res.report_sent,
        }

        # Dialog berdasarkan hasil
        if signals_data:
            top_sig = sorted(signals_data, key=lambda x: x["score"], reverse=True)[0]
            state.dialogue = (
                f"Boss! Terdeteksi target menarik: {top_sig['symbol']} "
                f"({top_sig['type']}) dengan skor {top_sig['score']}/100!"
            )
            state.dialogue_type = "alert"
        elif news_up:
            ev_title = news_up[0]["title"]
            state.dialogue = f"Boss, waspada! Berita ekonomi High Impact XAUUSD mendekat: {ev_title}."
            state.dialogue_type = "alert"
        else:
            state.dialogue = f"Siklus #{cycle_res.cycle} selesai, Boss. Pasar relatif tenang, radar tetap memantau."
            state.dialogue_type = "report"

    log_event(
        "SUCCESS",
        f"Siklus #{cycle_res.cycle} selesai: {len(signals_data)} sinyal, {len(news_up)} alert berita.",
    )
    return state.latest_result


def patrol_worker():
    log_event("INFO", "Mode Patroli 24 Jam diaktifkan.")
    cfg = Config.from_env()
    while state.is_patrolling:
        try:
            run_agent_scan()
        except Exception as e:
            log_event("ERROR", f"Error pada siklus patroli: {e}")

        interval = max(30, cfg.scan_interval_seconds)
        for _ in range(int(interval)):
            if not state.is_patrolling:
                break
            time.sleep(1)
    log_event("INFO", "Mode Patroli dihentikan.")


# ─── FastAPI App ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("INFO", "Tactical AI Command Center Gladius Aquila diinisialisasi.")
    yield
    state.is_patrolling = False


app = FastAPI(title="Gladius Aquila Tactical Command Center", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Routes ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Template index.html belum dibuat.")
    return FileResponse(index_file)


@app.get("/api/state")
async def get_state():
    with state.lock:
        cfg = Config.from_env()
        return {
            "agent_name": cfg.agent_name,
            "agent_emoji": cfg.agent_emoji,
            "user_title": cfg.user_title,
            "is_patrolling": state.is_patrolling,
            "cycle_count": state.cycle_count,
            "last_scan_at": state.last_scan_at,
            "dialogue": state.dialogue,
            "dialogue_type": state.dialogue_type,
            "telegram_configured": bool(cfg.telegram_bot_token and cfg.telegram_chat_id),
            "latest_result": state.latest_result,
            "logs": state.logs[-30:],
        }


@app.post("/api/scan")
async def trigger_scan():
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_agent_scan, True)
    # Broadcast ke WebSocket clients
    try:
        snap = await get_state()
        await broadcast_state(snap)
    except Exception:
        pass
    return {"status": "ok", "result": result}


@app.post("/api/toggle-patrol")
async def toggle_patrol():
    with state.lock:
        state.is_patrolling = not state.is_patrolling
        current_status = state.is_patrolling

    if current_status:
        t = threading.Thread(target=patrol_worker, daemon=True)
        state.patrol_thread = t
        t.start()
        with state.lock:
            state.dialogue = "Patroli 24 Jam diaktifkan! Saya akan memindai radar secara berkala dan melaporkan ke Boss & Telegram."
            state.dialogue_type = "info"
    else:
        with state.lock:
            state.dialogue = "Patroli diistirahatkan sementara, Boss. Standby untuk perintah selanjutnya."
            state.dialogue_type = "info"

    return {"status": "ok", "is_patrolling": current_status}


@app.post("/api/test-telegram")
async def test_telegram():
    cfg = Config.from_env()
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        raise HTTPException(status_code=400, detail="Telegram token/chat_id belum dikonfigurasi di .env")

    from notifications.telegram_bot import TelegramNotifier
    from character.persona import Persona
    notifier = TelegramNotifier(cfg, Persona(cfg))
    success = notifier.send_alert(
        f"🦅 <b>Laporan Uji Coba dari AI Command Center:</b>\n"
        f"Gladius Aquila terhubung dan siap mengawal Boss!"
    )
    log_event("INFO" if success else "WARNING", "Mengirim uji coba notifikasi ke Telegram...")
    return {"status": "ok", "sent": success}


# ─── Fase 2: Riwayat sinyal ──────────────────────────────────────────────────
@app.get("/api/history")
async def get_signal_history(hours: int = 24, limit: int = 100, signal_type: str = ""):
    """Riwayat sinyal dari SQLite. Filter per tipe: HIGH_VOLUME, STRONG_MOMENTUM, EXHAUSTION."""
    store = get_signal_store()
    rows = store.get_recent(hours=min(hours, 168), limit=min(limit, 500))
    if signal_type:
        rows = [r for r in rows if r["signal_type"] == signal_type.upper()]
    return {"status": "ok", "count": len(rows), "signals": rows}


@app.get("/api/history/stats")
async def get_history_stats():
    """Statistik ringkas sinyal 24 jam terakhir."""
    store = get_signal_store()
    return {"status": "ok", "stats": store.get_stats()}


@app.get("/api/history/{symbol}")
async def get_symbol_history(symbol: str, limit: int = 50):
    """Riwayat sinyal satu koin (misal BTC%2FUSDT)."""
    store = get_signal_store()
    rows = store.get_symbol_history(symbol.upper(), limit=min(limit, 200))
    return {"status": "ok", "symbol": symbol.upper(), "count": len(rows), "signals": rows}


# ─── Fase 3: Futures data ────────────────────────────────────────────────────
@app.get("/api/futures")
async def get_futures_data(top_n: int = 10, min_oi_usd: float = 50_000_000):
    """Top N koin berdasarkan funding rate absolut terbesar."""
    client = get_futures_client()
    loop = asyncio.get_running_loop()
    try:
        items = await loop.run_in_executor(
            None,
            lambda: client.get_top_funding(n=min(top_n, 30), min_oi_usd=min_oi_usd)
        )
        return {
            "status": "ok",
            "count": len(items),
            "data": [
                {
                    "symbol": x.symbol,
                    "base": x.base,
                    "funding_rate_pct": round(x.funding_rate, 6),
                    "funding_rate_annualized_pct": round(x.funding_rate_annualized, 2),
                    "open_interest_usd": x.open_interest_usd,
                    "sentiment": x.sentiment,
                    "source": x.source,
                }
                for x in items
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Futures data tidak tersedia: {exc}")


@app.get("/api/futures/{base}")
async def get_futures_by_base(base: str):
    """Funding rate & OI untuk satu koin (misal BTC)."""
    client = get_futures_client()
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, lambda: client.get_by_base(base.upper()))
    if info is None:
        raise HTTPException(status_code=404, detail=f"Tidak ada data futures untuk {base.upper()}")
    return {
        "status": "ok",
        "symbol": info.symbol,
        "funding_rate_pct": round(info.funding_rate, 6),
        "funding_rate_annualized_pct": round(info.funding_rate_annualized, 2),
        "open_interest_usd": info.open_interest_usd,
        "sentiment": info.sentiment,
        "long_short_ratio": info.long_short_ratio,
        "source": info.source,
    }


# ─── Fase 4: WebSocket ───────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint untuk live push state ke browser."""
    await ws.accept()
    with state.ws_lock:
        state.ws_clients.append(ws)
    log_event("INFO", f"WebSocket client terhubung. Total: {len(state.ws_clients)}")
    try:
        # Kirim state awal langsung
        snap = await get_state()
        await ws.send_json(snap)
        # Keep-alive: tunggu pesan dari client (ping/pong)
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket error: %s", exc)
    finally:
        with state.ws_lock:
            if ws in state.ws_clients:
                state.ws_clients.remove(ws)
        log_event("INFO", f"WebSocket client terputus. Sisa: {len(state.ws_clients)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
