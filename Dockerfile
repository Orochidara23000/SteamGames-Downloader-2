# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set work directory
WORKDIR /app

# Install system dependencies required for SteamCMD
RUN apt-get update && apt-get install -y \
    lib32gcc-s1 \
    curl \
    tar \
    unzip \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for SteamCMD and downloaded games
RUN mkdir -p /app/steamcmd /app/downloads

# Set permission for the entrypoint script
RUN chmod +x /app/entrypoint.sh

# Define environment variable for download path
ENV STEAM_DOWNLOAD_PATH=/app/downloads

# Expose the port Gradio will run on
EXPOSE 7860

# Run the application with the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]