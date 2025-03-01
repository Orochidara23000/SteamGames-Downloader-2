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
import sys
from datetime import datetime, timedelta
import shutil
import psutil
import uvicorn
from fastapi import FastAPI
import asyncio


# Set up logging to both file and stdout
log_level = os.environ.get('LOG_LEVEL', 'INFO')
log_dir = '/app/logs' if os.path.exists('/app/logs') else '.'
log_file = os.path.join(log_dir, 'steam_downloader.log')

logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)

logging.info(f"Starting Steam Downloader application (PID: {os.getpid()})")

# Global variables for download management
active_downloads = {}
download_queue = []
queue_lock = threading.Lock()

# Environment variable handling for containerization
STEAM_DOWNLOAD_PATH = os.environ.get('STEAM_DOWNLOAD_PATH', '/data/downloads')

# Global variable to store the share URL
SHARE_URL = ""

# Define your FastAPI app here
fastapi_app = FastAPI()

@fastapi_app.get("/status")
def get_status():
    return {"status": "running"}

def update_share_url(share_url):
    global SHARE_URL
    SHARE_URL = share_url
    logging.info(f"Gradio share URL updated: {share_url}")

def get_default_download_location():
    if STEAM_DOWNLOAD_PATH:
        logging.info(f"Using environment variable for download path: {STEAM_DOWNLOAD_PATH}")
        return STEAM_DOWNLOAD_PATH
    if platform.system() == "Windows":
        path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
    elif platform.system() == "Darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SteamLibrary")
    else:
        path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
    logging.info(f"Using platform-specific download path: {path}")
    return path

def get_steamcmd_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    steamcmd_dir = os.path.join(base_dir, "steamcmd")
    if platform.system() == "Windows":
        path = os.path.join(steamcmd_dir, "steamcmd.exe")
    else:
        path = os.path.join(steamcmd_dir, "steamcmd.sh")
    logging.info(f"SteamCMD path: {path}")
    return path

def check_steamcmd():
    steamcmd_path = get_steamcmd_path()
    is_installed = os.path.exists(steamcmd_path)
    result = "SteamCMD is installed." if is_installed else "SteamCMD is not installed."
    logging.info(f"SteamCMD check: {result}")
    return result

def install_steamcmd():
    logging.info("Installing SteamCMD for Linux")
    steamcmd_install_dir = "/app/steamcmd"
    steamcmd_path = os.path.join(steamcmd_install_dir, "steamcmd.sh")
    
    # Remove existing SteamCMD directory if it exists
    if os.path.exists(steamcmd_install_dir):
        logging.info("Removing existing SteamCMD directory: /app/steamcmd")
        shutil.rmtree(steamcmd_install_dir)
    
    # Re-create the /app/steamcmd directory before downloading
    os.makedirs(steamcmd_install_dir, exist_ok=True)
    
    # Download and extract SteamCMD
    logging.info("Downloading SteamCMD from https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz")
    response = requests.get("https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz")
    tarball_path = os.path.join(steamcmd_install_dir, "steamcmd_linux.tar.gz")
    
    with open(tarball_path, "wb") as f:
        f.write(response.content)
    
    logging.info("Extracting SteamCMD tar.gz file")
    with tarfile.open(tarball_path, "r:gz") as tar:
        tar.extractall(path=steamcmd_install_dir)
    
    # Make the steamcmd.sh executable
    os.chmod(steamcmd_path, 0o755)
    logging.info("Made steamcmd.sh executable")
    
    # Run SteamCMD for the first time to complete installation
    logging.info("Running SteamCMD for the first time to complete installation")
    os.system(steamcmd_path + " +quit")
    logging.info("SteamCMD initial run completed successfully")
    
    # Return two outputs: a success message and the path to steamcmd.sh
    return "SteamCMD installed successfully.", steamcmd_path

def parse_game_input(input_str):
    logging.info(f"Parsing game input: {input_str}")
    if not input_str or input_str.strip() == "":
        logging.warning("Empty game input provided")
        return None
    if input_str.isdigit():
        logging.info(f"Input is a valid App ID: {input_str}")
        return input_str
    
    # Support for Steam store URLs
    url_patterns = [
        r'store\.steampowered\.com/app/(\d+)',
        r'steamcommunity\.com/app/(\d+)',
        r'/app/(\d+)'
    ]
    for pattern in url_patterns:
        match = re.search(pattern, input_str)
        if match:
            appid = match.group(1)
            logging.info(f"Extracted App ID: {appid}")
            return appid
    logging.error("Failed to extract App ID from input")
    return None

def validate_appid(appid):
    logging.info(f"Validating App ID: {appid}")
    try:
        # Check if app exists via Steam API
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
        logging.info(f"Querying Steam API: {url}")
        
        response = requests.get(url, timeout=10)
        
        if not response.ok:
            logging.error(f"Steam API request failed with status: {response.status_code}")
            return False, f"Steam API request failed with status: {response.status_code}"
        
        data = response.json()
        
        if not data or not data.get(appid):
            logging.error(f"Invalid response from Steam API for App ID {appid}")
            return False, "Invalid response from Steam API"
        
        if not data.get(appid, {}).get('success', False):
            logging.warning(f"Game not found for App ID: {appid}")
            return False, "Game not found on Steam"
        
        game_data = data[appid]['data']
        
        # Enhanced game info with more details
        game_info = {
            'name': game_data.get('name', 'Unknown Game'),
            'required_age': game_data.get('required_age', 0),
            'is_free': game_data.get('is_free', False),
            'developers': game_data.get('developers', ['Unknown']),
            'publishers': game_data.get('publishers', ['Unknown']),
            'platforms': game_data.get('platforms', {}),
            'categories': [cat.get('description') for cat in game_data.get('categories', [])],
            'genres': [genre.get('description') for genre in game_data.get('genres', [])],
            'header_image': game_data.get('header_image', None),
            'background_image': game_data.get('background', None),
            'release_date': game_data.get('release_date', {}).get('date', 'Unknown'),
            'metacritic': game_data.get('metacritic', {}).get('score', None),
            'description': game_data.get('short_description', 'No description available'),
            'size_mb': game_data.get('file_size', 'Unknown')
        }
        
        logging.info(f"Game found: {game_info['name']} (Free: {game_info['is_free']})")
        return True, game_info
        
    except requests.exceptions.Timeout:
        logging.error(f"Timeout while validating App ID {appid}")
        return False, "Timeout while connecting to Steam API"
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error while validating App ID {appid}: {str(e)}")
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON response from Steam API for App ID {appid}")
        return False, "Invalid response from Steam API"
    except Exception as e:
        logging.error(f"Validation error for App ID {appid}: {str(e)}")
        return False, f"Validation error: {str(e)}"

def parse_progress(line):
    try:
        # Improved progress parsing with more information
        line_lower = line.lower()
        
        # Look for progress percentage patterns
        progress_patterns = [
            r'(?:progress|update|download):\s*(?:.*?)(\d+\.\d+)%',  # Matches various progress formats
            r'(\d+\.\d+)%\s*complete',
            r'progress:\s+(\d+\.\d+)\s*%',
            r'(\d+)\s+of\s+(\d+)\s+MB\s+\((\d+\.\d+)%\)'  # Matches current/total size
        ]
        
        for pattern in progress_patterns:
            progress_match = re.search(pattern, line_lower)
            if progress_match:
                if len(progress_match.groups()) == 3:  # Pattern with current/total size
                    current = int(progress_match.group(1))
                    total = int(progress_match.group(2))
                    progress = float(progress_match.group(3))
                    return {
                        "progress": progress,
                        "current_size": current,
                        "total_size": total,
                        "unit": "MB"
                    }
                else:
                    progress = float(progress_match.group(1))
                    return {"progress": progress}
        
        # Look for download speed
        speed_patterns = [
            r'(\d+\.?\d*)\s*(KB|MB|GB)/s',
            r'at\s+(\d+\.?\d*)\s*(KB|MB|GB)/s'
        ]
        
        for pattern in speed_patterns:
            speed_match = re.search(pattern, line_lower)
            if speed_match:
                speed = float(speed_match.group(1))
                unit = speed_match.group(2)
                return {"speed": speed, "speed_unit": unit}
        
        # Look for ETA
        eta_patterns = [
            r'ETA\s+(\d+m\s*\d+s)',
            r'ETA\s+(\d+:\d+:\d+)'
        ]
        
        for pattern in eta_patterns:
            eta_match = re.search(pattern, line_lower)
            if eta_match:
                return {"eta": eta_match.group(1)}
        
        # Look for total size in various formats
        size_patterns = [
            r'(?:size|total):\s*(\d+\.?\d*)\s*(\w+)',
            r'downloading\s+(\d+\.?\d*)\s*(\w+)',
            r'download of\s+(\d+\.?\d*)\s*(\w+)'
        ]
        
        for pattern in size_patterns:
            size_match = re.search(pattern, line_lower)
            if size_match:
                size = float(size_match.group(1))
                unit = size_match.group(2)
                return {"total_size": size, "unit": unit}
        
        # Check for success messages
        success_patterns = [
            r'success!\s+app\s+[\'"]?(\d+)[\'"]'
        ]
        
        for pattern in success_patterns:
            success_match = re.search(pattern, line_lower)
            if success_match:
                return {"success": True}
        
        return {}
    
    except Exception as e:
        logging.error(f"Error parsing progress line: {line}. Error: {str(e)}")
        return {}

def process_download_queue():
    with queue_lock:
        if download_queue and len(active_downloads) == 0:
            next_download = download_queue.pop(0)
            thread = threading.Thread(target=next_download["function"], args=next_download["args"])
            thread.daemon = True
            thread.start()
            logging.info(f"Started new download thread. Remaining in queue: {len(download_queue)}")

def verify_installation(appid, install_path):
    logging.info(f"Verifying installation for App ID: {appid} at path: {install_path}")
    
    if not os.path.exists(install_path):
        logging.error(f"Installation path does not exist: {install_path}")
        return False
    
    cmd_args = [get_steamcmd_path()]
    cmd_args.extend([
        "+login", "anonymous", 
        "+force_install_dir", install_path,
        "+app_update", appid, "validate", 
        "+quit"
    ])
    
    try:
        logging.info(f"Running verification command: {' '.join(cmd_args)}")
        process = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        verification_successful = False
        output_lines = []
        
        for line in process.stdout:
            line = line.strip()
            output_lines.append(line)
            logging.debug(f"Verification output: {line}")
            
            if f"Success! App '{appid}' fully installed" in line:
                verification_successful = True
        
        process.wait()
        
        if verification_successful:
            logging.info(f"Verification successful for App ID: {appid}")
            return True
        else:
            logging.warning(f"Verification failed for App ID: {appid}")
            if output_lines:
                logging.warning(f"Last 5 output lines: {output_lines[-5:]}")
            return False
    except Exception as e:
        logging.error(f"Error during verification of App ID {appid}: {str(e)}")
        return False

def download_game(username, password, guard_code, anonymous, game_input, validate_download):
    try:
        # Your logic to start the download
        if not game_input:
            return "Please enter a valid game ID or URL."
        
        # Simulate download process
        download_id = "12345"  # Example download ID
        return f"Download started for game ID: {download_id}."
    
    except Exception as e:
        logging.error(f"Error during download: {str(e)}")
        return f"Error: {str(e)}"

def queue_download(username, password, guard_code, anonymous, game_input, validate=True):
    logging.info(f"Queueing download for game: {game_input} (Anonymous: {anonymous})")
    
    if not anonymous and (not username or not password):
        error_msg = "Error: Username and password are required for non-anonymous downloads."
        logging.error(error_msg)
        return error_msg
    
    appid = parse_game_input(game_input)
    if not appid:
        error_msg = "Invalid game ID or URL. Please enter a valid Steam game ID or store URL."
        logging.error(error_msg)
        return error_msg
    
    # Validate the AppID
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        error_msg = f"Invalid AppID: {appid}. Error: {game_info}"
        logging.error(error_msg)
        return error_msg
    
    # Check if we can start a new download immediately or need to queue
    with queue_lock:
        if len(active_downloads) == 0:
            # Start download immediately
            thread = threading.Thread(
                target=download_game,
                args=(username, password, guard_code, anonymous, appid, validate)
            )
            thread.daemon = True
            thread.start()
            return f"Started download for {game_info.get('name', 'Unknown Game')} (AppID: {appid})"
        else:
            # Add to queue
            download_queue.append({
                "function": download_game,
                "args": (username, password, guard_code, anonymous, appid, validate)
            })
            position = len(download_queue)
            return f"Download for {game_info.get('name', 'Unknown Game')} (AppID: {appid}) queued at position {position}"

def get_download_status():
    # Get current downloads and queue
    active = []
    for id, info in active_downloads.items():
        active.append({
            "id": id,
            "name": info["name"],
            "appid": info["appid"],
            "progress": info["progress"],
            "status": info["status"],
            "eta": info["eta"],
            "runtime": str(datetime.now() - info["start_time"]).split('.')[0],  # Remove microseconds
            "speed": info.get("speed", "Unknown"),
            "size_downloaded": info.get("size_downloaded", "Unknown"),
            "total_size": info.get("total_size", "Unknown")
        })
    
    # Enhanced queue information
    queue = []
    for i, download in enumerate(download_queue):
        appid = download["args"][4]
        # Get game info again for better display - in a real implementation
        is_valid, game_info = validate_appid(appid)
        
        queue_item = {
            "position": i + 1,
            "appid": appid,
            "name": game_info.get('name', 'Unknown Game') if is_valid else "Unknown Game",
            "is_free": game_info.get('is_free', False) if is_valid else False,
            "size": game_info.get('size_mb', 'Unknown') if is_valid else "Unknown",
            "validate": download["args"][5]  # Whether validation is enabled
        }
        queue.append(queue_item)
    
    # Add system statistics
    system = {
        "cpu_usage": psutil.cpu_percent(),
        "memory_usage": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent,
        "network_speed": "N/A",  # Would need additional code to track network usage
        "uptime": str(datetime.now() - datetime.fromtimestamp(psutil.boot_time())).split('.')[0]
    }
    
    # Add history of completed downloads (mock data for now)
    history = []
    
    return {
        "active": active,
        "queue": queue,
        "system": system,
        "history": history
    }

def cancel_download(download_id):
    logging.info(f"Attempting to cancel download: {download_id}")
    
    if download_id in active_downloads:
        # Find and terminate the process
        current_pid = os.getpid()
        parent = psutil.Process(current_pid)
        
        for child in parent.children(recursive=True):
            try:
                cmdline = ' '.join(child.cmdline())
                if get_steamcmd_path() in cmdline and active_downloads[download_id]["appid"] in cmdline:
                    logging.info(f"Terminating process {child.pid} for download {download_id}")
                    child.terminate()
                    child.wait(5)  # Wait up to 5 seconds for graceful termination
                    
                    # If still running, kill forcefully
                    if child.is_running():
                        logging.warning(f"Process {child.pid} did not terminate gracefully, killing forcefully")
                        child.kill()
                    
                    active_downloads[download_id]["status"] = "Cancelled"
                    del active_downloads[download_id]
                    
                    # Process next download in queue
                    process_download_queue()
                    
                    return f"Download {download_id} cancelled successfully"
            except Exception as e:
                logging.error(f"Error cancelling process: {str(e)}")
        
        return f"Could not find process for download {download_id}"
    else:
        return f"Download {download_id} not found in active downloads"

def remove_from_queue(position):
    position = int(position)
    logging.info(f"Attempting to remove download from queue position: {position}")
    
    with queue_lock:
        if 1 <= position <= len(download_queue):
            removed = download_queue.pop(position - 1)
            return f"Removed download from queue position {position}"
        else:
            return f"Invalid queue position: {position}"

def get_game_details(game_input):
    appid = parse_game_input(game_input)
    if not appid:
        return {"success": False, "error": "Invalid game ID or URL"}
    
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        return {"success": False, "error": game_info}
    
    return {"success": True, "appid": appid, "game_info": game_info}

def create_download_games_tab():
    with gr.Tab("Download Games"):
        gr.Markdown("### Game Information")
        
        with gr.Row():
            with gr.Column(scale=3):
                game_input = gr.Textbox(
                    label="Game ID or Steam Store URL",
                    placeholder="Enter AppID (e.g., 570) or Steam store URL",
                    info="Tip: The AppID is the number in the URL of a Steam store page"
                )
                
            with gr.Column(scale=1):
                check_game_btn = gr.Button("Check Game", variant="secondary")
        
        # Game details display
        with gr.Row(visible=False) as game_details_row:
            with gr.Column(scale=1):
                game_image = gr.Image(label="Game Image", type="filepath", interactive=False)
            
            with gr.Column(scale=2):
                game_title = gr.Textbox(label="Game Title", interactive=False)
                game_description = gr.Textbox(label="Description", interactive=False)
                game_metadata = gr.Dataframe(
                    headers=["Property", "Value"],
                    interactive=False
                )
        
        gr.Markdown("### Download Options")
        
        with gr.Row():
            with gr.Column():
                with gr.Group():
                    gr.Markdown("#### Login Method")
                    anonymous = gr.Checkbox(label="Anonymous Login (Free Games Only)", value=True)
                    
                    with gr.Group(visible=False) as login_details:
                        username = gr.Textbox(label="Steam Username")
                        password = gr.Textbox(label="Steam Password", type="password")
                        guard_code = gr.Textbox(
                            label="Steam Guard Code (if applicable)", 
                            placeholder="Leave empty if not using Steam Guard"
                        )
            
            with gr.Column():
                with gr.Group():
                    gr.Markdown("#### Download Settings")
                    validate_download = gr.Checkbox(
                        label="Validate Files After Download", 
                        value=True,
                        info="Ensures all files are correctly downloaded"
                    )
                    debug_mode = gr.Checkbox(
                        label="Debug Mode", 
                        value=False,
                        info="Verbose logging for troubleshooting"
                    )
                    download_path = gr.Textbox(
                        label="Download Path", 
                        value=get_default_download_location(),
                        info="Location where games will be installed"
                    )
        
        download_btn = gr.Button("Download Game", variant="primary")
        download_status = gr.Markdown("Enter a game ID or URL and click 'Check Game' to start")
        
        # Function to update UI when game is checked
        def check_game(game_id):
            details = get_game_details(game_id)
            
            if not details["success"]:
                return (
                    gr.update(visible=False),  # Hide game details row
                    "",  # Game title
                    "",  # Game description
                    [],  # Game metadata
                    None,  # Game image
                    f"Error: {details['error']}"  # Download status
                )
            
            game_info = details["game_info"]
            
            # Format metadata for display
            metadata = [
                ["App ID", details["appid"]],
                ["Free Game", "Yes" if game_info.get("is_free", False) else "No"],
                ["Release Date", game_info.get("release_date", "Unknown")],
                ["Developer", ", ".join(game_info.get("developers", ["Unknown"]))],
                ["Publisher", ", ".join(game_info.get("publishers", ["Unknown"]))],
                ["Genres", ", ".join(game_info.get("genres", ["Unknown"]))],
                ["Metacritic", game_info.get("metacritic", "N/A")],
                ["Platforms", ", ".join([p for p, v in game_info.get("platforms", {}).items() if v])]
            ]
            
            # Get image URL or use placeholder
            image_url = game_info.get("header_image", None)
            
            return (
                gr.update(visible=True),  # Show game details row
                game_info.get("name", "Unknown Game"),  # Game title
                game_info.get("description", "No description available"),  # Game description
                metadata,  # Game metadata
                image_url,  # Game image
                f"Game found: {game_info.get('name', 'Unknown Game')} (AppID: {details['appid']})"  # Download status
            )
        
        # Connect events
        check_game_btn.click(
            check_game,
            inputs=[game_input],
            outputs=[game_details_row, game_title, game_description, game_metadata, game_image, download_status]
        )
        
        download_btn.click(
            fn=download_game,  # Call the download_game function
            inputs=[username, password, guard_code, anonymous, game_input, validate_download],
            outputs=[download_status]  # Output to the download_status component
        )
        
        return game_input, check_game_btn, download_btn, download_status

def create_gradio_interface():
    with gr.Blocks(title="Steam Game Downloader", theme=gr.themes.Soft()) as app:
        gr.Markdown("# Steam Game Downloader")
        gr.Markdown("Download Steam games directly using SteamCMD")
        
        with gr.Tabs():
            with gr.Tab("Setup"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### SteamCMD Installation")
                        steamcmd_status = gr.Textbox(label="SteamCMD Status", value=check_steamcmd(), interactive=False)
                        install_btn = gr.Button("Install/Update SteamCMD")
                        install_output = gr.Textbox(label="Installation Output", interactive=False)
                        
                        install_btn.click(
                            fn=install_steamcmd,
                            outputs=[install_output, steamcmd_status]
                        )
                    
                    with gr.Column():
                        gr.Markdown("### System Information")
                        system_info = gr.Dataframe(
                            headers=["Property", "Value"],
                            value=[
                                ["Operating System", platform.platform()],
                                ["CPU", platform.processor()],
                                ["Total Memory", f"{psutil.virtual_memory().total / (1024**3):.2f} GB"],
                                ["Free Disk Space", f"{psutil.disk_usage('/').free / (1024**3):.2f} GB"],
                                ["SteamCMD Path", get_steamcmd_path()]
                            ],
                            interactive=False
                        )
                        
                        refresh_system_btn = gr.Button("Refresh System Info")
                        
                        def update_system_info():
                            return [
                                ["Operating System", platform.platform()],
                                ["CPU", platform.processor()],
                                ["Total Memory", f"{psutil.virtual_memory().total / (1024**3):.2f} GB"],
                                ["Free Disk Space", f"{psutil.disk_usage('/').free / (1024**3):.2f} GB"],
                                ["SteamCMD Path", get_steamcmd_path()]
                            ]
                        
                        refresh_system_btn.click(fn=update_system_info, outputs=[system_info])
            
            # Call the create_download_games_tab function here
            game_input, check_game_btn, download_btn, download_status = create_download_games_tab()
            
            # Add the improved Downloads tab
            refresh_btn, auto_refresh = create_downloads_tab()
            
            with gr.Tab("Settings"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### Application Settings")
                        log_level = gr.Dropdown(
                            label="Log Level",
                            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                            value=os.environ.get('LOG_LEVEL', 'INFO')
                        )
                        max_concurrent_downloads = gr.Slider(
                            minimum=1,
                            maximum=5,
                            value=1,
                            step=1,
                            label="Max Concurrent Downloads",
                            info="Note: Multiple concurrent downloads may impact performance"
                        )
                        auto_validate = gr.Checkbox(
                            label="Auto-validate All Downloads",
                            value=True,
                            info="Automatically validate all downloads after completion"
                        )
                    
                    with gr.Column():
                        gr.Markdown("### Advanced Settings")
                        steamcmd_args = gr.Textbox(
                            label="Additional SteamCMD Arguments",
                            placeholder="Enter any additional arguments to pass to SteamCMD"
                        )
                        debug_mode = gr.Checkbox(
                            label="Debug Mode", 
                            value=False,
                            info="Enable verbose logging for troubleshooting"
                        )
                        keep_history = gr.Checkbox(
                            label="Keep Download History",
                            value=True,
                            info="Save details of completed downloads"
                        )
                
                save_settings_btn = gr.Button("Save Settings", variant="primary")
                settings_status = gr.Textbox(label="Settings Status", interactive=False)
                
                def save_settings(log_level, max_concurrent, auto_validate, steamcmd_args, debug_mode, keep_history):
                    # This would need to be implemented to actually save the settings
                    os.environ['LOG_LEVEL'] = log_level
                    # Update other settings as needed
                    return "Settings saved successfully"
                
                save_settings_btn.click(
                    save_settings,
                    inputs=[log_level, max_concurrent_downloads, auto_validate, steamcmd_args, debug_mode, keep_history],
                    outputs=[settings_status]
                )
            
            with gr.Tab("Help"):
                gr.Markdown("""
                ## Steam Game Downloader Help
                
                ### Quick Start Guide
                1. Go to the **Setup Tab** and install SteamCMD if not already installed
                2. Go to the **Download Games Tab** and enter a game ID or Steam store URL
                3. Click "Check Game" to verify and see game details
                4. Choose your login method (Anonymous for free games)
                5. Click "Download Game" to start or queue the download
                6. Monitor your downloads in the **Downloads Tab**
                
                ### Finding Game IDs
                - The AppID is the number in the URL of a Steam store page
                - Example: For `https://store.steampowered.com/app/570/Dota_2/` the AppID is `570`
                
                ### Anonymous Login
                - Only works for free-to-play games and demos
                - For paid games, you must provide your Steam credentials
                
                ### Download Options
                - **Validate Files**: Verifies all downloaded files are correct (recommended)
                - **Add to Queue**: Adds to queue instead of starting immediately
                - **Auto-start**: Automatically starts download when possible
                
                ### Download Management
                - You can pause, resume, or cancel active downloads
                - Queued downloads can be reordered or removed
                - System resources are monitored to ensure stable downloads
                
                ### Troubleshooting
                - If downloads fail, try reinstalling SteamCMD in the Setup tab
                - Check your available disk space
                - For paid games, ensure your credentials are correct
                - Look for detailed error messages in the Downloads tab
                """)
        
        # Start background thread for processing queue
        queue_thread = threading.Thread(target=queue_processor)
        queue_thread.daemon = True
        queue_thread.start()
    
    return app

def queue_processor():
    while True:
        process_download_queue()
        time.sleep(5)  # Check queue every 5 seconds

def reorder_queue(from_position, to_position):
    with queue_lock:
        if 1 <= from_position <= len(download_queue) and 1 <= to_position <= len(download_queue):
            # Convert to 0-based index
            from_idx = from_position - 1
            to_idx = to_position - 1
            
            # Get the item to move
            item = download_queue.pop(from_idx)
            
            # Insert at the new position
            download_queue.insert(to_idx, item)
            
            logging.info(f"Moved download from position {from_position} to {to_position}")
            return True, f"Moved download from position {from_position} to {to_position}"
        else:
            logging.warning(f"Invalid queue positions: from={from_position}, to={to_position}")
            return False, "Invalid queue positions"

def create_downloads_tab():
    with gr.Tab("Downloads"):
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Active Downloads")
                active_downloads_table = gr.Dataframe(
                    headers=["ID", "Name", "Progress", "Status", "Speed", "ETA", "Time Running"],
                    interactive=False
                )
                
                with gr.Row():
                    cancel_download_input = gr.Textbox(
                        label="Download ID to Cancel",
                        placeholder="Enter download ID to cancel"
                    )
                    cancel_download_btn = gr.Button("Cancel Download", variant="secondary")
                
                cancel_output = gr.Textbox(label="Cancel Result", interactive=False)
        
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Download Queue")
                queue_table = gr.Dataframe(
                    headers=["Position", "App ID", "Name", "Size", "Validate?"],
                    interactive=False
                )
                
                with gr.Row():
                    with gr.Column(scale=1):
                        remove_position = gr.Number(
                            label="Queue Position to Remove",
                            precision=0,
                            value=1
                        )
                    with gr.Column(scale=1):
                        remove_queue_btn = gr.Button("Remove from Queue")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        from_position = gr.Number(
                            label="Move From Position",
                            precision=0,
                            value=1
                        )
                    with gr.Column(scale=1):
                        to_position = gr.Number(
                            label="To Position",
                            precision=0,
                            value=2
                        )
                    with gr.Column(scale=1):
                        move_queue_btn = gr.Button("Move in Queue")
                
                queue_action_result = gr.Textbox(label="Queue Action Result", interactive=False)
        
        with gr.Row():
            with gr.Column():
                gr.Markdown("### System Status")
                system_stats = gr.Dataframe(
                    headers=["Metric", "Value"],
                    value=[
                        ["CPU Usage", f"{psutil.cpu_percent()}%"],
                        ["Memory Usage", f"{psutil.virtual_memory().percent}%"],
                        ["Disk Usage", f"{psutil.disk_usage('/').percent}%"],
                        ["Active Downloads", str(len(active_downloads))],
                        ["Queued Downloads", str(len(download_queue))]
                    ],
                    interactive=False
                )
        
        # Refresh button for downloads status
        with gr.Row():
            refresh_btn = gr.Button("Refresh Status")
            auto_refresh = gr.Checkbox(label="Auto-refresh (10s)", value=False)
        
        # Function to update download status in the UI
        def update_downloads_status():
            try:
                # Fetch the latest download status
                status = get_download_status()
                
                # Format active downloads for table display
                active_data = []
                for download in status["active"]:
                    active_data.append([
                        download["id"],
                        download["name"],
                        f"{download['progress']:.1f}%",
                        download["status"],
                        download["speed"],
                        download["eta"],
                        download["runtime"]
                    ])
                
                # Format queue for table display
                queue_data = []
                for item in status["queue"]:
                    queue_data.append([
                        item["position"],
                        item["appid"],
                        item["name"],
                        item["size"],
                        "Yes" if item["validate"] else "No"
                    ])
                
                # Format system stats
                system_data = [
                    ["CPU Usage", f"{status['system']['cpu_usage']}%"],
                    ["Memory Usage", f"{status['system']['memory_usage']}%"],
                    ["Disk Usage", f"{status['system']['disk_usage']}%"],
                    ["Active Downloads", str(len(status["active"]))],
                    ["Queued Downloads", str(len(status["queue"]))],
                    ["System Uptime", status['system']['uptime']]
                ]
                
                # Return the data as outputs for the three tables/components
                return active_data, queue_data, system_data
            
            except Exception as e:
                logging.error(f"Error updating download status: {str(e)}")
                # Return empty lists for all outputs if there's an error
                return [], [], []
        
        # Connect events for refreshing data
        refresh_btn.click(
            fn=update_downloads_status,
            outputs=[active_downloads_table, queue_table, system_stats]
        )
        
        # Connect cancel download button
        cancel_download_btn.click(
            fn=cancel_download,
            inputs=[cancel_download_input],
            outputs=[cancel_output]
        )
        
        # Connect remove from queue button
        remove_queue_btn.click(
            fn=remove_from_queue,
            inputs=[remove_position],
            outputs=[queue_action_result]
        )
        
        # Connect move in queue button
        move_queue_btn.click(
            fn=lambda from_pos, to_pos: reorder_queue(int(from_pos), int(to_pos))[1],
            inputs=[from_position, to_position],
            outputs=[queue_action_result]
        )
        
        # Handle auto-refresh
        def setup_auto_refresh(auto_refresh_enabled):
            if auto_refresh_enabled:
                while True:
                    update_downloads_status()
                    time.sleep(10)  # Refresh every 10 seconds
            else:
                return  # Stop refreshing
        
        auto_refresh.change(
            fn=setup_auto_refresh,
            inputs=[auto_refresh],
            outputs=[]
        )
        
        # Initial update of tables
        update_downloads_status()
        
    return refresh_btn, auto_refresh

def get_downloads_status():
    try:
        # Fetch the latest download status
        status = get_download_status()
        
        # Prepare active downloads data
        active_data = []
        for download in status["active"]:
            active_data.append([
                download["id"],
                download["name"],
                f"{download['progress']:.1f}%",
                download["status"],
                download["speed"],
                download["eta"],
                download["runtime"]
            ])
        
        # Prepare download queue data
        queue_data = []
        for item in status["queue"]:
            queue_data.append([
                item["position"],
                item["appid"],
                item["name"],
                item["size"],
                "Yes" if item["validate"] else "No"
            ])
        
        # Prepare system statistics data
        system_data = [
            ["CPU Usage", f"{status['system']['cpu_usage']}%"],
             ["Memory Usage", f"{status['system']['memory_usage']}%"],
             ["Disk Usage", f"{status['system']['disk_usage']}%"],
             ["Active Downloads", str(len(status["active"]))],
             ["Queued Downloads", str(len(status["queue"]))],
             ["System Uptime", status['system']['uptime']]
            ]
        
        return active_data, queue_data, system_data
    
    except Exception as e:
        logging.error(f"Error fetching download status: {str(e)}")
        # Return empty data in case of error
        return [], [], []

if __name__ == "__main__":
    # Ensure SteamCMD is installed
    if not check_steamcmd():
        install_steamcmd()
    
    # Create the Gradio interface directly
    app_interface = create_gradio_interface()
    
    # Start the FastAPI server for file serving in a separate thread
    threading.Thread(
        target=lambda: uvicorn.run(fastapi_app, host="0.0.0.0", port=8081),
        daemon=True
    ).start()
    
    port = int(os.getenv("PORT", 7861))
    logging.info(f"Starting application on port {port}")
    
    # Launch Gradio and capture the return value
    launch_info = app_interface.launch(
        server_port=port, 
        server_name="0.0.0.0", 
        share=True, 
        prevent_thread_lock=True,
        show_error=True
    )
    
    # Check if launch_info has a share_url attribute
    if hasattr(launch_info, 'share_url'):
        update_share_url(launch_info.share_url)
        logging.info(f"Gradio share URL: {launch_info.share_url}")
    else:
        logging.warning("Launch info does not contain a share URL.")
    
    # Keep the script running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Application stopped by user")