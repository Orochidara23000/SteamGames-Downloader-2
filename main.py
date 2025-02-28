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

def download_game(username, password, guard_code, anonymous, game_input, validate_download, debug_mode=False):
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
        
        # Sanitize game name to avoid path issues with spaces and special chars
        sanitized_name = re.sub(r'[^\w\-\.]', '_', game_name)
        install_dir = os.path.join(download_dir, sanitized_name)
        
        if not os.path.exists(download_dir):
            logging.info(f"Creating download directory: {download_dir}")
            os.makedirs(download_dir, exist_ok=True)
        
        # Ensure download directory permissions are correct
        try:
            os.makedirs(install_dir, exist_ok=True)
            # Test write permissions
            test_file = os.path.join(install_dir, '.permission_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            logging.info(f"Successfully verified write permissions to {install_dir}")
        except Exception as e:
            logging.error(f"Permission error with install directory: {str(e)}")
            active_downloads[download_id]["status"] = f"Error: Permission issue with install directory"
            return f"Error: Cannot write to install directory {install_dir}. {str(e)}"
        
        # Prepare download command
        cmd = [get_steamcmd_path()]
        
        if anonymous:
            cmd.extend(["+login", "anonymous"])
        else:
            cmd.extend(["+login", username, password])
            if guard_code:
                cmd.append(guard_code)  # Handle Steam Guard code if provided
        
        # Add download command - use quotes for paths to handle spaces
        cmd.extend([
            "+force_install_dir", install_dir,
            "+app_update", appid
        ])
        
        # Add validation if requested
        if validate_download:
            cmd.append("validate")
            
        # Add verbose flag for debugging
        if debug_mode:
            cmd.append("-debug")
        
        cmd.append("+quit")
        
        # Sanitize command for logging (hide password)
        log_cmd = cmd.copy()
        if not anonymous and password:
            password_index = log_cmd.index(password)
            log_cmd[password_index] = "********"
        
        logging.info(f"Running command: {' '.join(log_cmd)}")
        
        # Set environment variables for SteamCMD
        env = os.environ.copy()
        env["HOME"] = "/app"
        env["STEAMCMD_HOME"] = "/app/steamcmd"
        
        # Start the process with the environment variables
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
        
        # Monitor the process output
        for line in process.stdout:
            logging.info(line.strip())
            # Update download progress based on output if applicable
            # You can implement logic here to parse progress from the output
            
        process.wait()  # Wait for the process to complete
        
        # Update the status of the download
        active_downloads[download_id]["status"] = "Completed"
        logging.info(f"Download for App ID {appid} completed successfully.")
        return f"Download for App ID {appid} completed successfully."
    
    except Exception as e:
        logging.error(f"Error during download process: {str(e)}")
        active_downloads[download_id]["status"] = f"Error: {str(e)}"
        return f"Error during download process: {str(e)}"

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

def create_gradio_interface():
    with gr.Blocks(title="Steam Game Downloader") as app:
        gr.Markdown("# Steam Game Downloader")
        gr.Markdown("Download Steam games directly using SteamCMD")
        
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
                    gr.Markdown("### Download Settings")
                    download_path = gr.Textbox(label="Download Location", value=get_default_download_location())
                    disk_space = gr.Textbox(
                        label="Available Disk Space",
                        value=f"{psutil.disk_usage(get_default_download_location()).free / (1024**3):.2f} GB",
                        interactive=False
                    )
                    
                    refresh_space_btn = gr.Button("Refresh Disk Space")
                    
                    def update_disk_space():
                        return f"{psutil.disk_usage(get_default_download_location()).free / (1024**3):.2f} GB"
                    
                    refresh_space_btn.click(
                        fn=update_disk_space,
                        outputs=[disk_space]
                    )
        
        with gr.Tab("Download Games"):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Game Details")
                    game_input = gr.Textbox(label="Game ID or Steam Store URL")
                    check_game_btn = gr.Button("Check Game")
                    game_info = gr.JSON(label="Game Information")
                    
                    check_game_btn.click(
                        fn=get_game_details,
                        inputs=[game_input],
                        outputs=[game_info]
                    )
                
                with gr.Column():
                    gr.Markdown("### Login Information")
                    anonymous_login = gr.Checkbox(label="Use Anonymous Login (for free games only)", value=True)
                    username = gr.Textbox(label="Steam Username", interactive=True)
                    password = gr.Textbox(label="Steam Password", type="password", interactive=True)
                    guard_code = gr.Textbox(label="Steam Guard Code (if required)", interactive=True)
                    validate_download = gr.Checkbox(label="Validate Download", value=True)
                    
                    def update_login_fields(anonymous):
                        return [
                            gr.Textbox.update(interactive=not anonymous),
                            gr.Textbox.update(interactive=not anonymous),
                            gr.Textbox.update(interactive=not anonymous)
                        ]
                    
                    anonymous_login.change(
                        fn=update_login_fields,
                        inputs=[anonymous_login],
                        outputs=[username, password, guard_code]
                    )
            
            with gr.Row():
                download_btn = gr.Button("Download Game", variant="primary")
                download_output = gr.Textbox(label="Download Result", interactive=False)
            
            download_btn.click(
                fn=queue_download,
                inputs=[username, password, guard_code, anonymous_login, game_input, validate_download],
                outputs=[download_output]
            )
        
        with gr.Tab("Downloads"):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Active Downloads")
                    active_downloads_json = gr.JSON(label="Active Downloads")
                    download_id = gr.Textbox(label="Download ID to Cancel")
                    cancel_btn = gr.Button("Cancel Download")
                    cancel_output = gr.Textbox(label="Cancel Result", interactive=False)
                
                with gr.Column():
                    gr.Markdown("### Download Queue")
                    queue_json = gr.JSON(label="Queue")
                    queue_position = gr.Number(label="Queue Position to Remove", precision=0)
                    remove_btn = gr.Button("Remove from Queue")
                    remove_output = gr.Textbox(label="Remove Result", interactive=False)
            
            refresh_btn = gr.Button("Refresh Status")
            system_stats = gr.JSON(label="System Statistics")
            
            def update_status():
                status = get_download_status()
                return [
                    status["active"],
                    status["queue"],
                    status["system"]
                ]
            
            refresh_btn.click(
                fn=update_status,
                outputs=[active_downloads_json, queue_json, system_stats]
            )
            
            cancel_btn.click(
                fn=cancel_download,
                inputs=[download_id],
                outputs=[cancel_output]
            )
            
            remove_btn.click(
                fn=remove_from_queue,
                inputs=[queue_position],
                outputs=[remove_output]
            )
            
            # Replace the problematic line with a proper interval
            gr.Markdown("Status auto-refreshes every 5 seconds")
            
            # Set up auto-refresh with JavaScript instead
            app.load(
                update_status,
                inputs=None,
                outputs=[active_downloads_json, queue_json, system_stats],
                every=5  # Refresh every 5 seconds
            )
        
        with gr.Tab("Help"):
            gr.Markdown("""
            ## Steam Game Downloader Help
            
            ### How to Use
            1. **Setup Tab**: Install SteamCMD if not already installed
            2. **Download Games Tab**: Enter a game ID or Steam store URL
            3. Choose login method (Anonymous for free games, or with credentials)
            4. Click "Download Game" to start or queue the download
            
            ### Finding Game IDs
            - The AppID is the number in the URL of a Steam store page
            - Example: For https://store.steampowered.com/app/570/Dota_2/ the AppID is 570
            
            ### Anonymous Login
            - Only works for free-to-play games and demos
            - For paid games, you must provide your Steam credentials
            
            ### Steam Guard
            - If your account has Steam Guard enabled, you may need to enter a code
            - The application will pause and wait for the code to be entered
            
            ### Download Management
            - Only one download runs at a time
            - Additional downloads are queued
            - You can cancel active downloads or remove queued downloads
            
            ### Troubleshooting
            - If downloads fail, try reinstalling SteamCMD
            - Verify you have sufficient disk space
            - For paid games, ensure your credentials are correct
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