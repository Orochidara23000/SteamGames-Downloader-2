#!/bin/bash
set -e

# Print diagnostic information
echo "Starting Steam Downloader container..."
echo "Running as user: $(id)"
echo "Working directory: $(pwd)"
echo "Steam download path: ${STEAM_DOWNLOAD_PATH}"

# Ensure SteamCMD directory exists and has proper permissions
mkdir -p /app/steamcmd
chmod 755 /app/steamcmd

# Ensure downloads directory exists
mkdir -p ${STEAM_DOWNLOAD_PATH}
chmod 755 ${STEAM_DOWNLOAD_PATH}  # Ensure the download path is writable

# Ensure logs directory exists
mkdir -p /app/logs
chmod 755 /app/logs

# If SteamCMD exists, make sure it's executable
if [ -f "/app/steamcmd/steamcmd.sh" ]; then
    chmod +x /app/steamcmd/steamcmd.sh
fi

# Run the diagnostic check first
echo "Running diagnostic checks..."
python init_check.py

# Start the application and keep it running in the foreground
echo "Starting main application..."
exec python main.py