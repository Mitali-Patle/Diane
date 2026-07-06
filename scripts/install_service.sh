#!/usr/bin/env bash
# Install Diane as a systemd --user service (plus a user-level Ollama unit).
# Run once:  bash scripts/install_service.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OLLAMA_BIN="$HOME/.local/opt/ollama/bin/ollama"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/diane-ollama.service" <<EOF
[Unit]
Description=Ollama server for Diane (localhost:11434 only)

[Service]
ExecStart=$OLLAMA_BIN serve
Environment=OLLAMA_HOST=127.0.0.1:11434
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/diane.service" <<EOF
[Unit]
Description=Diane - local voice assistant
# graphical-session dependency needed once the avatar exists (M8, §21)
After=graphical-session.target diane-ollama.service
Wants=diane-ollama.service

[Service]
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/python -m jarvis.main
Restart=on-failure
RestartSec=3
# D-2: transcripts must not persist; stage logs go to the app log, not stdout.
StandardOutput=null
StandardError=journal

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable diane-ollama.service diane.service
echo "Installed. Start now with:  systemctl --user start diane-ollama diane"
echo "Logs: journalctl --user -u diane -f   (app log: $REPO/logs/app.log)"
