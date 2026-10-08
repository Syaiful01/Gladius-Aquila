"""Titik masuk untuk menjalankan Gladius Aquila AI Command Center Web UI."""
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import uvicorn

if __name__ == "__main__":
    port = 8888
    print("=" * 60)
    print(f"🦅 Gladius Aquila — AI Tactical Command Center")
    print(f"Akses Dashboard di browser: http://localhost:{port}")
    print("Tekan Ctrl+C untuk berhenti.")
    print("=" * 60)
    uvicorn.run("web.app:app", host="127.0.0.1", port=port, reload=False)

