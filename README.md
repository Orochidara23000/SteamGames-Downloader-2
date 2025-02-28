# Steam Games Downloader

This project is a Steam games downloader that uses [SteamCMD](https://developer.valvesoftware.com/wiki/SteamCMD) and provides a user-friendly interface via [Gradio](https://gradio.app/). It allows users to download Steam games by providing the game ID or URL, with support for login verification (including Steam Guard) and real-time progress tracking.

## Features
- Web-based interface for easy interaction.
- Automatic installation of SteamCMD if not already installed.
- Support for both authenticated and anonymous downloads (for free games).
- Real-time download progress with estimated time remaining and file size tracking.

## Installation
1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/steam-downloader.git
   cd steam-downloader