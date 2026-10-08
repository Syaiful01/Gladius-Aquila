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

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import GladiusAquila, CycleResult
from config import Config
from utils.helpers import utc_now
from utils.logger import get_logger

logger = get_logger("gladius.web")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# State global controller
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
        self.dialogue_type: str = "info" # "info" | "alert" | "scanning" | "report"
        self.logs: list[dict[str, str]] = []
        self.agent_instance: GladiusAquila | None = None
        self.patrol_thread: threading.Thread | None = None
        self.lock = threading.Lock()

state = AgentState()

def log_event(level: str, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    with state.lock:
        state.logs.append({"time": timestamp, "level": level, "message": message})
        if len(state.logs) > 100:
            state.logs.pop(0)

def run_agent_scan(force_report: bool = False) -> dict[str, Any]:
    with state.lock:
        state.dialogue = "Radar diaktifkan. Memindai volume, momentum crypto, dan kalender XAUUSD..."
        state.dialogue_type = "scanning"
    
    log_event("INFO", "Memulai pemindaian radar pasar...")
    cfg = Config()
    if state.agent_instance is None:
        state.agent_instance = GladiusAquila(cfg)
    
    cycle_res: CycleResult = state.agent_instance.run_cycle(force_report=force_report)
    
    # Format data untuk UI
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

    news_up = []
    for ev, imp in cycle_res.news.upcoming:
        news_up.append({
            "title": ev.title,
            "currency": ev.currency,
            "impact": ev.impact,
            "time_utc": ev.time_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "bias": imp.bias.value,
            "reason": imp.reason,
            "forecast": ev.forecast,
            "previous": ev.previous,
            "actual": ev.actual,
        })

    news_rel = []
    for ev, imp in cycle_res.news.released:
        news_rel.append({
            "title": ev.title,
            "currency": ev.currency,
            "impact": ev.impact,
            "time_utc": ev.time_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "bias": imp.bias.value,
            "reason": imp.reason,
            "forecast": ev.forecast,
            "previous": ev.previous,
            "actual": ev.actual,
        })

    news_nxt = []
    for ev in cycle_res.news.next_events:
        news_nxt.append({
            "title": ev.title,
            "currency": ev.currency,
            "impact": ev.impact,
            "time_utc": ev.time_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "forecast": ev.forecast,
            "previous": ev.previous,
            "actual": ev.actual,
        })

    # Gold proxy info
    gold_info = None
    try:
        gp = state.agent_instance._market.fetch_gold_proxy()
        if gp:
            gold_info = {
                "symbol": gp.symbol,
                "price": gp.price,
                "change_24h_pct": gp.change_24h_pct,
                "source": gp.source
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
        
        # Buat dialog interaktif berdasarkan hasil scan
        if signals_data:
            top_sig = sorted(signals_data, key=lambda x: x["score"], reverse=True)[0]
            state.dialogue = f"Boss! Terdeteksi target menarik: {top_sig['symbol']} ({top_sig['type']}) dengan skor {top_sig['score']}/100!"
            state.dialogue_type = "alert"
        elif news_up:
            ev_title = news_up[0]["title"]
            state.dialogue = f"Boss, waspada! Berita ekonomi High Impact XAUUSD mendekat: {ev_title}."
            state.dialogue_type = "alert"
        else:
            state.dialogue = f"Siklus #{cycle_res.cycle} selesai, Boss. Pasar relatif tenang, radar tetap memantau."
            state.dialogue_type = "report"
            
    log_event("SUCCESS", f"Siklus #{cycle_res.cycle} selesai: {len(signals_data)} sinyal, {len(news_up)} alert berita.")
    return state.latest_result

def patrol_worker():
    log_event("INFO", "Mode Patroli 24 Jam diaktifkan.")
    cfg = Config()
    while state.is_patrolling:
        try:
            run_agent_scan()
        except Exception as e:
            log_event("ERROR", f"Error pada siklus patroli: {e}")
        
        # Sleep per interval
        interval = max(30, cfg.scan_interval_seconds)
        for _ in range(int(interval)):
            if not state.is_patrolling:
                break
            time.sleep(1)
    log_event("INFO", "Mode Patroli dihentikan.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log_event("INFO", "Tactical AI Command Center Gladius Aquila diinisialisasi.")
    yield
    # Shutdown
    state.is_patrolling = False

app = FastAPI(title="Gladius Aquila Tactical Command Center", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Template index.html belum dibuat.")
    return FileResponse(index_file)

@app.get("/api/state")
async def get_state():
    with state.lock:
        cfg = Config()
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
    # Jalankan scan di thread terpisah agar tidak memblok event loop
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_agent_scan, True)
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
    cfg = Config()
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        raise HTTPException(status_code=400, detail="Telegram token/chat_id belum dikonfigurasi di .env")
    
    from notifications.telegram_bot import TelegramNotifier
    from character.persona import Persona
    notifier = TelegramNotifier(cfg, Persona(cfg))
    success = notifier.send_alert(f"🦅 <b>Laporan Uji Coba dari AI Command Center:</b>\nGladius Aquila terhubung dan siap mengawal Boss!")
    log_event("INFO" if success else "WARNING", "Mengirim uji coba notifikasi ke Telegram...")
    return {"status": "ok", "sent": success}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
