FROM python:3.9-slim

# Install required system dependencies
RUN apt-get update && \
    apt-get install -y \
    lib32gcc-s1 \
    curl \
    tar \
    ca-certificates \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Set locale to UTF-8
RUN localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8
ENV LANG en_US.utf8

# Install SteamCMD
WORKDIR /app
RUN mkdir -p /app/steamcmd && \
    curl -sqL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" | tar -xvzf - -C /app/steamcmd && \
    chmod +x /app/steamcmd/steamcmd.sh && \
    /app/steamcmd/steamcmd.sh +quit

# Set steamcmd in path
ENV PATH="/app/steamcmd:${PATH}"

# Create directory for Steam games
RUN mkdir -p /app/steamlibrary
ENV STEAM_LIBRARY_PATH=/app/steamlibrary

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the port
ENV PORT=7860
EXPOSE 7860

# Start the application
CMD ["python", "main.py"]
