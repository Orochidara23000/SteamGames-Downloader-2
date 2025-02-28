#!/bin/bash
set -euo pipefail

# Print diagnostic information
echo "Starting Steam Downloader container..."
echo "Running as user: $(id)"
echo "Working directory: $(pwd)"
echo "Steam download path: ${STEAM_DOWNLOAD_PATH:-Not set}"

# Ensure necessary directories exist and have proper permissions
mkdir -p /app/steamcmd /app/logs "${STEAM_DOWNLOAD_PATH}"
chmod 755 /app/steamcmd /app/logs "${STEAM_DOWNLOAD_PATH}"

# Ensure permissions are correct for the download directory
chown -R $(id -u):$(id -g) "${STEAM_DOWNLOAD_PATH}"
chmod -R 755 "${STEAM_DOWNLOAD_PATH}"

# If SteamCMD exists, make sure it's executable
if [ -f "/app/steamcmd/steamcmd.sh" ]; then
    chmod +x /app/steamcmd/steamcmd.sh
fi

# Run the diagnostic check first
echo "Running diagnostic checks..."
if ! python3 init_check.py; then
    echo "Diagnostic checks failed. Exiting."
    exit 1
fi

# Start the application and keep it running in the foreground
echo "Starting main application..."
exec python3 main.py