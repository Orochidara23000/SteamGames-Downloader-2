# Steam Games Downloader

This project is a Steam games downloader that uses [SteamCMD](https://developer.valvesoftware.com/wiki/SteamCMD) and provides a user-friendly interface via [Gradio](https://gradio.app/). It allows users to download Steam games by providing the game ID or URL, with support for login verification (including Steam Guard) and real-time progress tracking.

## Features

- Web-based interface for easy interaction
- Automatic installation of SteamCMD if not already installed
- Support for both authenticated and anonymous downloads (for free games)
- Real-time download progress with estimated time remaining and file size tracking
- Download queue for multiple games
- Game installation verification
- Library management to view installed games
- Cross-platform support (Windows, Linux, macOS)
- Docker container support for easy deployment
- Detailed logging for troubleshooting
- Automatic download location management

## Standard Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/steam-downloader.git
   cd steam-downloader
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application**:
   ```bash
   python main.py
   ```

4. Open your browser and navigate to `http://127.0.0.1:7860` to access the interface.

## Docker Installation

For easier deployment and to avoid dependency issues, you can use Docker:

1. **Build and run with Docker Compose**:
   ```bash
   docker-compose up -d
   ```

2. Open your browser and navigate to `http://127.0.0.1:7860` to access the interface.

3. **Stop the container**:
   ```bash
   docker-compose down
   ```

### Custom Docker Configuration

You can customize the Docker setup by modifying the `docker-compose.yml` file:

- Change the port mapping (default is 7860)
- Change the download location by modifying the `STEAM_DOWNLOAD_PATH` environment variable
- Add custom volumes for persistent storage

## Usage

### Setting up SteamCMD

1. Go to the "Setup" tab
2. Click "Check SteamCMD Installation" to see if SteamCMD is already installed
3. If not installed, click "Install SteamCMD" to automatically download and set it up
4. If running in a container, you can also check container dependencies

### Downloading Games

1. Go to the "Download Games" tab
2. For free games, keep "Anonymous Login" checked
3. For paid games, uncheck "Anonymous Login" and enter your Steam credentials
4. Enter a game ID or Steam store URL in the "Game ID or URL" field
5. Click "Download Now" to start downloading immediately, or "Add to Queue" to queue the download

### Download Locations

Games are automatically saved to:
- **Standard installation**: Platform-specific location
  - Windows: ~/SteamLibrary
  - macOS: ~/Library/Application Support/SteamLibrary
  - Linux: ~/SteamLibrary
- **Docker installation**: /data/downloads (mapped to the steam-downloads volume)

### Managing Your Library

1. Go to the "Library" tab
2. Click "Refresh Library" to view installed games and their sizes

## Security Notes

- Your Steam credentials are not stored and are only used for the current session
- For security reasons, avoid using your main Steam account password and consider creating a separate account for downloads
- Steam Guard codes are used once and not stored

## Troubleshooting

- Check the `steam_downloader.log` file for detailed information if you encounter any issues
- Ensure you have sufficient disk space for game downloads
- For connection issues, verify your internet connection and firewall settings
- If SteamCMD fails to install, try installing it manually following [official instructions](https://developer.valvesoftware.com/wiki/SteamCMD#Downloading_SteamCMD)

### Container-specific Issues

- **Permission errors**: Ensure the container has write permissions to the volumes
- **Missing dependencies**: Use the "Check Container Dependencies" button in the Setup tab
- **Path resolution problems**: Check the paths in the docker-compose.yml file

## License

MIT License - See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.