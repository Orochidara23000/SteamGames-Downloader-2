#!/bin/bash
set -e

# Ensure SteamCMD directory exists and has proper permissions
mkdir -p /app/steamcmd
chmod 755 /app/steamcmd

# Ensure downloads directory exists
mkdir -p ${STEAM_DOWNLOAD_PATH}

# If SteamCMD exists, make sure it's executable
if [ -f "/app/steamcmd/steamcmd.sh" ]; then
    chmod +x /app/steamcmd/steamcmd.sh
fi

# Run the Python application
exec python main.py 