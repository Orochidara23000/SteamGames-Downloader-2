import gradio as gr
import os
import subprocess
import re
import requests
import zipfile
import tarfile
import platform
import time
import logging
import json
import threading
from datetime import datetime
import shutil
import sys

# Set up logging
logging.basicConfig(
    filename='steam_downloader.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Also log to console
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

# Global variables for download management
active_downloads = {}
download_queue = []

# Environment detection - check if running in Docker/Railway
def is_container():
    # Check for Docker
    if os.path.exists('/.dockerenv'):
        return True
    # Check for Railway specific env vars
    if os.environ.get('RAILWAY_ENVIRONMENT') is not None:
        return True
    return False

# Default download location - automatic based on platform
def get_default_download_location():
    # If in container environment, use a fixed path
    if is_container():
        return os.environ.get('STEAM_LIBRARY_PATH', '/app/steamlibrary')
    
    if platform.system() == "Windows":
        return os.path.join(os.path.expanduser("~"), "SteamLibrary")
    elif platform.system() == "Darwin":  # macOS
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SteamLibrary")
    else:  # Linux and others
        return os.path.join(os.path.expanduser("~"), "SteamLibrary")

# Function to get the correct SteamCMD path
def get_steamcmd_path():
    # If in container, check if steamcmd is in PATH
    if is_container():
        # Check common paths in containers
        container_paths = [
            "/usr/games/steamcmd",
            "/usr/bin/steamcmd",
            "/app/steamcmd/steamcmd.sh"
        ]
        
        # Try to use steamcmd from PATH first
        try:
            result = subprocess.run(["which", "steamcmd"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                logging.info(f"Found steamcmd in PATH: {result.stdout.strip()}")
                return result.stdout.strip()
        except:
            pass
            
        # Check common container paths
        for path in container_paths:
            if os.path.exists(path):
                logging.info(f"Found steamcmd at container path: {path}")
                return path
    
    # Regular paths for local installation
    if platform.system() == "Windows":
        return os.path.abspath("./steamcmd/steamcmd.exe")
    else:
        return os.path.abspath("./steamcmd/steamcmd.sh")

# Function to check if SteamCMD is installed
def check_steamcmd():
    steamcmd_path = get_steamcmd_path()
    is_installed = os.path.exists(steamcmd_path)
    
    if is_installed:
        logging.info(f"SteamCMD found at: {steamcmd_path}")
        # Verify it's executable
        if platform.system() != "Windows" and not os.access(steamcmd_path, os.X_OK):
            try:
                logging.info(f"Setting executable permission on {steamcmd_path}")
                os.chmod(steamcmd_path, 0o755)
                return f"SteamCMD found at {steamcmd_path}, fixed permissions."
            except Exception as e:
                logging.error(f"Failed to set executable permission: {str(e)}")
                return f"SteamCMD is installed at {steamcmd_path} but not executable. Error: {str(e)}"
        return f"SteamCMD is installed at {steamcmd_path}."
    
    # If in container, try to find system-wide SteamCMD
    if is_container():
        try:
            result = subprocess.run(["which", "steamcmd"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                logging.info(f"Found system SteamCMD at: {path}")
                return f"SteamCMD is installed at {path}"
        except:
            pass
    
    result = "SteamCMD is not installed."
    logging.info(result)
    return result

# Function to install SteamCMD
def install_steamcmd():
    os_type = platform.system()
    logging.info(f"Installing SteamCMD for {os_type}")
    
    # If in container, try to use package manager
    if is_container():
        try:
            logging.info("Trying to install SteamCMD via package manager")
            
            # Try apt (Debian/Ubuntu)
            try:
                logging.info("Attempting to install via apt-get")
                subprocess.run(["apt-get", "update"], check=True)
                subprocess.run(["apt-get", "install", "-y", "steamcmd"], check=True)
                
                # Check if it was installed
                result = subprocess.run(["which", "steamcmd"], capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    path = result.stdout.strip()
                    logging.info(f"Successfully installed SteamCMD via apt at: {path}")
                    return f"SteamCMD installed successfully at {path}"
            except Exception as e:
                logging.warning(f"apt-get installation failed: {str(e)}")
            
            # Try yum (CentOS/RHEL)
            try:
                logging.info("Attempting to install via yum")
                subprocess.run(["yum", "install", "-y", "steamcmd"], check=True)
                
                result = subprocess.run(["which", "steamcmd"], capture_output=True, text=True)
                if result.returncode == 0:
                    path = result.stdout.strip()
                    logging.info(f"Successfully installed SteamCMD via yum at: {path}")
                    return f"SteamCMD installed successfully at {path}"
            except Exception as e:
                logging.warning(f"yum installation failed: {str(e)}")
        
        except Exception as e:
            logging.warning(f"Package manager installation failed: {str(e)}")
            logging.info("Falling back to manual installation")
    
    try:
        # Clean up any existing installation
        if os.path.exists("./steamcmd"):
            shutil.rmtree("./steamcmd")
        
        os.makedirs("./steamcmd", exist_ok=True)
        
        if os_type == "Windows":
            url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
            logging.info(f"Downloading SteamCMD from {url}")
            response = requests.get(url, timeout=30)
            
            with open("steamcmd.zip", "wb") as f:
                f.write(response.content)
            
            logging.info("Extracting SteamCMD zip file")
            with zipfile.ZipFile("steamcmd.zip", "r") as zip_ref:
                zip_ref.extractall("./steamcmd")
            
            os.remove("steamcmd.zip")
            
        elif os_type == "Linux" or os_type == "Darwin":  # Linux or macOS
            url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
            logging.info(f"Downloading SteamCMD from {url}")
            response = requests.get(url, timeout=30)
            
            with open("steamcmd.tar.gz", "wb") as f:
                f.write(response.content)
            
            logging.info("Extracting SteamCMD tar.gz file")
            # Use separate commands for extraction to avoid potential issues
            try:
                if os_type == "Linux":
                    subprocess.run(["tar", "-xzf", "steamcmd.tar.gz", "-C", "./steamcmd"], check=True)
                else:
                    with tarfile.open("steamcmd.tar.gz", "r:gz") as tar:
                        tar.extractall(path="./steamcmd")
            except Exception as e:
                logging.error(f"Error extracting with tar: {str(e)}")
                logging.info("Trying alternative extraction method")
                # Alternative extraction method using subprocess
                subprocess.run(["mkdir", "-p", "./steamcmd"], check=True)
                subprocess.run(["tar", "-xzf", "steamcmd.tar.gz", "-C", "./steamcmd"], check=True)
            
            os.remove("steamcmd.tar.gz")
            
            # Make the script executable
            if os.path.exists("./steamcmd/steamcmd.sh"):
                os.chmod("./steamcmd/steamcmd.sh", 0o755)
                logging.info("Made steamcmd.sh executable")
            else:
                logging.error("steamcmd.sh not found after extraction")
                return "Error: steamcmd.sh not found after extraction"
        else:
            logging.error(f"Unsupported OS: {os_type}")
            return "Unsupported OS."
        
        # Run SteamCMD once to update itself
        logging.info("Running SteamCMD for the first time to complete installation")
        steamcmd_path = get_steamcmd_path()
        if os.path.exists(steamcmd_path):
            try:
                # Ensure it's executable
                if platform.system() != "Windows":
                    os.chmod(steamcmd_path, 0o755)
                
                subprocess.run([steamcmd_path, "+quit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logging.info("SteamCMD initial run completed")
                return f"SteamCMD installed successfully at {steamcmd_path}."
            except Exception as e:
                logging.error(f"Error running SteamCMD: {str(e)}")
                return f"Error running SteamCMD: {str(e)}"
        else:
            logging.error(f"SteamCMD executable not found at {steamcmd_path}")
            return f"Error: SteamCMD executable not found at {steamcmd_path}"
    except Exception as e:
        logging.error(f"Error installing SteamCMD: {str(e)}")
        return f"Error installing SteamCMD: {str(e)}"

# Function to parse game App ID from input
def parse_game_input(input_str):
    logging.info(f"Parsing game input: {input_str}")
    
    # Check if it's a direct App ID
    if input_str.isdigit():
        return input_str
    
    # Check if it's a Steam store URL
    url_patterns = [
        r'store\.steampowered\.com/app/(\d+)',  # Store URL
        r'steamcommunity\.com/app/(\d+)',       # Community URL
        r'/app/(\d+)'                          # General app pattern
    ]
    
    for pattern in url_patterns:
        match = re.search(pattern, input_str)
        if match:
            return match.group(1)
    
    logging.warning(f"Failed to parse game input: {input_str}")
    return None

# Function to validate App ID and get game information
def validate_appid(appid):
    logging.info(f"Validating App ID: {appid}")
    try:
        # Check if app exists via Steam API
        response = requests.get(f"https://store.steampowered.com/api/appdetails?appids={appid}", timeout=10)
        data = response.json()
        
        if data and data.get(appid, {}).get('success', False):
            game_data = data[appid]['data']
            game_info = {
                'name': game_data.get('name', 'Unknown Game'),
                'required_age': game_data.get('required_age', 0),
                'is_free': game_data.get('is_free', False),
                'developers': game_data.get('developers', ['Unknown']),
                'publishers': game_data.get('publishers', ['Unknown']),
                'platforms': game_data.get('platforms', {}),
                'categories': [cat.get('description') for cat in game_data.get('categories', [])],
                'size_mb': game_data.get('file_size', 'Unknown')
            }
            logging.info(f"Game found: {game_info['name']}")
            return True, game_info
        
        logging.warning(f"Game not found for App ID: {appid}")
        return False, "Game not found or API error"
    except Exception as e:
        logging.error(f"Validation error for App ID {appid}: {str(e)}")
        return False, f"Validation error: {str(e)}"

# Function to create system-specific paths with multiple fallbacks
def create_system_path(path):
    # Ensure absolute path
    abs_path = os.path.abspath(path)
    
    # Create directories
    try:
        os.makedirs(abs_path, exist_ok=True)
        return abs_path
    except PermissionError:
        # If permission error, try user home directory
        logging.warning(f"Permission denied for {abs_path}, trying user home")
        try:
            home_path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
            os.makedirs(home_path, exist_ok=True)
            return home_path
        except Exception as e:
            # Last resort - use temp directory
            logging.warning(f"Failed to use home directory: {str(e)}, using temp dir")
            temp_path = os.path.join(os.path.abspath(os.sep), "tmp", "SteamLibrary")
            os.makedirs(temp_path, exist_ok=True)
            return temp_path
    except Exception as e:
        logging.error(f"Error creating directory {abs_path}: {str(e)}")
        # Try temp directory as fallback
        temp_path = os.path.join(os.path.abspath(os.sep), "tmp", "SteamLibrary")
        os.makedirs(temp_path, exist_ok=True)
        return temp_path

# Remaining functions...
# [Rest of the code remains the same]

# Gradio interface
with gr.Blocks(title="Steam Games Downloader") as app:
    gr.Markdown("# Steam Games Downloader")
    gr.Markdown("Download Steam games using SteamCMD with a user-friendly interface.")
    
    # Environment info
    env_info = "Running in container" if is_container() else "Running locally"
    gr.Markdown(f"### Environment: {env_info}")
    
    # System check section
    with gr.Tab("Setup"):
        gr.Markdown("### SteamCMD Setup")
        with gr.Row():
            check_button = gr.Button("Check SteamCMD Installation")
            install_button = gr.Button("Install SteamCMD")
        
        setup_output = gr.Textbox(label="Status", lines=5)
        
        # Show the download location with environment variables support
        download_location = get_default_download_location()
        gr.Markdown(f"### Download Location\nGames will be automatically downloaded to: **{download_location}**")
        gr.Markdown("To change the download location when using Docker/Railway, set the `STEAM_LIBRARY_PATH` environment variable.")
    
    # Download section
    with gr.Tab("Download Games"):
        gr.Markdown("### Download Steam Games")
        gr.Markdown("Enter a Steam game ID or store URL to download a game.")
        
        with gr.Row():
            with gr.Column():
                with gr.Group():
                    gr.Markdown("#### Login Information")
                    anonymous = gr.Checkbox(label="Anonymous Login (for free games only)", value=True)
                    username = gr.Textbox(label="Steam Username", interactive=True)
                    password = gr.Textbox(label="Steam Password", type="password", interactive=True)
                    guard_code = gr.Textbox(label="Steam Guard Code (if required)", interactive=True)
                
                with gr.Group():
                    gr.Markdown("#### Game Information")
                    game_input = gr.Textbox(label="Game ID or URL (e.g., 570 or https://store.steampowered.com/app/570/)")
                    validate_download = gr.Checkbox(label="Verify After Download", value=True)
                
                with gr.Row():
                    download_button = gr.Button("Download Now", variant="primary")
                    queue_button = gr.Button("Add to Queue")
            
            download_output = gr.Textbox(label="Download Status", lines=15)
    
    # Library section
    with gr.Tab("Library"):
        gr.Markdown("### Game Library")
        with gr.Row():
            refresh_button = gr.Button("Refresh Library")
        
        library_output = gr.Textbox(label="Installed Games", lines=10)
    
    # Logs section - new
    with gr.Tab("Logs"):
        gr.Markdown("### Application Logs")
        log_output = gr.Textbox(label="Recent Logs", lines=15)
        refresh_log_button = gr.Button("Refresh Logs")
        
        # Function to get recent logs
        def get_recent_logs():
            try:
                if os.path.exists("steam_downloader.log"):
                    with open("steam_downloader.log", "r") as f:
                        # Get last 20 lines
                        lines = f.readlines()[-20:]
                        return "".join(lines)
                return "No log file found."
            except Exception as e:
                return f"Error reading log file: {str(e)}"
        
        refresh_log_button.click(get_recent_logs, outputs=log_output)
    
    # About section
    with gr.Tab("About"):
        gr.Markdown("""
        ## About Steam Games Downloader
        
        This application uses SteamCMD to download games from the Steam platform. It provides a user-friendly web interface built with Gradio.
        
        ### Features
        - Web-based interface for easy interaction
        - Automatic installation of SteamCMD
        - Support for both authenticated and anonymous downloads
        - Real-time download progress with estimated time remaining
        - Download queue for multiple games
        - Game installation verification
        
        ### Environment Support
        - Desktop: Windows, macOS, Linux
        - Container: Docker, Railway, and other cloud platforms
        
        ### System Requirements
        - Python 3.7+
        - Internet connection
        - Space for downloaded games
        
        ### Credits
        - SteamCMD by Valve Corporation
        - Built with Gradio
        
        ### License
        MIT License
        """)
    
    # Event handlers
    check_button.click(check_steamcmd, outputs=setup_output)
    install_button.click(install_steamcmd, outputs=setup_output)
    
    # Login logic to enable/disable fields based on anonymous checkbox
    def update_login_fields(anonymous):
        return [gr.update(interactive=not anonymous) for _ in range(3)]
    
    anonymous.change(update_login_fields, inputs=[anonymous], outputs=[username, password, guard_code])
    
    # Download buttons
    download_button.click(download_game, inputs=[username, password, guard_code, anonymous, game_input, validate_download], outputs=download_output)
    queue_button.click(queue_download, inputs=[username, password, guard_code, anonymous, game_input, validate_download], outputs=download_output)
    
    # Library buttons
    refresh_button.click(list_installed_games, outputs=library_output)

# Launch the app with environment-based settings
if __name__ == "__main__":
    # Get port from environment or use default
    port = int(os.environ.get("PORT", 7860))
    
    # Determine whether to use 0.0.0.0 (for containers) or 127.0.0.1 (local)
    host = "0.0.0.0" if is_container() else "127.0.0.1"
    
    # Log startup info
    logging.info(f"Starting Steam Downloader on {host}:{port}")
    logging.info(f"Environment: {'Container' if is_container() else 'Local'}")
    logging.info(f"Default download location: {get_default_download_location()}")
    logging.info(f"Python version: {sys.version}")
    logging.info(f"Platform: {platform.platform()}")
    
    # Launch app with appropriate settings
    app.launch(server_name=host, server_port=port)