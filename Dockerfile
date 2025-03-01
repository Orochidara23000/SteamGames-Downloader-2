# Use official Alpine base image
FROM alpine:3.18

# Install required system dependencies with community repository
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/community" >> /etc/apk/repositories && \
    apk update && \
    apk add --no-cache \
        wget \
        tar \
        gzip \
        libgcc \
        libstdc++ \
        ca-certificates && \
    # Create directory structure
    mkdir -p /steam/downloads && \
    mkdir -p /home/steamuser && \
    # Set ownership before symlink
    chown -R 1000:1000 /steam && \
    chown -R 1000:1000 /home/steamuser && \
    # Create symbolic link
    ln -s /steam /home/steamuser/steam && \
    # Download and extract SteamCMD
    wget -O /tmp/steamcmd.tar.gz https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz && \
    tar -xvzf /tmp/steamcmd.tar.gz -C /steam && \
    # Cleanup
    rm /tmp/steamcmd.tar.gz && \
    # Fix permissions
    chmod -R 755 /steam

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