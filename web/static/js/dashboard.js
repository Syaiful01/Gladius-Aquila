// Gladius Aquila Tactical Dashboard Controller
document.addEventListener("DOMContentLoaded", () => {
  const statusBadge = document.getElementById("patrolStatusBadge");
  const cycleCountElem = document.getElementById("cycleCount");
  const lastScanElem = document.getElementById("lastScanTime");
  const speechText = document.getElementById("speechText");
  const signalsContainer = document.getElementById("signalsContainer");
  const goldEventsContainer = document.getElementById("goldEventsContainer");
  const terminalLogs = document.getElementById("terminalLogs");
  const goldPriceElem = document.getElementById("goldPrice");
  
  const btnScanNow = document.getElementById("btnScanNow");
  const btnTogglePatrol = document.getElementById("btnTogglePatrol");
  const btnTestTelegram = document.getElementById("btnTestTelegram");
  const eagleGraphic = document.getElementById("eagleGraphic");
  
  let isScanning = false;

  // Render State
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

    // 2. Metrics & Info
    cycleCountElem.textContent = `#${data.cycle_count || 0}`;
    lastScanElem.textContent = data.last_scan_at ? data.last_scan_at.split(" ")[1] : "Belum ada";
    
    // 3. Dialogue Gladius
    if (data.dialogue && speechText.textContent !== data.dialogue) {
      typeWriter(speechText, data.dialogue);
      
      // Visual pulse pada avatar
      if (data.dialogue_type === "alert") {
        eagleGraphic.style.borderColor = "var(--crimson-neon)";
        eagleGraphic.style.boxShadow = "0 0 35px rgba(239, 68, 68, 0.6)";
      } else {
        eagleGraphic.style.borderColor = "var(--cyan-neon)";
        eagleGraphic.style.boxShadow = "0 0 25px rgba(0, 240, 255, 0.35)";
      }
    }

    // 4. Gold Proxy Price
    if (data.latest_result?.gold_proxy) {
      const gp = data.latest_result.gold_proxy;
      goldPriceElem.textContent = `$${gp.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}`;
    }

    // 5. Signals
    const signals = data.latest_result?.signals || [];
    if (signals.length === 0) {
      signalsContainer.innerHTML = `<div class="empty-state">Belum ada sinyal terdeteksi pada radar. Tekan 'Pindai Radar Sekarang'.</div>`;
    } else {
      signalsContainer.innerHTML = signals.map(s => {
        let tagClass = "tag-volume";
        if (s.type === "momentum") tagClass = "tag-momentum";
        if (s.type === "exhaustion") tagClass = "tag-exhaustion";
        
        return `
          <div class="signal-card">
            <div class="signal-top">
              <div class="signal-symbol">${s.symbol}</div>
              <span class="signal-score">SKOR: ${s.score}</span>
            </div>
            <div>
              <span class="signal-type-tag ${tagClass}">${s.type}</span>
            </div>
            <div class="signal-stats">
              <div>Harga: <b>$${s.price < 1 ? s.price.toFixed(6) : s.price.toFixed(2)}</b></div>
              <div>24h: <b style="color: ${s.change_24h_pct >= 0 ? 'var(--emerald-neon)' : 'var(--crimson-neon)'}">${s.change_24h_pct > 0 ? '+' : ''}${s.change_24h_pct.toFixed(2)}%</b></div>
              <div>Vol 24h: <b>$${(s.volume_24h_usd / 1e6).toFixed(1)}M</b></div>
              <div>RSI(14): <b>${s.rsi ? s.rsi.toFixed(1) : '-'}</b></div>
            </div>
            <div class="signal-reasons">
              ${s.reasons.map(r => `• ${r}`).join("<br>")}
            </div>
          </div>
        `;
      }).join("");
    }

    // 6. Gold Events
    const events = [
      ...(data.latest_result?.news_upcoming || []),
      ...(data.latest_result?.news_next || [])
    ];
    if (events.length === 0) {
      goldEventsContainer.innerHTML = `<div class="empty-state">Tidak ada event ekonomi High Impact dalam waktu dekat.</div>`;
    } else {
      goldEventsContainer.innerHTML = events.map(ev => {
        const isHigh = ev.impact === "High";
        const bias = ev.bias || "NEUTRAL";
        let biasClass = "bias-neutral";
        if (bias === "BULLISH") biasClass = "bias-bullish";
        if (bias === "BEARISH") biasClass = "bias-bearish";

        return `
          <div class="event-item ${isHigh ? 'event-impact-high' : ''}">
            <div>
              <div class="event-title">${ev.title} (${ev.currency})</div>
              <div class="event-meta">Jadwal: ${ev.time_utc} | Forecast: ${ev.forecast || '-'} | Prev: ${ev.previous || '-'}</div>
            </div>
            <div>
              <span class="bias-badge ${biasClass}">${bias} GOLD</span>
            </div>
          </div>
        `;
      }).join("");
    }

    // 7. Logs
    const logs = data.logs || [];
    terminalLogs.innerHTML = logs.map(l => `
      <div class="log-line">
        <span class="log-time">[${l.time}]</span>
        <span class="log-level-${l.level}">${l.level}</span>
        <span class="log-msg">${escapeHtml(l.message)}</span>
      </div>
    `).join("");
    terminalLogs.scrollTop = terminalLogs.scrollHeight;
  }

  // Typewriter effect untuk dialog karakter
  let typeTimer = null;
  function typeWriter(element, text) {
    if (typeTimer) clearInterval(typeTimer);
    element.textContent = "";
    let i = 0;
    typeTimer = setInterval(() => {
      if (i < text.length) {
        element.textContent += text.charAt(i);
        i++;
      } else {
        clearInterval(typeTimer);
      }
    }, 20);
  }

  function escapeHtml(str) {
    return str.replace(/[&<>"']/g, m => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    })[m]);
  }

  // Fetch state berkala
  async function fetchState() {
    try {
      const res = await fetch("/api/state");
      if (res.ok) {
        const data = await res.json();
        updateUI(data);
      }
    } catch (err) {
      console.warn("Gagal mengambil state:", err);
    }
  }

  // Event Handlers
  btnScanNow.addEventListener("click", async () => {
    if (isScanning) return;
    isScanning = true;
    btnScanNow.disabled = true;
    btnScanNow.innerHTML = `<span>⏳</span> Memindai...`;
    speechText.textContent = "Radar aktif, sedang memindai pasar crypto & berita XAUUSD...";
    
    try {
      const res = await fetch("/api/scan", { method: "POST" });
      if (res.ok) {
        await fetchState();
      }
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
      if (res.ok) {
        await fetchState();
      }
    } catch (err) {
      alert("Gagal toggle patroli: " + err);
    }
  });

  btnTestTelegram.addEventListener("click", async () => {
    btnTestTelegram.disabled = true;
    btnTestTelegram.innerHTML = `<span>⏳</span> Mengirim...`;
    try {
      const res = await fetch("/api/test-telegram", { method: "POST" });
      const data = await res.json();
      if (data.sent) {
        alert("Laporan uji coba berhasil dikirim ke Telegram Boss!");
      } else {
        alert("Peringatan: Gagal mengirim pesan ke Telegram.");
      }
      await fetchState();
    } catch (err) {
      alert("Error tes telegram: " + err);
    } finally {
      btnTestTelegram.disabled = false;
      btnTestTelegram.innerHTML = `<span>📲</span> Tes Notifikasi Telegram`;
    }
  });

  // Initial Fetch & Interval Polling (tiap 3 detik)
  fetchState();
  setInterval(fetchState, 3000);
});
