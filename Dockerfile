FROM python:3.9-slim

# Install required system dependencies
RUN apt-get update && \
    apt-get install -y \
    lib32gcc-s1 \
    curl \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Make entrypoint script executable
RUN chmod +x entrypoint.sh

# Create directories for volumes
RUN mkdir -p /data/downloads /app/steamcmd /app/logs

# Create appcache directory, add appinfo.vdf, and set permissions
RUN mkdir -p /root/Steam/appcache && \
    echo "326360" > /root/Steam/appcache/appinfo.vdf && \
    chmod -R 777 /root/Steam

# New commands to set up SteamCMD
RUN mkdir -p /steamcmd && \
    chown -R nobody:nogroup /steamcmd && \
    ln -s /steamcmd /root/Steam && \
    chmod -R 777 /steamcmd

# Set environment variables
ENV STEAM_DOWNLOAD_PATH=/data/downloads
ENV LOG_LEVEL=INFO

# Run the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]