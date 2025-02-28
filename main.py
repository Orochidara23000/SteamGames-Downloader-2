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

# Set up logging
logging.basicConfig(
    filename='steam_downloader.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Global variables for download management
active_downloads = {}
download_queue = []

# Default download location - automatic based on platform
def get_default_download_location():
    if platform.system() == "Windows":
        return os.path.join(os.path.expanduser("~"), "SteamLibrary")
    elif platform.system() == "Darwin":  # macOS
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SteamLibrary")
    else:  # Linux and others
        return os.path.join(os.path.expanduser("~"), "SteamLibrary")

# Function to get the correct SteamCMD path
def get_steamcmd_path():
    if platform.system() == "Windows":
        return "./steamcmd/steamcmd.exe"
    else:
        return "./steamcmd/steamcmd.sh"

# Function to check if SteamCMD is installed
def check_steamcmd():
    steamcmd_path = get_steamcmd_path()
    is_installed = os.path.exists(steamcmd_path)
    result = "SteamCMD is installed." if is_installed else "SteamCMD is not installed."
    logging.info(f"SteamCMD check: {result}")
    return result

# Function to install SteamCMD
def install_steamcmd():
    os_type = platform.system()
    logging.info(f"Installing SteamCMD for {os_type}")
    
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
            subprocess.run([steamcmd_path, "+quit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logging.info("SteamCMD initial run completed")
            return "SteamCMD installed successfully."
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

# Function to parse SteamCMD output for progress and size
def parse_progress(line):
    try:
        # Look for progress percentage
        progress_patterns = [
            r'(Progress|Update|Download): .*?(\d+\.\d+)%',
            r'(\d+\.\d+)% complete',
            r'Progress: +(\d+\.\d+) %'
        ]
        
        for pattern in progress_patterns:
            progress_match = re.search(pattern, line)
            if progress_match:
                return {"progress": float(progress_match.group(2) if len(progress_match.groups()) > 1 else progress_match.group(1))}
        
        # Look for total size in various formats
        size_patterns = [
            r'(size|total): (\d+\.?\d*) (\w+)',
            r'downloading (\d+\.?\d*) (\w+)',
            r'download of (\d+\.?\d*) (\w+)'
        ]
        
        for pattern in size_patterns:
            size_match = re.search(pattern, line, re.IGNORECASE)
            if size_match:
                size = float(size_match.group(2))
                unit = size_match.group(3)
                return {"total_size": size, "unit": unit}
        
        # Look for error messages
        error_keywords = [
            "Invalid Password", "Connection to Steam servers failed",
            "ERROR", "FAILED", "Authentication failed", "timeout"
        ]
        
        for keyword in error_keywords:
            if keyword in line:
                return {"error": line}
                
        return None
    except Exception as e:
        logging.error(f"Error parsing progress: {str(e)}")
        return None

# Function to manage the download queue
def process_download_queue():
    if download_queue and not active_downloads:
        next_download = download_queue.pop(0)
        thread = threading.Thread(target=next_download["function"], args=next_download["args"])
        thread.daemon = True
        thread.start()

# Function to verify game installation
def verify_installation(appid, install_path):
    logging.info(f"Verifying installation for App ID: {appid}")
    
    cmd_args = [get_steamcmd_path()]
    cmd_args.extend(["+login", "anonymous", "+app_update", appid, "validate", "+quit"])
    
    process = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for line in process.stdout:
        logging.debug(f"Verification output: {line.strip()}")
        if f"Success! App '{appid}' fully installed" in line:
            logging.info(f"Verification successful for App ID: {appid}")
            return True
    
    logging.warning(f"Verification failed for App ID: {appid}")
    return False

# Function to download the game with progress updates
def download_game(username, password, guard_code, anonymous, game_input, validate=True):
    # Get the automatic download location
    download_path = get_default_download_location()
    
    appid = parse_game_input(game_input)
    if not appid:
        yield "Invalid game ID or URL. Please enter a valid Steam game ID or store URL."
        return
    
    # Validate the App ID
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        yield f"Error: {game_info}"
        return
    
    # Create a unique ID for this download
    download_id = f"{appid}_{int(time.time())}"
    active_downloads[download_id] = {"status": "starting", "progress": 0}
    
    # Ensure download directory exists
    os.makedirs(download_path, exist_ok=True)
    game_path = os.path.join(download_path, game_info['name'] if isinstance(game_info, dict) else f"app_{appid}")
    os.makedirs(game_path, exist_ok=True)
    
    yield f"Starting download for: {game_info['name'] if isinstance(game_info, dict) else f'App ID: {appid}'}\n"
    yield f"Download location: {game_path}\n"
    
    # Construct SteamCMD arguments
    cmd_args = [get_steamcmd_path()]
    
    if anonymous:
        cmd_args.extend(["+login", "anonymous"])
    else:
        cmd_args.extend(["+login", username, password])
        if guard_code:
            cmd_args.append(guard_code)
    
    cmd_args.extend(["+force_install_dir", game_path, "+app_update", appid])
    
    if validate:
        cmd_args.append("validate")
    
    cmd_args.append("+quit")
    
    # Variables for progress tracking
    total_size = None
    unit = None
    start_time = time.time()
    active_downloads[download_id]["start_time"] = start_time
    
    logging.info(f"Starting download for App ID: {appid}, Command: {' '.join(cmd_args)}")
    
    # Run SteamCMD
    try:
        process = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        active_downloads[download_id]["status"] = "downloading"
        
        for line in process.stdout:
            logging.debug(f"SteamCMD output: {line.strip()}")
            
            # Check for specific error conditions
            if "Invalid Password" in line:
                active_downloads[download_id]["status"] = "failed"
                yield "Error: Invalid credentials. Please check your username and password."
                break
                
            if "Steam Guard code is incorrect" in line:
                active_downloads[download_id]["status"] = "failed"
                yield "Error: Incorrect Steam Guard code."
                break
                
            if "Connection to Steam servers failed" in line:
                active_downloads[download_id]["status"] = "failed"
                yield "Error: Connection to Steam servers failed. Check your internet connection."
                break
            
            info = parse_progress(line)
            if info:
                if "error" in info:
                    active_downloads[download_id]["status"] = "failed"
                    yield f"Error: {info['error']}"
                    break
                    
                if "total_size" in info:
                    total_size = info["total_size"]
                    unit = info["unit"]
                    active_downloads[download_id]["total_size"] = total_size
                    active_downloads[download_id]["unit"] = unit
                    yield f"Total download size: {total_size} {unit}\n"
                    
                elif "progress" in info and total_size:
                    progress = info["progress"]
                    active_downloads[download_id]["progress"] = progress
                    
                    downloaded = (progress / 100) * total_size
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    
                    # Calculate remaining time
                    remaining_size = total_size - downloaded
                    remaining_time = remaining_size / speed if speed > 0 else 0
                    
                    # Format remaining time as hours:minutes:seconds
                    hours, remainder = divmod(remaining_time, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s" if hours > 0 else f"{int(minutes)}m {int(seconds)}s"
                    
                    status = (f"Downloading: {progress:.1f}% - {downloaded:.2f}/{total_size} {unit}\n"
                             f"Speed: {speed:.2f} {unit}/s - Remaining: {time_str}")
                    
                    yield status
                elif "progress" in info:
                    progress = info["progress"]
                    active_downloads[download_id]["progress"] = progress
                    yield f"Downloading: {progress:.1f}%"
            else:
                # Check for completion message
                if "Success! App fully installed." in line or f"Success! App '{appid}' fully installed" in line:
                    active_downloads[download_id]["status"] = "completed"
                    yield "Download completed successfully!"
                else:
                    yield line.strip()
        
        process.wait()
        
        if process.returncode == 0:
            active_downloads[download_id]["status"] = "completed"
            active_downloads[download_id]["progress"] = 100
            active_downloads[download_id]["end_time"] = time.time()
            
            yield "Download completed successfully!"
            
            # Verify installation if requested
            if validate:
                yield "Verifying installation..."
                if verify_installation(appid, game_path):
                    yield "Verification successful. Game is ready to play!"
                else:
                    yield "Verification failed. You may need to repair the installation."
        else:
            active_downloads[download_id]["status"] = "failed"
            yield f"Download failed with exit code: {process.returncode}"
            
    except Exception as e:
        active_downloads[download_id]["status"] = "failed"
        logging.error(f"Error during download: {str(e)}")
        yield f"Error during download: {str(e)}"
    
    # Remove from active downloads
    del active_downloads[download_id]
    
    # Process next download in queue if any
    process_download_queue()

# Function to add a download to the queue
def queue_download(username, password, guard_code, anonymous, game_input, validate=True):
    if not anonymous and (not username or not password):
        return "Error: Username and password are required for non-anonymous downloads."
    
    appid = parse_game_input(game_input)
    if not appid:
        return "Invalid game ID or URL. Please enter a valid Steam game ID or store URL."
    
    # Add the download to the queue
    download_queue.append({
        "function": download_game,
        "args": (username, password, guard_code, anonymous, game_input, validate)
    })
    
    # Start processing the queue if not already processing
    if not active_downloads:
        process_download_queue()
    
    return f"Added game with App ID {appid} to the download queue. Position: {len(download_queue)}"

# Function to get a list of installed games
def list_installed_games():
    library_path = get_default_download_location()
    if not os.path.exists(library_path):
        return f"Library folder not found at {library_path}."
    
    games = []
    for item in os.listdir(library_path):
        path = os.path.join(library_path, item)
        if os.path.isdir(path):
            # Check if it looks like a Steam game directory
            if os.path.exists(os.path.join(path, "steam_appid.txt")):
                try:
                    with open(os.path.join(path, "steam_appid.txt"), "r") as f:
                        appid = f.read().strip()
                    
                    games.append({
                        "name": item,
                        "appid": appid,
                        "path": path,
                        "size": get_folder_size(path)
                    })
                except:
                    games.append({
                        "name": item,
                        "path": path,
                        "size": get_folder_size(path)
                    })
            else:
                games.append({
                    "name": item,
                    "path": path,
                    "size": get_folder_size(path)
                })
    
    if not games:
        return f"No games found in the library at {library_path}."
    
    result = f"Installed Games (in {library_path}):\n"
    for game in games:
        result += f"- {game['name']}"
        if 'appid' in game:
            result += f" (App ID: {game['appid']})"
        result += f" - {format_size(game['size'])}\n"
    
    return result

# Helper function to get folder size
def get_folder_size(folder_path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            total_size += os.path.getsize(file_path)
    return total_size

# Helper function to format size
def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

# Gradio interface
with gr.Blocks(title="Steam Games Downloader") as app:
    gr.Markdown("# Steam Games Downloader")
    gr.Markdown("Download Steam games using SteamCMD with a user-friendly interface.")
    
    # System check section
    with gr.Tab("Setup"):
        gr.Markdown("### SteamCMD Setup")
        with gr.Row():
            check_button = gr.Button("Check SteamCMD Installation")
            install_button = gr.Button("Install SteamCMD")
        
        setup_output = gr.Textbox(label="Status", lines=5)
        gr.Markdown(f"### Download Location\nGames will be automatically downloaded to: **{get_default_download_location()}**")
    
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
        
        ### Download Location
        Games are automatically saved to a platform-specific location:
        - Windows: ~/SteamLibrary
        - macOS: ~/Library/Application Support/SteamLibrary
        - Linux: ~/SteamLibrary
        
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

# Launch the app
if __name__ == "__main__":
    app.launch(share=True)