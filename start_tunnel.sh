#!/bin/bash

# Configuration
PORT=5000
SERVER="serveo.net"

print_header() {
    echo "================================================="
    echo "   🚀  Persistent Tunnel Manager "
    echo "================================================="
    echo "Target: localhost:$PORT -> $SERVER"
    echo "Press Ctrl+C to stop"
    echo "================================================="
}

start_tunnel() {
    # -R 80:localhost:5000 : Forward remote port 80 to local port 5000
    # -o ServerAliveInterval=60 : Send heartbeat every 60s to prevent timeout
    # -o StrictHostKeyChecking=no : Don't ask for fingerprint confirmation
    ssh -o ServerAliveInterval=60 -o StrictHostKeyChecking=no -R 80:localhost:$PORT $SERVER
}

print_header

while true; do
    echo "[$(date)] Starting tunnel..."
    start_tunnel
    
    echo "[$(date)] Tunnel disconnected. Restarting in 3 seconds..."
    sleep 3
done
