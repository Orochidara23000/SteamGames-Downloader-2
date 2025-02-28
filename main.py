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
STEAM_DOWNLOAD_PATH = os.environ.get('STEAM_DOWNLOAD_PATH')

# Global variable to store the share URL
SHARE_URL = ""

# Define your FastAPI app here
fastapi_app = FastAPI()

# Health check endpoint
@fastapi_app.get("/")
def health_check():
    return {"status": "healthy", "app": "Steam Downloader"}

def update_share_url(share_url):
    global SHARE_URL
    SHARE_URL = share_url
    logging.info(f"Gradio share URL updated: {share_url}")

def get_default_download_location():
    # First check for environment variable (for containerization)
    if STEAM_DOWNLOAD_PATH:
        logging.info(f"Using environment variable for download path: {STEAM_DOWNLOAD_PATH}")
        return STEAM_DOWNLOAD_PATH
        
    # Fall back to platform-specific paths
    if platform.system() == "Windows":
        path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
    elif platform.system() == "Darwin":  # macOS
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SteamLibrary")
    else:  # Linux and others
        path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
    
    logging.info(f"Using platform-specific download path: {path}")
    return path

def get_steamcmd_path():
    # Use absolute paths
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
    steamcmd_dir = "/app/steamcmd"
    steamcmd_path = os.path.join(steamcmd_dir, "steamcmd.sh")
    
    # Remove existing SteamCMD directory if it exists
    if os.path.exists(steamcmd_dir):
        logging.info(f"Removing existing SteamCMD directory: {steamcmd_dir}")
        shutil.rmtree(steamcmd_dir)
    
    # Create the SteamCMD directory
    os.makedirs(steamcmd_dir, exist_ok=True)  # Ensure the directory exists
    
    # Download SteamCMD
    logging.info(f"Downloading SteamCMD from https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz")
    response = requests.get("https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz")
    
    # Write the downloaded content to the file
    steamcmd_tar_path = os.path.join(steamcmd_dir, "steamcmd_linux.tar.gz")
    with open(steamcmd_tar_path, "wb") as f:
        f.write(response.content)
    
    logging.info("Extracting SteamCMD tar.gz file")
    with tarfile.open(steamcmd_tar_path, "r:gz") as tar:
        tar.extractall(path=steamcmd_dir)
    
    # Make the steamcmd.sh executable
    os.chmod(steamcmd_path, 0o755)
    logging.info("Made steamcmd.sh executable")
    
    # Run SteamCMD for the first time
    logging.info("Running SteamCMD for the first time to complete installation")
    os.system(steamcmd_path + " +quit")
    
    logging.info("SteamCMD initial run completed successfully")
    
    # Return two outputs, for example, a success message and the path
    return "SteamCMD installed successfully.", steamcmd_path  # Adjust as needed

def parse_game_input(input_str):
    logging.info(f"Parsing game input: {input_str}")
    
    # Check if input is empty
    if not input_str or input_str.strip() == "":
        logging.warning("Empty game input provided")
        return None
    
    # Check if it's a direct App ID
    if input_str.isdigit():
        logging.info(f"Input is a valid App ID: {input_str}")
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
            appid = match.group(1)
            logging.info(f"Extracted App ID {appid} from URL: {input_str}")
            return appid
    
    logging.warning(f"Failed to parse game input: {input_str}")
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
        # Convert line to lowercase for case-insensitive matching
        line_lower = line.lower()
        
        # Look for progress percentage
        progress_patterns = [
            r'(?:progress|update|download):\s*(?:.*?)(\d+\.\d+)%',
            r'(\d+\.\d+)%\s*complete',
            r'progress:\s+(\d+\.\d+)\s*%'
        ]
        
        for pattern in progress_patterns:
            progress_match = re.search(pattern, line_lower)
            if progress_match:
                progress = float(progress_match.group(1))
                return {"progress": progress}
        
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
        
        # Look for error messages
        error_keywords = [
            "invalid password", "connection to steam servers failed",
            "error", "failed", "authentication failed", "timeout"
        ]
        
        for keyword in error_keywords:
            if keyword in line_lower:
                return {"error": line}
                
        return None
    except Exception as e:
        logging.error(f"Error parsing progress: {str(e)}")
        return None

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
    logging.info(f"Starting download for game: {game_input}")
    
    # Parse the AppID from input
    appid = parse_game_input(game_input)
    if not appid:
        logging.error(f"Invalid game input: {game_input}")
        return "Invalid game ID or URL. Please enter a valid Steam game ID or store URL."
    
    # Validate the AppID
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        logging.error(f"Invalid AppID: {appid}")
        return f"Invalid AppID: {appid}. Error: {game_info}"
    
    # Check if anonymous login is being used for a non-free game
    if anonymous and not game_info.get('is_free', False):
        warning = f"Warning: You're attempting to download a paid game ({game_info['name']}) with anonymous login, which may not work."
        logging.warning(warning)
    
    logging.info(f"Validated game: {game_info['name']} (AppID: {appid})")
    
    # Create unique download ID
    download_id = f"download_{appid}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Mark this download as active
    active_downloads[download_id] = {
        "appid": appid,
        "name": game_info.get('name', 'Unknown Game'),
        "progress": 0,
        "status": "Initializing",
        "start_time": datetime.now(),
        "last_update": datetime.now(),
        "eta": "Unknown"
    }
    
    try:
        # Create download directory if it doesn't exist
        download_dir = get_default_download_location()
        game_name = game_info.get('name', appid)
        install_dir = os.path.join(download_dir, game_name)
        
        if not os.path.exists(download_dir):
            logging.info(f"Creating download directory: {download_dir}")
            os.makedirs(download_dir, exist_ok=True)
        
        # Prepare download command
        cmd = [get_steamcmd_path()]
        
        if anonymous:
            cmd.extend(["+login", "anonymous"])
        else:
            cmd.extend(["+login", username, password])
            if guard_code:
                # This is a simplification - actual Steam Guard handling might be more complex
                cmd.append(guard_code)
        
        # Add download command
        cmd.extend([
            "+force_install_dir", install_dir,
            "+app_update", appid
        ])
        
        # Add validation if requested
        if validate_download:
            cmd.append("validate")
        
        cmd.append("+quit")
        
        # Sanitize command for logging (hide password)
        log_cmd = cmd.copy()
        if not anonymous and password:
            password_index = log_cmd.index(password)
            log_cmd[password_index] = "********"
        
        logging.info(f"Running command: {' '.join(log_cmd)}")
        
        # Start the process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Track progress and last speed to calculate ETA
        last_progress = 0
        last_progress_time = datetime.now()
        progress_samples = []
        
        # Track progress
        for line in process.stdout:
            line = line.strip()
            logging.debug(line)
            
            # Update status based on output
            progress_info = parse_progress(line)
            if progress_info:
                if "progress" in progress_info:
                    current_progress = progress_info["progress"]
                    current_time = datetime.now()
                    
                    # Only calculate speed if progress has changed
                    if current_progress > last_progress:
                        # Calculate progress speed (% per second)
                        time_diff = (current_time - last_progress_time).total_seconds()
                        if time_diff > 0:
                            progress_diff = current_progress - last_progress
                            speed = progress_diff / time_diff
                            
                            # Keep the last 5 speed samples for a rolling average
                            progress_samples.append(speed)
                            if len(progress_samples) > 5:
                                progress_samples.pop(0)
                            
                            # Calculate average speed and ETA
                            avg_speed = sum(progress_samples) / len(progress_samples)
                            if avg_speed > 0:
                                remaining_progress = 100 - current_progress
                                eta_seconds = remaining_progress / avg_speed
                                eta = str(timedelta(seconds=int(eta_seconds)))
                                active_downloads[download_id]["eta"] = eta
                            
                            last_progress = current_progress
                            last_progress_time = current_time
                    
                    active_downloads[download_id]["progress"] = current_progress
                    active_downloads[download_id]["status"] = f"Downloading: {current_progress:.1f}%"
                    active_downloads[download_id]["last_update"] = current_time
                
                if "error" in progress_info:
                    active_downloads[download_id]["status"] = f"Error: {progress_info['error']}"
            
            # Check for completion indicators
            if f"Success! App '{appid}' fully installed" in line:
                active_downloads[download_id]["status"] = "Completed"
                active_downloads[download_id]["progress"] = 100
                active_downloads[download_id]["eta"] = "0:00:00"
            
            # Check for Steam Guard prompts
            if "Steam Guard code" in line:
                active_downloads[download_id]["status"] = "Waiting for Steam Guard code"
        
        # Wait for process to complete
        process.wait()
        
        # Final status update
        if process.returncode == 0:
            if active_downloads[download_id]["progress"] < 100:
                active_downloads[download_id]["progress"] = 100
            
            if active_downloads[download_id]["status"] != "Completed":
                active_downloads[download_id]["status"] = "Completed"
            
            result = f"Download completed for {game_info.get('name', 'Unknown Game')} (AppID: {appid})"
            logging.info(result)
            
            # If validation was requested, verify the installation
            if validate_download:
                validation_result = verify_installation(appid, install_dir)
                if validation_result:
                    result += "\nValidation successful. Game is ready to play."
                else:
                    result += "\nValidation failed. Game may be corrupted or incomplete."
        else:
            active_downloads[download_id]["status"] = "Failed"
            result = f"Download failed for {game_info.get('name', 'Unknown Game')} (AppID: {appid}) with return code {process.returncode}"
            logging.error(result)
        
        # Clean up
        del active_downloads[download_id]
        
        # Process next download in queue
        process_download_queue()
        
        return result
    
    except Exception as e:
        logging.error(f"Error during download: {str(e)}")
        
        # Update status and clean up
        active_downloads[download_id]["status"] = f"Error: {str(e)}"
        del active_downloads[download_id]
        
        # Process next download in queue
        process_download_queue()
        
        return f"Error during download: {str(e)}"

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
    status = {
        "active": [
            {
                "id": id,
                "name": info["name"],
                "appid": info["appid"],
                "progress": info["progress"],
                "status": info["status"],
                "eta": info["eta"],
                "runtime": str(datetime.now() - info["start_time"]).split('.')[0]  # Remove microseconds
            }
            for id, info in active_downloads.items()
        ],
        "queue": [
            {
                "position": i + 1,
                "appid": download["args"][4],  # AppID is the 5th argument in the args tuple
            }
            for i, download in enumerate(download_queue)
        ],
        "system": {
            "cpu_usage": psutil.cpu_percent(),
            "memory_usage": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent,
        }
    }
    return status

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

def create_improved_gradio_interface():
    with gr.Blocks(title="Steam Game Downloader") as app:
        gr.Markdown("# Steam Game Downloader")
        gr.Markdown("Download Steam games directly using SteamCMD")
        
        # Create a state container for persistent data
        state = gr.State({
            "refresh_active": True,
            "last_refresh": time.time()
        })
        
        # Setup Tab
        with gr.Tab("Setup"):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### SteamCMD Installation")
                    steamcmd_status = gr.Textbox(label="SteamCMD Status", value=check_steamcmd(), interactive=False)
                    install_btn = gr.Button("Install/Update SteamCMD", variant="primary")
                    install_output = gr.Textbox(label="Installation Output", interactive=False)
                    
                    install_btn.click(
                        fn=install_steamcmd,
                        outputs=[install_output, steamcmd_status],
                        api_name="install_steamcmd"
                    )
                
                with gr.Column():
                    gr.Markdown("### Download Settings")
                    download_path = gr.Textbox(label="Download Location", value=get_default_download_location(), interactive=False)
                    
                    # Get current disk space
                    def get_disk_space():
                        path = get_default_download_location()
                        try:
                            free_space = psutil.disk_usage(path).free / (1024**3)
                            return f"{free_space:.2f} GB free at {path}"
                        except Exception as e:
                            return f"Error checking disk space: {str(e)}"
                    
                    disk_space = gr.Textbox(
                        label="Available Disk Space",
                        value=get_disk_space(),
                        interactive=False
                    )
                    
                    refresh_space_btn = gr.Button("Refresh Disk Space")
                    refresh_space_btn.click(
                        fn=get_disk_space,
                        outputs=[disk_space],
                        api_name="refresh_disk_space"
                    )
        
        # Download Games Tab
        with gr.Tab("Download Games"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### Game Details")
                    game_input = gr.Textbox(
                        label="Game ID or Steam Store URL", 
                        placeholder="Enter App ID (e.g., 570) or Steam store URL"
                    )
                    check_game_btn = gr.Button("Check Game", variant="secondary")
                    
                    # Improved game info display with HTML formatting
                    game_info_html = gr.HTML(label="Game Information")
                    
                    # Convert JSON game info to formatted HTML
                    def format_game_info(response):
                        if not response["success"]:
                            return f"<div style='color: red;'><b>Error:</b> {response['error']}</div>"
                        
                        game = response["game_info"]
                        html = f"""
                        <div style='padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>
                            <h3>{game.get('name', 'Unknown Game')}</h3>
                            <p><b>App ID:</b> {response["appid"]}</p>
                            <p><b>Free Game:</b> {"Yes" if game.get('is_free', False) else "No"}</p>
                            <p><b>Developers:</b> {', '.join(game.get('developers', ['Unknown']))}</p>
                            <p><b>Publishers:</b> {', '.join(game.get('publishers', ['Unknown']))}</p>
                            <p><b>Categories:</b> {', '.join(game.get('categories', ['Unknown']))}</p>
                        </div>
                        """
                        return html
                    
                    check_game_btn.click(
                        fn=lambda x: format_game_info(get_game_details(x)),
                        inputs=[game_input],
                        outputs=[game_info_html],
                        api_name="check_game"
                    )
                
                with gr.Column(scale=1):
                    gr.Markdown("### Login Information")
                    with gr.Group():
                        anonymous_login = gr.Checkbox(
                            label="Use Anonymous Login (for free games only)", 
                            value=True
                        )
                        
                        with gr.Group(visible=False) as login_group:
                            username = gr.Textbox(label="Steam Username")
                            password = gr.Textbox(label="Steam Password", type="password")
                            guard_code = gr.Textbox(
                                label="Steam Guard Code (if required)",
                                placeholder="Leave empty if not needed"
                            )
                        
                        # Toggle login fields visibility
                        def toggle_login_fields(anonymous):
                            return gr.Group.update(visible=not anonymous)
                        
                        anonymous_login.change(
                            fn=toggle_login_fields,
                            inputs=[anonymous_login],
                            outputs=[login_group]
                        )
                        
                        validate_download = gr.Checkbox(
                            label="Validate Download (Recommended)", 
                            value=True,
                            info="Ensures all game files are properly downloaded"
                        )
            
            with gr.Row():
                download_btn = gr.Button("Download Game", variant="primary", size="lg")
                download_output = gr.Textbox(
                    label="Download Status", 
                    interactive=False
                )
            
            download_btn.click(
                fn=queue_download,
                inputs=[username, password, guard_code, anonymous_login, game_input, validate_download],
                outputs=[download_output],
                api_name="download_game"
            )
        
        # Downloads Tab
        with gr.Tab("Downloads"):
            # Status indicators
            with gr.Row():
                with gr.Column(scale=1):
                    cpu_indicator = gr.Label(label="CPU Usage")
                with gr.Column(scale=1):
                    memory_indicator = gr.Label(label="Memory Usage")
                with gr.Column(scale=1):
                    disk_indicator = gr.Label(label="Disk Usage")
            
            # Active downloads
            gr.Markdown("### Active Downloads")
            with gr.Row():
                # Use a more visual component for active downloads
                active_downloads_table = gr.Dataframe(
                    headers=["ID", "Game", "Progress", "Status", "ETA", "Runtime"],
                    datatype=["str", "str", "number", "str", "str", "str"],
                    col_count=(6, "fixed"),
                    row_count=(5, "dynamic"),
                    interactive=False
                )
            
            with gr.Row():
                download_id_input = gr.Textbox(label="Download ID to Cancel")
                cancel_btn = gr.Button("Cancel Download", variant="stop")
                cancel_output = gr.Textbox(label="Operation Result", interactive=False)
            
            # Download queue
            gr.Markdown("### Download Queue")
            with gr.Row():
                queue_table = gr.Dataframe(
                    headers=["Position", "App ID", "Status"],
                    datatype=["number", "str", "str"],
                    col_count=(3, "fixed"),
                    row_count=(5, "dynamic"),
                    interactive=False
                )
            
            with gr.Row():
                queue_position = gr.Number(
                    label="Queue Position to Remove", 
                    precision=0,
                    minimum=1
                )
                remove_btn = gr.Button("Remove from Queue", variant="secondary")
            
            # Refresh controls
            with gr.Row():
                refresh_btn = gr.Button("Refresh Status Now", variant="primary")
                auto_refresh = gr.Checkbox(label="Auto-refresh (every 5 seconds)", value=True)
            
            # Format the status data for better display
            def format_status_for_display():
                status = get_download_status()
                
                # Format active downloads for table
                active_data = []
                for download in status["active"]:
                    active_data.append([
                        download["id"],
                        download["name"],
                        float(download["progress"]),
                        download["status"],
                        download["eta"],
                        download["runtime"]
                    ])
                
                # Format queue for table
                queue_data = []
                for item in status["queue"]:
                    queue_data.append([
                        item["position"],
                        item["appid"],
                        "Queued"
                    ])
                
                # System stats
                cpu = status["system"]["cpu_usage"]
                memory = status["system"]["memory_usage"]
                disk = status["system"]["disk_usage"]
                
                return active_data, queue_data, f"{cpu}%", f"{memory}%", f"{disk}%"
            
            # Handle the auto-refresh toggle
            def toggle_auto_refresh(state_data, enable_auto_refresh):
                state_data["refresh_active"] = enable_auto_refresh
                return state_data
            
            auto_refresh.change(
                fn=toggle_auto_refresh,
                inputs=[state, auto_refresh],
                outputs=[state]
            )
            
            # Manual refresh button
            refresh_btn.click(
                fn=format_status_for_display,
                outputs=[active_downloads_table, queue_table, cpu_indicator, memory_indicator, disk_indicator]
            )
            
            # Cancel download button
            cancel_btn.click(
                fn=cancel_download,
                inputs=[download_id_input],
                outputs=[cancel_output]
            )
            
            # Remove from queue button
            remove_btn.click(
                fn=remove_from_queue,
                inputs=[queue_position],
                outputs=[cancel_output]
            )
            
            # Interval function for auto-refresh
            def check_auto_refresh(state_data):
                current_time = time.time()
                if state_data["refresh_active"] and (current_time - state_data["last_refresh"]) >= 5:
                    state_data["last_refresh"] = current_time
                    return state_data, True
                return state_data, False
            
            # Auto-refresh logic
            auto_refresh_trigger = gr.Textbox(visible=False)
            
            app.load(
                fn=check_auto_refresh,
                inputs=[state],
                outputs=[state, auto_refresh_trigger],
                every=1  # Check every second if we need to refresh
            )
            
            # When trigger fires, refresh the data
            auto_refresh_trigger.change(
                fn=lambda x: format_status_for_display() if x == "True" else None,
                inputs=[auto_refresh_trigger],
                outputs=[active_downloads_table, queue_table, cpu_indicator, memory_indicator, disk_indicator]
            )
        
        # Help Tab
        with gr.Tab("Help"):
            gr.Markdown("""
            ## Steam Game Downloader Help
            
            ### How to Use
            1. **Setup Tab**: Verify SteamCMD installation and disk space
            2. **Download Games Tab**: Enter a game ID or Steam store URL
            3. Check login requirements (Anonymous for free games, or use credentials)
            4. Click "Download Game" to start or queue the download
            
            ### Finding Game IDs
            - The AppID is the number in the URL of a Steam store page
            - Example: For `https://store.steampowered.com/app/570/Dota_2/` the AppID is `570`
            
            ### Anonymous Login
            - Only works for free-to-play games and demos
            - For paid games, you must provide your Steam credentials
            
            ### Steam Guard
            - If your account has Steam Guard enabled, you may need to enter a code
            - The application will pause and wait for the code to be entered
            
            ### Download Management
            - Only one download runs at a time
            - Additional downloads are queued automatically
            - You can cancel active downloads or remove queued downloads
            
            ### Troubleshooting
            - If downloads fail, try reinstalling SteamCMD
            - Verify you have sufficient disk space
            - For paid games, ensure your credentials are correct
            - Check the logs for detailed error information
            """)
    
    return app

def start_health_check_server():
    health_port = int(os.environ.get("HEALTH_PORT", 7861))
    logging.info(f"Starting health check server on port {health_port}")
    
    config = uvicorn.Config(
        fastapi_app, 
        host="0.0.0.0", 
        port=health_port, 
        log_level="error"
    )
    server = uvicorn.Server(config)
    
    # Run the server in a separate thread
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    logging.info(f"Health check server running at http://0.0.0.0:{health_port}")

def main():
    # Ensure SteamCMD is installed
    if not check_steamcmd():
        logging.info("SteamCMD not found, attempting installation...")
        install_steamcmd()
    
    # Create the Gradio interface
    app_interface = create_improved_gradio_interface()
    
    # Get server settings from environment variables
    port = int(os.environ.get("PORT", 7860))
    server_name = os.environ.get("SERVER_NAME", "0.0.0.0")
    
    # Check if running in Railway
    is_railway = "RAILWAY_STATIC_URL" in os.environ
    
    # Log deployment environment information
    logging.info(f"Starting application on {server_name}:{port}")
    if is_railway:
        railway_url = os.environ.get("RAILWAY_STATIC_URL", "")
        project_name = os.environ.get("RAILWAY_PROJECT_NAME", "")
        logging.info(f"Running in Railway environment. Project: {project_name}")
        if railway_url:
            logging.info(f"Railway provided URL: {railway_url}")
    
    # Start health check server
    start_health_check_server()
    
    # Launch Gradio with consistent settings
    try:
        # Disable share when running in Railway as we'll use Railway's URL
        share = not is_railway

        # Launch the Gradio interface
        launch_info = app_interface.launch(
            server_port=port, 
            server_name=server_name, 
            show_error=True,
            share=share
        )
        
        # Properly capture the share URL
        if hasattr(launch_info, 'share_url') and launch_info.share_url:
            update_share_url(launch_info.share_url)
            logging.info(f"Gradio share URL: {launch_info.share_url}")
        else:
            # Provide Railway URL if available
            if is_railway and railway_url:
                public_url = railway_url
                if not public_url.startswith("http"):
                    public_url = f"https://{public_url}"
                logging.info(f"Application accessible at: {public_url}")
            else:
                # Provide local URL
                local_url = f"http://{server_name}:{port}" if server_name != "0.0.0.0" else f"http://localhost:{port}"
                logging.info(f"Application accessible at: {local_url}")
                logging.info(f"If deployed on a server, check your provider's dashboard for the public URL")
    except Exception as e:
        logging.error(f"Error launching Gradio interface: {str(e)}")
        raise

if __name__ == "__main__":
    main()