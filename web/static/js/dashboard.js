// Gladius Aquila Tactical Dashboard Controller — v2.0
// Fase 4: WebSocket live push + History + Futures

document.addEventListener("DOMContentLoaded", () => {
  // ─── Element refs ───────────────────────────────────────────────────────
  const statusBadge       = document.getElementById("patrolStatusBadge");
  const cycleCountElem    = document.getElementById("cycleCount");
  const lastScanElem      = document.getElementById("lastScanTime");
  const speechText        = document.getElementById("speechText");
  const signalsContainer  = document.getElementById("signalsContainer");
  const goldEventsContainer = document.getElementById("goldEventsContainer");
  const terminalLogs      = document.getElementById("terminalLogs");
  const goldPriceElem     = document.getElementById("goldPrice");
  const signal24hElem     = document.getElementById("signal24hCount");
  const wsIndicator       = document.getElementById("wsIndicator");
  const signalTypeFilter  = document.getElementById("signalTypeFilter");

  const btnScanNow        = document.getElementById("btnScanNow");
  const btnTogglePatrol   = document.getElementById("btnTogglePatrol");
  const btnTestTelegram   = document.getElementById("btnTestTelegram");
  const eagleGraphic      = document.getElementById("eagleGraphic");

  // History tab
  const btnLoadHistory    = document.getElementById("btnLoadHistory");
  const historyHours      = document.getElementById("historyHours");
  const historyTypeFilter = document.getElementById("historyTypeFilter");
  const historyContainer  = document.getElementById("historyTableContainer");
  const historyCount      = document.getElementById("historyCount");
  const historyStats      = document.getElementById("historyStats");

  // Futures tab
  const btnLoadFutures    = document.getElementById("btnLoadFutures");
  const futuresTopN       = document.getElementById("futuresTopN");
  const futuresContainer  = document.getElementById("futuresContainer");

  let isScanning  = false;
  let lastSignals = [];

  // ─── Tab navigation ─────────────────────────────────────────────────────
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${target}`).classList.add("active");
    });
  });

  // ─── Filter sinyal di dashboard ─────────────────────────────────────────
  signalTypeFilter.addEventListener("change", () => {
    renderSignals(lastSignals);
  });

  // ─── WebSocket ──────────────────────────────────────────────────────────
  let ws = null;
  let wsFallbackInterval = null;
  let wsRetryCount = 0;

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
      wsIndicator.className = "ws-indicator online";
      wsIndicator.title = "WebSocket Terhubung";
      wsRetryCount = 0;
      // Hentikan fallback polling bila WS berhasil
      if (wsFallbackInterval) {
        clearInterval(wsFallbackInterval);
        wsFallbackInterval = null;
      }
      // Keep-alive ping tiap 25 detik
      ws._pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 25000);
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data && typeof data === "object" && "cycle_count" in data) {
          updateUI(data);
        }
      } catch (_) {}
    };

    ws.onclose = () => {
      wsIndicator.className = "ws-indicator offline";
      wsIndicator.title = "WebSocket Terputus";
      clearInterval(ws._pingTimer);
      // Fallback ke polling bila WS gagal/putus
      if (!wsFallbackInterval) {
        wsFallbackInterval = setInterval(fetchState, 3000);
      }
      // Retry koneksi dengan backoff
      const delay = Math.min(30000, 2000 * Math.pow(1.5, wsRetryCount++));
      setTimeout(connectWS, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  // ─── Fallback polling ───────────────────────────────────────────────────
  async function fetchState() {
    try {
      const res = await fetch("/api/state");
      if (res.ok) updateUI(await res.json());
    } catch (err) {
      console.warn("Gagal ambil state:", err);
    }
  }

  // ─── Render utama ────────────────────────────────────────────────────────
  function updateUI(data) {
    // 1. Status Patroli
    if (data.is_patrolling) {
      statusBadge.className = "status-badge active";
      statusBadge.innerHTML = `<span class="pulse-dot"></span> PATROLI AKTIF (24J)`;
      btnTogglePatrol.innerHTML = `<span>🛑</span> Hentikan Patroli`;
      btnTogglePatrol.style.borderColor = "var(--crimson-neon)";
      btnTogglePatrol.style.color = "var(--crimson-neon)";
    } else {
      statusBadge.className = "status-badge standby";
      statusBadge.innerHTML = `<span class="pulse-dot"></span> STANDBY`;
      btnTogglePatrol.innerHTML = `<span>🛡️</span> Aktifkan Patroli 24 Jam`;
      btnTogglePatrol.style.borderColor = "var(--emerald-neon)";
      btnTogglePatrol.style.color = "var(--emerald-neon)";
    }

    // 2. Metrics
    cycleCountElem.textContent = `#${data.cycle_count || 0}`;
    lastScanElem.textContent = data.last_scan_at ? data.last_scan_at.split(" ")[1] : "Belum ada";

    // 3. Dialogue Gladius
    if (data.dialogue && speechText.textContent !== data.dialogue) {
      typeWriter(speechText, data.dialogue);
      if (data.dialogue_type === "alert") {
        eagleGraphic.style.borderColor = "var(--crimson-neon)";
        eagleGraphic.style.boxShadow = "0 0 35px rgba(239, 68, 68, 0.6)";
      } else {
        eagleGraphic.style.borderColor = "var(--cyan-neon)";
        eagleGraphic.style.boxShadow = "0 0 25px rgba(0, 240, 255, 0.35)";
      }
    }

    // 4. Gold Price
    if (data.latest_result?.gold_proxy) {
      const gp = data.latest_result.gold_proxy;
      goldPriceElem.textContent = `$${gp.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}`;
    }

    // 5. Signals (dengan filter)
    lastSignals = data.latest_result?.signals || [];
    renderSignals(lastSignals);

    // 6. Gold Events
    const events = [
      ...(data.latest_result?.news_upcoming || []),
      ...(data.latest_result?.news_next || [])
    ];
    renderEvents(events);

    // 7. Terminal Logs
    const logs = data.logs || [];
    terminalLogs.innerHTML = logs.map(l => `
      <div class="log-line">
        <span class="log-time">[${l.time}]</span>
        <span class="log-level-${l.level}">${l.level}</span>
        <span class="log-msg">${escapeHtml(l.message)}</span>
      </div>
    `).join("");
    terminalLogs.scrollTop = terminalLogs.scrollHeight;

    // 8. Ambil stat 24h untuk metric
    fetch("/api/history/stats").then(r => r.ok ? r.json() : null).then(d => {
      if (!d) return;
      const total = Object.values(d.stats?.last_24h_by_type || {}).reduce((a, b) => a + b, 0);
      signal24hElem.textContent = total;
    }).catch(() => {});
  }

  // ─── Render sinyal dengan filter ─────────────────────────────────────────
  function renderSignals(signals) {
    const filterVal = signalTypeFilter.value;
    const filtered  = filterVal ? signals.filter(s => s.type === filterVal) : signals;

    if (filtered.length === 0) {
      signalsContainer.innerHTML = `<div class="empty-state">Belum ada sinyal terdeteksi pada radar. Tekan 'Pindai Radar Sekarang'.</div>`;
      return;
    }

    signalsContainer.innerHTML = filtered.map(s => {
      const typeMap = {
        "HIGH_VOLUME": { cls: "tag-volume", label: "High Volume" },
        "STRONG_MOMENTUM": { cls: "tag-momentum", label: "Momentum Kuat" },
        "EXHAUSTION": { cls: "tag-exhaustion", label: "Exhaustion" },
      };
      const t = typeMap[s.type] || { cls: "tag-volume", label: s.type };
      const changeColor = s.change_24h_pct >= 0 ? "var(--emerald-neon)" : "var(--crimson-neon)";

      return `
        <div class="signal-card">
          <div class="signal-top">
            <div class="signal-symbol">${escapeHtml(s.symbol)}</div>
            <span class="signal-score">SKOR: ${s.score}</span>
          </div>
          <div>
            <span class="signal-type-tag ${t.cls}">${t.label}</span>
          </div>
          <div class="signal-stats">
            <div>Harga: <b>$${s.price < 1 ? s.price.toFixed(6) : s.price.toFixed(2)}</b></div>
            <div>24h: <b style="color:${changeColor}">${s.change_24h_pct > 0 ? '+' : ''}${s.change_24h_pct.toFixed(2)}%</b></div>
            <div>Vol 24h: <b>$${(s.volume_24h_usd / 1e6).toFixed(1)}M</b></div>
            <div>RSI(14): <b>${s.rsi ? s.rsi.toFixed(1) : '-'}</b></div>
          </div>
          <div class="signal-reasons">
            ${s.reasons.map(r => `• ${escapeHtml(r)}`).join("<br>")}
          </div>
        </div>
      `;
    }).join("");
  }

  // ─── Render gold events ─────────────────────────────────────────────────
  function renderEvents(events) {
    if (events.length === 0) {
      goldEventsContainer.innerHTML = `<div class="empty-state">Tidak ada event ekonomi High Impact dalam waktu dekat.</div>`;
      return;
    }
    goldEventsContainer.innerHTML = events.map(ev => {
      const isHigh = ev.impact === "High";
      const bias   = ev.bias || "NEUTRAL";
      const biasClass = bias === "BULLISH" ? "bias-bullish" : bias === "BEARISH" ? "bias-bearish" : "bias-neutral";
      return `
        <div class="event-item ${isHigh ? 'event-impact-high' : ''}">
          <div>
            <div class="event-title">${escapeHtml(ev.title)} (${escapeHtml(ev.currency)})</div>
            <div class="event-meta">Jadwal: ${escapeHtml(ev.time_utc)} | Forecast: ${ev.forecast || '-'} | Prev: ${ev.previous || '-'}</div>
          </div>
          <div>
            <span class="bias-badge ${biasClass}">${bias} GOLD</span>
          </div>
        </div>
      `;
    }).join("");
  }

  // ─── Riwayat Sinyal (Fase 2) ─────────────────────────────────────────────
  btnLoadHistory.addEventListener("click", loadHistory);

  async function loadHistory() {
    const hours    = historyHours.value;
    const typeVal  = historyTypeFilter.value;
    historyContainer.innerHTML = `<div class="empty-state">⏳ Memuat riwayat...</div>`;
    historyCount.textContent   = "";

    try {
      let url = `/api/history?hours=${hours}&limit=200`;
      if (typeVal) url += `&signal_type=${typeVal}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Gagal ambil riwayat");
      const data = await res.json();
      renderHistoryTable(data.signals);
      historyCount.textContent = `${data.count} sinyal`;

      // Stats
      const statsRes = await fetch("/api/history/stats");
      if (statsRes.ok) {
        const sd = await statsRes.json();
        renderHistoryStats(sd.stats);
      }
    } catch (err) {
      historyContainer.innerHTML = `<div class="empty-state" style="color:var(--crimson-neon)">Error: ${err.message}</div>`;
    }
  }

  function renderHistoryStats(stats) {
    if (!stats) return;
    const byType = stats.last_24h_by_type || {};
    const total  = Object.values(byType).reduce((a, b) => a + b, 0);
    const topSym = (stats.top_symbols_24h || []).slice(0, 3)
      .map(s => `<span class="stat-tag">${escapeHtml(s.symbol)} (${s.cnt}x)</span>`).join(" ");

    historyStats.innerHTML = `
      <div class="history-stats-grid">
        <div class="stat-card">
          <div class="stat-label">Total 24j</div>
          <div class="stat-value">${total}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Volume</div>
          <div class="stat-value">${byType["HIGH_VOLUME"] || 0}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Momentum</div>
          <div class="stat-value">${byType["STRONG_MOMENTUM"] || 0}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Exhaustion</div>
          <div class="stat-value">${byType["EXHAUSTION"] || 0}</div>
        </div>
        <div class="stat-card stat-wide">
          <div class="stat-label">Top Simbol 24j</div>
          <div style="margin-top:0.25rem;">${topSym || "—"}</div>
        </div>
      </div>
    `;
  }

  function renderHistoryTable(signals) {
    if (!signals || signals.length === 0) {
      historyContainer.innerHTML = `<div class="empty-state">Tidak ada sinyal ditemukan untuk filter yang dipilih.</div>`;
      return;
    }

    const rows = signals.map(s => {
      const typeMap = {
        "HIGH_VOLUME": { cls: "tag-volume", label: "High Volume" },
        "STRONG_MOMENTUM": { cls: "tag-momentum", label: "Momentum" },
        "EXHAUSTION": { cls: "tag-exhaustion", label: "Exhaustion" },
      };
      const t = typeMap[s.signal_type] || { cls: "tag-volume", label: s.signal_type };
      const changeColor = s.change_24h_pct >= 0 ? "var(--emerald-neon)" : "var(--crimson-neon)";
      const localTime = new Date(s.created_at).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", hour12: false });

      return `
        <tr>
          <td><code>${escapeHtml(s.symbol)}</code></td>
          <td><span class="signal-type-tag ${t.cls}">${t.label}</span></td>
          <td><b>$${s.price < 1 ? s.price.toFixed(6) : s.price.toFixed(2)}</b></td>
          <td style="color:${changeColor}">${s.change_24h_pct > 0 ? '+' : ''}${s.change_24h_pct.toFixed(2)}%</td>
          <td>$${(s.volume_24h_usd / 1e6).toFixed(1)}M</td>
          <td>${s.rsi ? s.rsi.toFixed(1) : '—'}</td>
          <td><span class="signal-score" style="font-size:0.75rem">${s.score}</span></td>
          <td style="font-size:0.75rem;color:var(--text-dim)">${escapeHtml(localTime)}</td>
        </tr>
      `;
    }).join("");

    historyContainer.innerHTML = `
      <div class="history-table-wrapper">
        <table class="history-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Tipe</th><th>Harga</th>
              <th>24h %</th><th>Volume</th><th>RSI</th><th>Skor</th><th>Waktu (WIB)</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  // ─── Funding Rate (Fase 3) ───────────────────────────────────────────────
  btnLoadFutures.addEventListener("click", loadFutures);

  async function loadFutures() {
    const topN = futuresTopN.value;
    futuresContainer.innerHTML = `<div class="empty-state">⏳ Mengambil data funding rate dari exchange...</div>`;

    try {
      const res = await fetch(`/api/futures?top_n=${topN}`);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Gagal ambil futures");
      }
      const data = await res.json();
      renderFutures(data.data);
    } catch (err) {
      futuresContainer.innerHTML = `<div class="empty-state" style="color:var(--crimson-neon)">Error: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderFutures(items) {
    if (!items || items.length === 0) {
      futuresContainer.innerHTML = `<div class="empty-state">Tidak ada data futures tersedia.</div>`;
      return;
    }

    futuresContainer.innerHTML = `
      <div class="futures-grid">
        ${items.map(f => {
          const frColor = f.funding_rate_pct > 0.05
            ? "var(--crimson-neon)"
            : f.funding_rate_pct < -0.01
              ? "var(--emerald-neon)"
              : "var(--text-muted)";
          const frSign = f.funding_rate_pct > 0 ? "+" : "";
          const barWidth = Math.min(100, Math.abs(f.funding_rate_pct) / 0.1 * 100);
          const barColor = f.funding_rate_pct > 0 ? "var(--crimson-neon)" : "var(--emerald-neon)";

          return `
            <div class="futures-card">
              <div class="futures-top">
                <div class="signal-symbol">${escapeHtml(f.base)}</div>
                <span class="futures-fr" style="color:${frColor}">${frSign}${f.funding_rate_pct.toFixed(4)}%</span>
              </div>
              <div class="fr-bar-wrap">
                <div class="fr-bar" style="width:${barWidth}%;background:${barColor};${f.funding_rate_pct < 0 ? 'margin-left:auto;' : ''}"></div>
              </div>
              <div class="futures-stats">
                <div>OI (Est.): <b>$${(f.open_interest_usd / 1e6).toFixed(0)}M</b></div>
                <div>Annual: <b>${frSign}${f.funding_rate_annualized_pct.toFixed(1)}%</b></div>
              </div>
              <div class="futures-sentiment">${escapeHtml(f.sentiment)}</div>
              <div style="font-size:0.7rem;color:var(--text-dim);margin-top:0.25rem;">via ${f.source}</div>
            </div>
          `;
        }).join("")}
      </div>
    `;
  }

  // ─── Typewriter effect ───────────────────────────────────────────────────
  let typeTimer = null;
  function typeWriter(element, text) {
    if (typeTimer) clearInterval(typeTimer);
    element.textContent = "";
    let i = 0;
    typeTimer = setInterval(() => {
      if (i < text.length) { element.textContent += text.charAt(i); i++; }
      else clearInterval(typeTimer);
    }, 20);
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, m => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    })[m]);
  }

  // ─── Button handlers ────────────────────────────────────────────────────
  btnScanNow.addEventListener("click", async () => {
    if (isScanning) return;
    isScanning = true;
    btnScanNow.disabled = true;
    btnScanNow.innerHTML = `<span>⏳</span> Memindai...`;
    speechText.textContent = "Radar aktif, sedang memindai pasar crypto & berita XAUUSD...";

    try {
      await fetch("/api/scan", { method: "POST" });
      // State akan datang via WebSocket; fallback via polling manual
      await fetchState();
    } catch (err) {
      alert("Gagal melakukan scan: " + err);
    } finally {
      isScanning = false;
      btnScanNow.disabled = false;
      btnScanNow.innerHTML = `<span>🚀</span> Pindai Radar Sekarang`;
    }
  });

  btnTogglePatrol.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/toggle-patrol", { method: "POST" });
      if (res.ok) await fetchState();
    } catch (err) {
      alert("Gagal toggle patroli: " + err);
    }
  });

  btnTestTelegram.addEventListener("click", async () => {
    btnTestTelegram.disabled = true;
    btnTestTelegram.innerHTML = `<span>⏳</span> Mengirim...`;
    try {
      const res  = await fetch("/api/test-telegram", { method: "POST" });
      const data = await res.json();
      alert(data.sent
        ? "Laporan uji coba berhasil dikirim ke Telegram Boss!"
        : "Peringatan: Gagal mengirim pesan ke Telegram.");
      await fetchState();
    } catch (err) {
      alert("Error tes telegram: " + err);
    } finally {
      btnTestTelegram.disabled = false;
      btnTestTelegram.innerHTML = `<span>📲</span> Tes Notifikasi Telegram`;
    }
  });

  // ─── Init ────────────────────────────────────────────────────────────────
  connectWS();
  // Polling fallback langsung sebagai safety net awal
  fetchState();
  wsFallbackInterval = setInterval(fetchState, 3000);
});
