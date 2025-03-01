FROM python:3.9-slim

# Install required system dependencies
RUN apt-get update && \
    apt-get install -y wget tar gzip && \
    mkdir -p /steam/downloads /home/steamuser && \
    chown -R 1000:1000 /steam && \
    ln -s /steam /home/steamuser/steam && \
    wget https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux_old.tar.gz && \
    tar -xvzf steamcmd_linux_old.tar.gz -C /steam && \
    rm steamcmd_linux_old.tar.gz && \
    apt-get purge -y wget && apt-get autoremove -y

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
RUN mkdir -p /data/downloads /app/logs

# Set environment variables
ENV STEAM_DOWNLOAD_PATH=/data/downloads
ENV LOG_LEVEL=INFO

# Run the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]