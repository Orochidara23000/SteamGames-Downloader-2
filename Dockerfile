FROM python:3.9-slim

# Install required system dependencies and locales
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y \
    lib32gcc1 \
    lib32stdc++6 \
    lib32gcc-s1 \
    curl \
    libcurl4 \
    locales && \
    locale-gen en_US.UTF-8 && \
    rm -rf /var/lib/apt/lists/*

# Set the locale environment variables
ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

# Set working directory
WORKDIR /app

# Create directories for volumes and set permissions
RUN mkdir -p /data/downloads && \
    chmod 777 /data/downloads  # Temporary wide permissions for testing

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Make entrypoint script executable
RUN chmod +x entrypoint.sh

# Create additional directories for SteamCMD and logs
RUN mkdir -p /app/steamcmd /app/logs

# Set environment variables
ENV STEAM_DOWNLOAD_PATH=/data/downloads
ENV LOG_LEVEL=INFO

# Run the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]