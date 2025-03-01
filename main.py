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
from typing import Dict, List, Any, Tuple, Optional
from queue import Queue
import signal
import uuid
import math
import concurrent.futures

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

logger = logging.getLogger(__name__)
logger.info(f"Starting Steam Downloader application (PID: {os.getpid()})")

# Global variables for download management with better typing
active_downloads: Dict[str, dict] = {}
download_queue: List[dict] = []
queue_lock = threading.Lock()
download_history: List[dict] = []  # Track completed downloads
MAX_HISTORY_SIZE = 50  # Maximum entries in download history

# Environment variable handling for containerization
STEAM_DOWNLOAD_PATH = os.environ.get('STEAM_DOWNLOAD_PATH', '/data/downloads')

# Global variable to store the share URL
SHARE_URL = ""

# Define your FastAPI app here
fastapi_app = FastAPI()

@fastapi_app.get("/status")
def get_status():
    return {"status": "running"}

@fastapi_app.get("/downloads")
def api_get_downloads():
    return {
        "active": active_downloads,
        "queue": download_queue,
        "history": download_history
    }

def update_share_url(share_url):
    global SHARE_URL
    SHARE_URL = share_url
    logger.info(f"Gradio share URL updated: {share_url}")

# ========================
# PATH AND SYSTEM UTILITIES
# ========================

def get_default_download_location():
    if STEAM_DOWNLOAD_PATH:
        logger.info(f"Using environment variable for download path: {STEAM_DOWNLOAD_PATH}")
        return STEAM_DOWNLOAD_PATH
    if platform.system() == "Windows":
        path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
    elif platform.system() == "Darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SteamLibrary")
    else:
        path = os.path.join(os.path.expanduser("~"), "SteamLibrary")
    logger.info(f"Using platform-specific download path: {path}")
    return path

def get_steamcmd_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    steamcmd_dir = os.path.join(base_dir, "steamcmd")
    if platform.system() == "Windows":
        path = os.path.join(steamcmd_dir, "steamcmd.exe")
    else:
        path = os.path.join(steamcmd_dir, "steamcmd.sh")
    logger.info(f"SteamCMD path: {path}")
    return path

def ensure_directory_exists(directory):
    """Create directory if it doesn't exist."""
    if not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
            logger.info(f"Created directory: {directory}")
            return True
        except Exception as e:
            logger.error(f"Failed to create directory {directory}: {str(e)}")
            return False
    return True

# ========================
# STEAMCMD INSTALLATION
# ========================

def check_steamcmd():
    steamcmd_path = get_steamcmd_path()
    is_installed = os.path.exists(steamcmd_path)
    result = "SteamCMD is installed." if is_installed else "SteamCMD is not installed."
    logger.info(f"SteamCMD check: {result}")
    return result

def install_steamcmd():
    if platform.system() == "Windows":
        return install_steamcmd_windows()
    else:
        return install_steamcmd_linux()

def install_steamcmd_linux():
    logger.info("Installing SteamCMD for Linux")
    steamcmd_install_dir = "/app/steamcmd"
    steamcmd_path = os.path.join(steamcmd_install_dir, "steamcmd.sh")
    
    # Remove existing SteamCMD directory if it exists
    if os.path.exists(steamcmd_install_dir):
        logger.info(f"Removing existing SteamCMD directory: {steamcmd_install_dir}")
        shutil.rmtree(steamcmd_install_dir)
    
    # Re-create the SteamCMD directory before downloading
    ensure_directory_exists(steamcmd_install_dir)
    
    try:
        # Download and extract SteamCMD
        logger.info("Downloading SteamCMD from https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz")
        response = requests.get("https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz", timeout=30)
        response.raise_for_status()  # Raise an exception for HTTP errors
        
        tarball_path = os.path.join(steamcmd_install_dir, "steamcmd_linux.tar.gz")
        
        with open(tarball_path, "wb") as f:
            f.write(response.content)
        
        logger.info("Extracting SteamCMD tar.gz file")
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=steamcmd_install_dir)
        
        # Make the steamcmd.sh executable
        os.chmod(steamcmd_path, 0o755)
        logger.info("Made steamcmd.sh executable")
        
        # Run SteamCMD for the first time to complete installation
        logger.info("Running SteamCMD for the first time to complete installation")
        process = subprocess.run([steamcmd_path, "+quit"], 
                               check=True, 
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               text=True)
        
        if process.returncode == 0:
            logger.info("SteamCMD initial run completed successfully")
            return "SteamCMD installed successfully.", steamcmd_path
        else:
            logger.error(f"SteamCMD initial run failed: {process.stderr}")
            return f"Error: SteamCMD installation failed. {process.stderr}", ""
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading SteamCMD: {str(e)}")
        return f"Error: Failed to download SteamCMD. {str(e)}", ""
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running SteamCMD: {str(e)}")
        return f"Error: Failed to run SteamCMD. {str(e)}", ""
    except Exception as e:
        logger.error(f"Unexpected error during SteamCMD installation: {str(e)}")
        return f"Error: Unexpected error during installation. {str(e)}", ""

def install_steamcmd_windows():
    logger.info("Installing SteamCMD for Windows")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    steamcmd_dir = os.path.join(base_dir, "steamcmd")
    steamcmd_path = os.path.join(steamcmd_dir, "steamcmd.exe")
    
    # Remove existing SteamCMD directory if it exists
    if os.path.exists(steamcmd_dir):
        logger.info(f"Removing existing SteamCMD directory: {steamcmd_dir}")
        shutil.rmtree(steamcmd_dir)
    
    # Create steamcmd directory
    ensure_directory_exists(steamcmd_dir)
    
    try:
        # Download SteamCMD
        logger.info("Downloading SteamCMD from https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip")
        response = requests.get("https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip", timeout=30)
        response.raise_for_status()
        
        zip_path = os.path.join(steamcmd_dir, "steamcmd.zip")
        
        with open(zip_path, "wb") as f:
            f.write(response.content)
        
        # Extract SteamCMD
        logger.info("Extracting SteamCMD zip file")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(steamcmd_dir)
        
        # Run SteamCMD for the first time to complete installation
        logger.info("Running SteamCMD for the first time to complete installation")
        subprocess.run([steamcmd_path, "+quit"], check=True)
        
        logger.info("SteamCMD installed successfully")
        return "SteamCMD installed successfully.", steamcmd_path
    
    except Exception as e:
        logger.error(f"Error during SteamCMD installation: {str(e)}")
        return f"Error: {str(e)}", ""

# ========================
# GAME IDENTIFICATION & VALIDATION
# ========================

def parse_game_input(input_str):
    """Extract a Steam AppID from user input."""
    logger.info(f"Parsing game input: {input_str}")
    if not input_str or input_str.strip() == "":
        logger.warning("Empty game input provided")
        return None
    
    # If input is just a number, assume it's an AppID
    if input_str.strip().isdigit():
        logger.info(f"Input is a valid App ID: {input_str}")
        return input_str.strip()
    
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
            logger.info(f"Extracted App ID {appid} from URL: {input_str}")
            return appid
    
    logger.error("Failed to extract App ID from input")
    return None

def validate_appid(appid: str) -> Tuple[bool, Any]:
    """Validate if an AppID exists on Steam and return game information."""
    logger.info(f"Validating App ID: {appid}")
    
    def _fetch_game_info():
        try:
            # Check if app exists via Steam API
            url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
            logger.info(f"Querying Steam API: {url}")
            
            # Add a shorter timeout to prevent hanging
            response = requests.get(url, timeout=3)
            logger.info(f"Received response from Steam API with status: {response.status_code}")
            
            if not response.ok:
                logger.error(f"Steam API request failed with status: {response.status_code}")
                return False, f"Steam API request failed with status: {response.status_code}"
            
            data = response.json()
            
            if not data or not data.get(appid):
                logger.error(f"Invalid response from Steam API for App ID {appid}")
                return False, "Invalid response from Steam API"
            
            if not data.get(appid, {}).get('success', False):
                logger.warning(f"Game not found for App ID: {appid}")
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
            
            logger.info(f"Game found: {game_info['name']} (Free: {game_info['is_free']})")
            return True, game_info
            
        except requests.exceptions.Timeout:
            logger.error(f"Timeout while validating App ID {appid}")
            return False, "Timeout while connecting to Steam API. Please try again later."
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error while validating App ID {appid}")
            return False, "Connection error when contacting Steam API. Please check your internet connection."
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error while validating App ID {appid}: {str(e)}")
            return False, f"Request error: {str(e)}"
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON response from Steam API for App ID {appid}")
            return False, "Invalid response from Steam API"
        except Exception as e:
            logger.error(f"Validation error for App ID {appid}: {str(e)}", exc_info=True)
            return False, f"Validation error: {str(e)}"
    
    # First, try to check the cache - if we've already fetched this game info
    cache_file = os.path.join(CACHE_DIR, f"game_{appid}.json")
    if os.path.exists(cache_file):
        try:
            logger.info(f"Found cached info for App ID {appid}")
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
                return True, cached_data
        except Exception as e:
            logger.warning(f"Failed to read cached data for App ID {appid}: {e}")
            # Continue to live API fetch if cache reading fails
    
    # Use thread executor with timeout to avoid hanging
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit task to executor and wait with timeout
            future = executor.submit(_fetch_game_info)
            try:
                # Wait for result with a strict timeout
                result = future.result(timeout=6)  # 6 second timeout
                return result
            except concurrent.futures.TimeoutError:
                logger.error(f"API request timed out for App ID {appid} (thread timeout)")
                return False, "API request timed out. Steam servers may be unavailable."
    except Exception as e:
        logger.error(f"Unexpected error in thread execution: {str(e)}", exc_info=True)
        return False, f"Error: {str(e)}"

# ========================
# DOWNLOAD MANAGEMENT 
# ========================

def parse_progress(line: str) -> dict:
    """Extract progress information from SteamCMD output lines."""
    try:
        # Improved progress parsing with more information
        line_lower = line.lower()
        result = {}
        
        # Debug the line we're parsing
        if "progress" in line_lower or "download" in line_lower or "%" in line_lower:
            logger.debug(f"Parsing progress line: {line}")
        
        # Look for progress percentage patterns
        progress_patterns = [
            r'(?:progress|update|download):\s*(?:.*?)(\d+\.?\d*)%',  # Matches various progress formats
            r'(\d+\.?\d*)%\s*complete',
            r'progress:\s+(\d+\.?\d*)\s*%',
            r'(\d+)\s+of\s+(\d+)\s+MB\s+\((\d+\.?\d*)%\)',  # Matches current/total size
            r'(\d+\.?\d*)%', # Just a percentage on its own
            r'progress:\s+(\d+\.?\d*)',  # Progress without % sign
            r'downloading\s+(\d+\.?\d*)%'  # "Downloading x%"
        ]
        
        for pattern in progress_patterns:
            progress_match = re.search(pattern, line_lower)
            if progress_match:
                if len(progress_match.groups()) == 3:  # Pattern with current/total size
                    current = int(progress_match.group(1))
                    total = int(progress_match.group(2))
                    progress = float(progress_match.group(3))
                    result.update({
                        "progress": progress,
                        "current_size": current,
                        "total_size": total,
                        "unit": "MB"
                    })
                else:
                    progress = float(progress_match.group(1))
                    result.update({"progress": progress})
                logger.debug(f"Found progress: {progress}%")
                break
        
        # Look for download speed
        speed_patterns = [
            r'(\d+\.?\d*)\s*(KB|MB|GB)/s',
            r'at\s+(\d+\.?\d*)\s*(KB|MB|GB)/s',
            r'(\d+\.?\d*)\s*(KB|MB|GB)\s+per\s+second',
            r'speed:\s+(\d+\.?\d*)\s*(KB|MB|GB)'
        ]
        
        for pattern in speed_patterns:
            speed_match = re.search(pattern, line_lower)
            if speed_match:
                speed = float(speed_match.group(1))
                unit = speed_match.group(2)
                result.update({"speed": speed, "speed_unit": unit})
                logger.debug(f"Found speed: {speed} {unit}/s")
                break
        
        # Look for ETA
        eta_patterns = [
            r'ETA\s+(\d+m\s*\d+s)',
            r'ETA\s+(\d+:\d+:\d+)',
            r'ETA:\s+(\d+)\s+seconds',
            r'estimated\s+time\s+remaining:\s+(.+?)\s'
        ]
        
        for pattern in eta_patterns:
            eta_match = re.search(pattern, line_lower)
            if eta_match:
                result.update({"eta": eta_match.group(1)})
                logger.debug(f"Found ETA: {eta_match.group(1)}")
                break
        
        # Look for total size in various formats
        size_patterns = [
            r'(?:size|total):\s*(\d+\.?\d*)\s*(\w+)',
            r'downloading\s+(\d+\.?\d*)\s*(\w+)',
            r'download of\s+(\d+\.?\d*)\s*(\w+)',
            r'(\d+\.?\d*)\s*(\w+)\s+remaining'
        ]
        
        for pattern in size_patterns:
            size_match = re.search(pattern, line_lower)
            if size_match:
                size = float(size_match.group(1))
                unit = size_match.group(2)
                result.update({"total_size": size, "unit": unit})
                logger.debug(f"Found size: {size} {unit}")
                break
        
        # Check for success messages
        success_patterns = [
            r'success!\s+app\s+[\'"]?(\d+)[\'"]',
            r'fully installed',
            r'download\s+complete',
            r'installation\s+complete',
            r'complete!'
        ]
        
        for pattern in success_patterns:
            success_match = re.search(pattern, line_lower)
            if success_match:
                result.update({"success": True})
                logger.debug("Found success message")
                break
        
        # Check for error messages
        error_patterns = [
            r'error!\s+(.*)',
            r'failed\s+(.*)',
            r'invalid password',
            r'invalid user name',
            r'account logon denied',
            r'need two-factor code',
            r'rate limited',
            r'no subscription',
            r'invalid platform'
        ]
        
        for pattern in error_patterns:
            error_match = re.search(pattern, line_lower)
            if error_match:
                result.update({"error": True, "error_message": line})
                logger.debug(f"Found error: {line}")
                break
        
        return result
    
    except Exception as e:
        logger.error(f"Error parsing progress line: {line}. Error: {str(e)}")
        return {}

def process_download_queue():
    """Start the next download in queue if no active downloads are running."""
    with queue_lock:
        if download_queue and len(active_downloads) == 0:
            next_download = download_queue.pop(0)
            thread = threading.Thread(target=next_download["function"], args=next_download["args"])
            thread.daemon = True
            thread.start()
            logger.info(f"Started new download thread. Remaining in queue: {len(download_queue)}")

def verify_installation(appid, install_path):
    """Verify that a game was correctly downloaded."""
    logger.info(f"Verifying installation for App ID: {appid} at path: {install_path}")
    
    if not os.path.exists(install_path):
        logger.error(f"Installation path does not exist: {install_path}")
        return False
    
    cmd_args = [get_steamcmd_path()]
    cmd_args.extend([
        "+login", "anonymous", 
        "+force_install_dir", install_path,
        "+app_update", appid, "validate", 
        "+quit"
    ])
    
    try:
        logger.info(f"Running verification command: {' '.join(cmd_args)}")
        process = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        verification_successful = False
        output_lines = []
        
        for line in process.stdout:
            line = line.strip()
            output_lines.append(line)
            logger.debug(f"Verification output: {line}")
            
            if f"Success! App '{appid}' fully installed" in line or "Fully installed" in line:
                verification_successful = True
        
        process.wait(timeout=300)  # Add timeout to prevent hanging
        
        if verification_successful:
            logger.info(f"Verification successful for App ID: {appid}")
            return True
        else:
            logger.warning(f"Verification failed for App ID: {appid}")
            if output_lines:
                logger.warning(f"Last 5 output lines: {output_lines[-5:]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Verification process timed out for App ID: {appid}")
        return False
    except Exception as e:
        logger.error(f"Error during verification of App ID {appid}: {str(e)}")
        return False

def download_game(username, password, guard_code, anonymous, game_input, validate_download=True):
    """Main function to download a game from Steam using SteamCMD."""
    # Generate a unique download ID
    download_id = str(uuid.uuid4())
    appid = parse_game_input(game_input)
    
    # Log the download request
    logger.info(f"Starting download for AppID: {appid} (Anonymous: {anonymous}, Validate: {validate_download})")
    
    # Validate AppID and get game info
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        logger.error(f"Invalid AppID: {appid}. Error: {game_info}")
        return f"Error: {game_info}"
    
    game_name = game_info.get('name', f"Game {appid}")
    is_free = game_info.get('is_free', False)
    
    # Define special case free-to-play games that need special handling
    special_f2p_games = {
        "230410": {  # Warframe
            "name": "Warframe",
            "requires_account": True,  # Requires Steam account despite being free
            "platform": "windows"      # Requires Windows platform
        },
        "570": {  # Dota 2
            "name": "Dota 2",
            "requires_account": True,
            "platform": "windows"
        },
        "440": {  # Team Fortress 2
            "name": "Team Fortress 2",
            "requires_account": True,
            "platform": "windows"
        }
        # Add more special cases as needed
    }
    
    # Check if this is a special case game that requires credentials
    is_special_game = appid in special_f2p_games
    requires_account = is_special_game and special_f2p_games[appid].get("requires_account", False)
    specific_platform = is_special_game and special_f2p_games[appid].get("platform", None)
    
    # For special games requiring an account, override the anonymous setting
    if requires_account:
        if not username or not password:
            logger.error(f"Username and password required for {game_name} despite being free-to-play")
            return "Error: This free game requires a Steam account. Please provide your Steam credentials."
        anonymous = False
        logger.info(f"Using account login for special free-to-play game: {game_name}")
    # For normal free games, use anonymous login
    elif is_free:
        anonymous = True
        logger.info(f"Using anonymous login for standard free game: {game_name}")
    # For paid games, ensure we have credentials
    elif not anonymous and (not username or not password):
        logger.error(f"Username and password required for non-free game: {game_name}")
        return "Error: Username and password are required for non-free games."
    
    # Set up download directory
    download_path = os.path.join(get_default_download_location(), game_name.replace(" ", "_"))
    ensure_directory_exists(download_path)
    
    logger.info(f"Download path: {download_path}")
    
    # Prepare SteamCMD command
    cmd_args = [get_steamcmd_path()]
    
    # Force Windows platform if needed
    if specific_platform:
        logger.info(f"Forcing platform to {specific_platform} for {game_name}")
        cmd_args.append(f"+@sSteamCmdForcePlatformType {specific_platform}")
    
    # Add login parameters
    if anonymous:
        cmd_args.extend(["+login", "anonymous"])
    else:
        cmd_args.extend(["+login", username, password])
        if guard_code:
            # Steam Guard code needs special handling in the command
            cmd_args[-1] = f"{password} {guard_code}"
    
    # Add standard installation commands
    cmd_args.extend([
        "+force_install_dir", download_path,
        "+app_update", appid
    ])
    
    # For special games, add license request if needed
    if is_special_game and not anonymous:
        cmd_args.extend(["+app_license_request", appid])
    
    # Add validation if requested
    if validate_download:
        cmd_args.append("validate")
    
    # Add quit command
    cmd_args.append("+quit")
    
    # Log the command without using nested f-strings
    cmd_str = " ".join([arg if " " not in arg else f'"{arg}"' for arg in cmd_args])
    logger.info(f"SteamCMD command: {cmd_str}")
    
    # Add to active downloads with initial status
    with queue_lock:
        active_downloads[download_id] = {
            "appid": appid,
            "name": game_name,
            "status": "Initializing",
            "progress": 0.0,
            "speed": "0 KB/s",
            "eta": "Unknown",
            "start_time": datetime.now(),
            "path": download_path,
            "anonymous": anonymous,
            "command": cmd_str
        }
    
    logger.info(f"[{game_name}] Download ID: {download_id}")
    logger.info(f"[{game_name}] Download starting...")
    
    try:
        # Start SteamCMD process
        process = subprocess.Popen(
            cmd_args, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Update download status to "In Progress"
        with queue_lock:
            if download_id in active_downloads:
                active_downloads[download_id]["status"] = "In Progress"
                active_downloads[download_id]["process_pid"] = process.pid
        
        logger.info(f"[{game_name}] Download started (PID: {process.pid})")
        
        # Set up a timeout for stalled downloads
        last_progress_time = time.time()
        last_progress_value = 0
        stall_timeout = 300  # 5 minutes with no progress is considered stalled
        
        # Read output line by line
        output_buffer = []
        download_error = False
        error_message = ""
        last_progress_log = 0  # To avoid logging every tiny progress update
        
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
                
            output_buffer.append(line)
            
            # Only log important lines to avoid flooding
            if "error" in line.lower() or "warning" in line.lower() or "fail" in line.lower():
                logger.warning(f"[{game_name}] {line}")
            elif "progress" in line.lower() or "download" in line.lower() or "%" in line.lower():
                logger.debug(f"[{game_name}] {line}")
            
            # Parse progress from output
            progress_info = parse_progress(line)
            
            # Check for error conditions
            if "error" in progress_info:
                download_error = True
                error_message = progress_info.get("error_message", "Unknown error")
                logger.error(f"[{game_name}] Download error: {error_message}")
                break
            
            # Update the download status with parsed progress info
            with queue_lock:
                if download_id in active_downloads:
                    # Update progress if available
                    if "progress" in progress_info:
                        progress_value = progress_info["progress"]
                        active_downloads[download_id]["progress"] = progress_value
                        
                        # Update last progress time if there's actual progress
                        if progress_value > last_progress_value:
                            last_progress_time = time.time()
                            
                            # Only log every 5% or more change to avoid spam
                            if progress_value - last_progress_log >= 5 or progress_value == 100:
                                logger.info(f"[{game_name}] Download progress: {progress_value:.1f}%")
                                last_progress_log = progress_value
                            
                            last_progress_value = progress_value
                    
                    # Update download speed if available
                    if "speed" in progress_info and "speed_unit" in progress_info:
                        speed_text = f"{progress_info['speed']} {progress_info['speed_unit']}/s"
                        active_downloads[download_id]["speed"] = speed_text
                        logger.info(f"[{game_name}] Download speed: {speed_text}")
                    
                    # Update ETA if available
                    if "eta" in progress_info:
                        active_downloads[download_id]["eta"] = progress_info["eta"]
                        logger.info(f"[{game_name}] ETA: {progress_info['eta']}")
                    
                    # Update total size if available
                    if "total_size" in progress_info and "unit" in progress_info:
                        size_text = f"{progress_info['total_size']} {progress_info['unit']}"
                        active_downloads[download_id]["total_size"] = size_text
                        logger.info(f"[{game_name}] Total size: {size_text}")
                    
                    # Update current size if available
                    if "current_size" in progress_info and "unit" in progress_info:
                        current_text = f"{progress_info['current_size']} {progress_info['unit']}"
                        active_downloads[download_id]["size_downloaded"] = current_text
                        logger.info(f"[{game_name}] Downloaded: {current_text}")
            
            # Check for success message
            if "success" in progress_info:
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["progress"] = 100.0
                        active_downloads[download_id]["status"] = "Complete"
                logger.info(f"[{game_name}] Download completed successfully!")
            
            # Check if download has stalled
            current_time = time.time()
            if current_time - last_progress_time > stall_timeout:
                logger.warning(f"[{game_name}] Download appears to be stalled - no progress for {stall_timeout} seconds")
                
                # Add a stalled status to the download
                with queue_lock:
                    if download_id in active_downloads:
                        # Only mark as stalled if we're not at 100%
                        if active_downloads[download_id]["progress"] < 100:
                            active_downloads[download_id]["status"] = "Stalled"
        
        # Wait for process to complete with timeout
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # If process doesn't exit gracefully, terminate it
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.warning(f"[{game_name}] SteamCMD process terminated after timeout")
        
        # Check for download error
        if download_error:
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = "Failed"
                    active_downloads[download_id]["error"] = error_message
                    
                    # Move to download history
                    download_history.insert(0, {
                        "id": download_id,
                        "appid": appid,
                        "name": game_name,
                        "status": "Failed",
                        "error": error_message,
                        "start_time": active_downloads[download_id]["start_time"],
                        "end_time": datetime.now(),
                        "duration": str(datetime.now() - active_downloads[download_id]["start_time"]).split('.')[0]
                    })
                    
                    # Remove from active downloads
                    del active_downloads[download_id]
            
            logger.error(f"[{game_name}] Download failed: {error_message}")
            return f"Error: Download failed. {error_message}"
        
        # Check process exit code
        if process.returncode != 0:
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = "Failed"
                    active_downloads[download_id]["error"] = f"SteamCMD exited with code {process.returncode}"
                    
                    # Move to download history
                    download_history.insert(0, {
                        "id": download_id,
                        "appid": appid,
                        "name": game_name,
                        "status": "Failed",
                        "error": f"SteamCMD exited with code {process.returncode}",
                        "start_time": active_downloads[download_id]["start_time"],
                        "end_time": datetime.now(),
                        "duration": str(datetime.now() - active_downloads[download_id]["start_time"]).split('.')[0]
                    })
                    
                    # Remove from active downloads
                    del active_downloads[download_id]
            
            logger.error(f"[{game_name}] SteamCMD process exited with code {process.returncode}")
            return f"Error: SteamCMD exited with code {process.returncode}"
        
        # Validate download if requested
        if validate_download:
            logger.info(f"[{game_name}] Validating download...")
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = "Validating"
            
            validation_success = verify_installation(appid, download_path)
            
            if validation_success:
                logger.info(f"[{game_name}] Validation successful!")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["status"] = "Valid"
            else:
                logger.warning(f"[{game_name}] Validation failed")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["status"] = "Invalid"
        else:
            logger.info(f"[{game_name}] Validation skipped")
        
        # Calculate download stats
        end_time = datetime.now()
        duration = end_time - active_downloads[download_id]["start_time"]
        
        # Move from active downloads to history
        with queue_lock:
            if download_id in active_downloads:
                download_info = active_downloads[download_id].copy()
                download_info["end_time"] = end_time
                download_info["duration"] = str(duration).split('.')[0]  # Remove microseconds
                
                # Add to download history
                download_history.insert(0, download_info)
                
                # Keep history size limited
                if len(download_history) > MAX_HISTORY_SIZE:
                    download_history.pop()
                
                # Remove from active downloads
                del active_downloads[download_id]
        
        # Process next download in queue
        process_download_queue()
        
        logger.info(f"[{game_name}] Download process completed! Game installed to: {download_path}")
        return f"Download completed for {game_name}"
    
    except Exception as e:
        # Handle any unexpected exceptions
        logger.error(f"[{game_name}] Unexpected error: {str(e)}")
        
        # Update download status to reflect the error
        with queue_lock:
            if download_id in active_downloads:
                active_downloads[download_id]["status"] = "Failed"
                active_downloads[download_id]["error"] = str(e)
                
                # Move to download history
                download_history.insert(0, {
                    "id": download_id,
                    "appid": appid,
                    "name": game_name,
                    "status": "Failed",
                    "error": str(e),
                    "start_time": active_downloads[download_id]["start_time"],
                    "end_time": datetime.now(),
                    "duration": str(datetime.now() - active_downloads[download_id]["start_time"]).split('.')[0]
                })
                
                # Remove from active downloads
                del active_downloads[download_id]
        
        # Process next download in queue
        process_download_queue()
        
        return f"Error: {str(e)}"

def queue_download(username, password, guard_code, anonymous, game_input, validate=True):
    """Add a download to the queue or start it immediately if no downloads are active."""
    logger.info(f"Queueing download for game: {game_input} (Anonymous: {anonymous})")
    
    # Validate login inputs for non-anonymous downloads
    if not anonymous and (not username or not password):
        error_msg = "Error: Username and password are required for non-anonymous downloads."
        logger.error(error_msg)
        return error_msg
    
    # Parse the game input to get an AppID
    appid = parse_game_input(game_input)
    if not appid:
        error_msg = "Invalid game ID or URL. Please enter a valid Steam game ID or store URL."
        logger.error(error_msg)
        return error_msg
    
    # Validate the AppID
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        error_msg = f"Invalid AppID: {appid}. Error: {game_info}"
        logger.error(error_msg)
        return error_msg
    
    # Check if game is free - override anonymous setting if so
    is_free = game_info.get('is_free', False)
    if is_free and not anonymous:
        logger.info(f"Game {game_info.get('name')} is free - using anonymous login regardless of setting")
        anonymous = True
    
    # Check Steam Guard requirements
    needs_guard = not anonymous and not guard_code
    if needs_guard:
        logger.info(f"Non-anonymous login requires Steam Guard code")
        # This will be handled by download_game if Steam Guard is needed
    
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
                "args": (username, password, guard_code, anonymous, appid, validate),
                "appid": appid,
                "name": game_info.get('name', 'Unknown Game'),
                "queued_time": datetime.now()
            })
            position = len(download_queue)
            return f"Download for {game_info.get('name', 'Unknown Game')} (AppID: {appid}) queued at position {position}"

def get_download_status():
    """Get the current status of all downloads and queue for display in the UI."""
    # Get current downloads and queue
    active = []
    with queue_lock:
        for id, info in active_downloads.items():
            # Calculate runtime properly
            start_time = info.get("start_time", datetime.now())
            runtime = str(datetime.now() - start_time).split('.')[0]  # Remove microseconds
            
            # Ensure we have values for all fields
            active.append({
                "id": id,
                "name": info.get("name", "Unknown"),
                "appid": info.get("appid", "Unknown"),
                "progress": info.get("progress", 0.0),
                "status": info.get("status", "Unknown"),
                "eta": info.get("eta", "Unknown"),
                "runtime": runtime,
                "speed": info.get("speed", "0 KB/s"),
                "size_downloaded": info.get("size_downloaded", "Unknown"),
                "total_size": info.get("total_size", "Unknown")
            })
    
    # Enhanced queue information
    queue = []
    for i, download in enumerate(download_queue):
        appid = download["args"][4]
        queue_item = {
            "position": i + 1,
            "appid": appid,
            "name": download.get("name", "Unknown Game"),
            "size": "Unknown",  # Would need to get from game_info
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
    
    # Add history of completed downloads
    history = []
    for i, download in enumerate(download_history[:10]):  # Show only last 10
        history.append([
            download.get("id", f"hist_{i}")[:8],  # Shorten ID
            download.get("name", "Unknown"),
            download.get("status", "Unknown"),
            download.get("duration", "Unknown"),
            download.get("end_time", datetime.now()).strftime("%Y-%m-%d %H:%M:%S") if isinstance(download.get("end_time"), datetime) else "Unknown"
        ])
    
    return {
        "active": active,
        "queue": queue,
        "system": system,
        "history": history
    }

def cancel_download(download_id):
    """Cancel an active download and remove it from the list."""
    logger.info(f"Attempting to cancel download: {download_id}")
    
    if download_id in active_downloads:
        try:
            # Get process ID if available
            process_pid = active_downloads[download_id].get("process_pid")
            
            if process_pid:
                try:
                    # Kill the process
                    process = psutil.Process(process_pid)
                    logger.info(f"Terminating process {process_pid} for download {download_id}")
                    process.terminate()
                    
                    # Wait up to 5 seconds for graceful termination
                    process.wait(timeout=5)
                    
                    # If still running, kill forcefully
                    if process.is_running():
                        logger.warning(f"Process {process_pid} did not terminate gracefully, killing forcefully")
                        process.kill()
                except psutil.NoSuchProcess:
                    logger.warning(f"Process {process_pid} not found")
                except Exception as e:
                    logger.error(f"Error terminating process {process_pid}: {str(e)}")
            
            # Update download status
            with queue_lock:
                download_info = active_downloads[download_id].copy()
                download_info["status"] = "Cancelled"
                download_info["end_time"] = datetime.now()
                
                # Add to download history
                download_history.insert(0, download_info)
                
                # Remove from active downloads
                del active_downloads[download_id]
            
            # Process next download in queue
            process_download_queue()
            
            return f"Download {download_id} cancelled successfully"
        except Exception as e:
            logger.error(f"Error cancelling download {download_id}: {str(e)}")
            return f"Error cancelling download: {str(e)}"
    else:
        return f"Download {download_id} not found in active downloads"

def remove_from_queue(position):
    """Remove a download from the queue at the specified position."""
    try:
        position = int(position)
        logger.info(f"Attempting to remove download from queue position: {position}")
        
        with queue_lock:
            if 1 <= position <= len(download_queue):
                removed = download_queue.pop(position - 1)
                return f"Removed download from queue position {position}"
            else:
                return f"Invalid queue position: {position}"
    except ValueError:
        return "Position must be a number"
    except Exception as e:
        logger.error(f"Error removing download from queue: {str(e)}")
        return f"Error: {str(e)}"

def reorder_queue(from_position, to_position):
    """Move a download in the queue from one position to another."""
    try:
        from_position = int(from_position)
        to_position = int(to_position)
        
        with queue_lock:
            if 1 <= from_position <= len(download_queue) and 1 <= to_position <= len(download_queue):
                # Convert to 0-based index
                from_idx = from_position - 1
                to_idx = to_position - 1
                
                # Get the item to move
                item = download_queue.pop(from_idx)
                
                # Insert at the new position
                download_queue.insert(to_idx, item)
                
                logger.info(f"Moved download from position {from_position} to {to_position}")
                return True, f"Moved download from position {from_position} to {to_position}"
            else:
                logger.warning(f"Invalid queue positions: from={from_position}, to={to_position}")
                return False, "Invalid queue positions"
    except ValueError:
        return False, "Positions must be numbers"
    except Exception as e:
        logger.error(f"Error reordering queue: {str(e)}")
        return False, f"Error: {str(e)}"

def get_game_details(game_input):
    """Get detailed information about a game based on input (ID or URL)."""
    appid = parse_game_input(game_input)
    if not appid:
        return {"success": False, "error": "Invalid game ID or URL"}
    
    is_valid, game_info = validate_appid(appid)
    if not is_valid:
        return {"success": False, "error": game_info}
    
    return {"success": True, "appid": appid, "game_info": game_info}

# ========================
# UI COMPONENTS
# ========================

def create_download_games_tab():
    """Create the 'Download Games' tab in the Gradio interface."""
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
        
        # Define check_game_result BEFORE using it in any event handlers
        check_game_result = gr.Textbox(
            label="Game Status",
            placeholder="Game status will appear here",
            lines=6,  # More space for detailed status
            interactive=False
        )
        
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
                    
                    with gr.Group() as login_details:
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
        
        with gr.Row():
            download_btn = gr.Button("Download Game", variant="primary")
            queue_btn = gr.Button("Add to Queue", variant="secondary")
        
        download_status = gr.Markdown("Enter a game ID or URL and click 'Check Game' to start")
        
        # Event handlers
        
        # Toggle login details visibility based on anonymous checkbox
        def toggle_login_fields(anonymous):
            return gr.update(visible=not anonymous)
        
        anonymous.change(
            fn=toggle_login_fields,
            inputs=[anonymous],
            outputs=[login_details]
        )
        
        # Define the check game status function to return only what's needed
        def extract_appid_from_input(input_text):
            """Extract the AppID from various input formats including URLs"""
            try:
                # If it's a URL, extract the app ID from it
                url_match = re.search(r'app/(\d+)', input_text)
                if url_match:
                    return url_match.group(1)
            
                # Extract any numeric sequence that looks like an app ID
                numeric_match = re.search(r'(\d+)', input_text)
                if numeric_match:
                    return numeric_match.group(1)
            
                return input_text.strip()
            except Exception as e:
                logger.error(f"Error extracting App ID: {e}")
                return input_text.strip()

        def check_game_status(input_text):
            """Check if a game is installed and return its status"""
            if not input_text:
                return "Please enter a valid App ID or game URL"
            
            try:
                # Extract AppID from input (URL or text)
                appid = extract_appid_from_input(input_text)
                logger.info(f"Extracted AppID: {appid} from input: {input_text}")
                
                # First, check with the validate_appid function which is more reliable
                # This already has a timeout of 5 seconds in the requests.get() call
                is_valid, game_info = validate_appid(appid)
                
                if not is_valid:
                    logger.warning(f"Invalid AppID: {appid}. Error: {game_info}")
                    return f"Error: {game_info}"
                
                # If we get here, game is valid
                game_name = game_info.get('name', f"App {appid}")
                is_free = game_info.get('is_free', False)
                description = game_info.get('description', 'No description available')
                
                # Check if game is installed - with a simple try/except
                installed = False
                try:
                    installed = is_game_installed(appid)
                except Exception as e:
                    logger.warning(f"Error checking if game is installed: {e}")
                    # Continue anyway
                
                # Format a more complete response
                result = f"### Game: {game_name}\n"
                result += f"AppID: {appid}\n"
                result += f"Free to Play: {'Yes' if is_free else 'No'}\n"
                result += f"Installed: {'Yes' if installed else 'No'}\n"
                result += f"\nDescription: {description}"
                
                logger.info(f"Successfully retrieved info for game: {game_name}")
                return result
            
            except requests.exceptions.Timeout:
                logger.error(f"Timeout while checking game status for: {input_text}")
                return "Error: Request timed out while checking game status. Steam API may be unavailable."
            except Exception as e:
                logger.error(f"Error checking game status: {e}", exc_info=True)
                return f"Error checking game status: {str(e)}"
        
        # IMPORTANT: Find ALL instances where check_game_btn.click is defined
        # and make sure they're consistent
        
        # This is what it should be - connecting to only one output
        check_game_btn.click(
            fn=check_game_status,
            inputs=[game_input],
            outputs=[check_game_result]
        )
        
        download_btn.click(
            fn=queue_download,  # Call the queue_download function
            inputs=[username, password, guard_code, anonymous, game_input, validate_download],
            outputs=[check_game_result]
        )
        
        return game_input, check_game_btn, download_btn, check_game_result

def create_downloads_tab():
    """Create the 'Downloads' tab in the Gradio interface with real-time logs instead of tabular data."""
    # Get initial data for tables
    def get_system_stats():
        return [
            ["CPU Usage", f"{psutil.cpu_percent()}%"],
            ["Memory Usage", f"{psutil.virtual_memory().percent}%"],
            ["Disk Usage", f"{psutil.disk_usage('/').percent}%"],
            ["Active Downloads", str(len(active_downloads))],
            ["Queued Downloads", str(len(download_queue))]
        ]
    
    def get_queue_data():
        queue_data = []
        for i, download in enumerate(download_queue):
            appid = download["args"][4]
            queue_data.append([
                i + 1,  # Position
                appid,
                download.get("name", "Unknown Game"),
                "Unknown",  # Size
                "Yes" if download["args"][5] else "No"  # Validate
            ])
        return queue_data
    
    def get_history_data():
        history_data = []
        for download in download_history[:10]:  # Show latest 10 entries
            history_data.append([
                download.get("id", "")[:8],  # Shorten ID
                download.get("name", "Unknown"),
                download.get("status", "Unknown"),
                download.get("duration", "Unknown"),
                download.get("end_time", datetime.now()).strftime("%Y-%m-%d %H:%M:%S") if isinstance(download.get("end_time"), datetime) else "Unknown"
            ])
        return history_data
    
    with gr.Tab("Downloads"):
        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown("### Download Progress")
                # Replace the table with a scrolling log display
                download_logs = gr.Textbox(
                    label="Real-Time Download Progress",
                    value="Waiting for downloads to start...\n",
                    lines=20,
                    max_lines=1000,
                    autoscroll=True,
                    interactive=False
                )
                
                # Cancel functionality
                with gr.Row():
                    cancel_download_input = gr.Textbox(
                        label="Download ID to Cancel",
                        placeholder="Enter download ID to cancel"
                    )
                    cancel_download_btn = gr.Button("Cancel Download", variant="secondary")
                cancel_output = gr.Textbox(label="Cancel Result", interactive=False)
            
            with gr.Column(scale=1):
                gr.Markdown("### System Status")
                # Instead of creating the dataframe first and then updating it,
                # provide the initial value directly when creating it
                initial_stats = [
                    ["CPU Usage", f"{psutil.cpu_percent()}%"],
                    ["Memory Usage", f"{psutil.virtual_memory().percent}%"],
                    ["Disk Usage", f"{psutil.disk_usage('/').percent}%"],
                    ["Active Downloads", str(len(active_downloads))],
                    ["Queued Downloads", str(len(download_queue))]
                ]
                system_stats = gr.Dataframe(
                    headers=["Metric", "Value"],
                    value=initial_stats,  # Set initial value here
                    interactive=False,
                    wrap=True
                )
                
                # Add refresh button for system stats
                refresh_system_btn = gr.Button("Refresh Status")
        
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Download Queue")
                queue_table = gr.Dataframe(
                    headers=["Position", "App ID", "Name", "Size", "Validate?"],
                    interactive=False,
                    value=get_queue_data()  # Use function to get initial values
                )
                
                with gr.Row():
                    with gr.Column(scale=1):
                        remove_position = gr.Number(
                            label="Queue Position to Remove",
                            precision=0,
                            value=1,
                            minimum=1
                        )
                    with gr.Column(scale=1):
                        remove_queue_btn = gr.Button("Remove from Queue", variant="secondary")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        from_position = gr.Number(
                            label="Move From Position",
                            precision=0,
                            value=1,
                            minimum=1
                        )
                    with gr.Column(scale=1):
                        to_position = gr.Number(
                            label="To Position",
                            precision=0,
                            value=2,
                            minimum=1
                        )
                    with gr.Column(scale=1):
                        move_queue_btn = gr.Button("Move in Queue", variant="secondary")
                
                queue_action_result = gr.Textbox(label="Queue Action Result", interactive=False)
        
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Download History")
                history_table = gr.Dataframe(
                    headers=["ID", "Name", "Status", "Duration", "End Time"],
                    interactive=False,
                    value=get_history_data()  # Use function to get initial values
                )
        
        # Set up a log handler to capture logs for the UI
        class UILogHandler(logging.Handler):
            def __init__(self, log_box):
                super().__init__()
                self.log_box = log_box
                self.buffer = []
                self.max_lines = 1000
                self.lock = threading.Lock()
                
                # Format for download-related logs only
                self.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
                self.setLevel(logging.INFO)
            
            def emit(self, record):
                if record.levelno >= self.level:
                    # Only capture download-related logs
                    msg = record.getMessage().lower()
                    if "progress" in msg or "download" in msg or "speed" in msg or \
                       "eta" in msg or "steamcmd" in msg:
                        formatted = self.format(record)
                        with self.lock:
                            self.buffer.append(formatted)
                            # Keep buffer size limited
                            if len(self.buffer) > self.max_lines:
                                self.buffer = self.buffer[-self.max_lines:]
                            
                            # Update the log box with the latest buffer content
                            log_text = "\n".join(self.buffer)
                            self.log_box.value = log_text
        
        # Function to update system stats and tables for refresh button
        def update_system_stats():
            return (
                get_system_stats(),
                get_queue_data(),
                get_history_data()
            )
        
        # Connect refresh button
        refresh_system_btn.click(
            fn=update_system_stats,
            inputs=None,
            outputs=[system_stats, queue_table, history_table]
        )
        
        # Create and add the UI log handler
        ui_log_handler = UILogHandler(download_logs)
        logger.addHandler(ui_log_handler)
        
        # Connect cancel download button
        def cancel_and_refresh(download_id):
            result = cancel_download(download_id)
            stats = get_system_stats()
            queue = get_queue_data()
            history = get_history_data()
            return result, stats, queue, history
        
        cancel_download_btn.click(
            fn=cancel_and_refresh,
            inputs=[cancel_download_input],
            outputs=[cancel_output, system_stats, queue_table, history_table]
        )
        
        # Connect remove from queue button with refresh after
        def remove_and_refresh(position):
            result = remove_from_queue(position)
            stats = get_system_stats()
            queue = get_queue_data()
            history = get_history_data()
            return result, stats, queue, history
        
        remove_queue_btn.click(
            fn=remove_and_refresh,
            inputs=[remove_position],
            outputs=[queue_action_result, system_stats, queue_table, history_table]
        )
        
        # Connect move in queue button with refresh after
        def move_and_refresh(from_pos, to_pos):
            result = reorder_queue(int(from_pos), int(to_pos))[1]
            stats = get_system_stats()
            queue = get_queue_data()
            history = get_history_data()
            return result, stats, queue, history
        
        move_queue_btn.click(
            fn=move_and_refresh,
            inputs=[from_position, to_position],
            outputs=[queue_action_result, system_stats, queue_table, history_table]
        )
        
    # Return None since we don't have refresh buttons to return
    return None, None

def create_gradio_interface():
    """Create the main Gradio interface with all tabs."""
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
                                ["Python Version", platform.python_version()],
                                ["Total Memory", f"{psutil.virtual_memory().total / (1024**3):.2f} GB"],
                                ["Free Disk Space", f"{psutil.disk_usage('/').free / (1024**3):.2f} GB"],
                                ["SteamCMD Path", get_steamcmd_path()],
                                ["Download Path", get_default_download_location()]
                            ],
                            interactive=False
                        )
            
            # Call the create_download_games_tab function here
            game_input, check_game_btn, download_btn, check_game_result = create_download_games_tab()
            
            # Downloads tab (now returns None, None)
            _ = create_downloads_tab()
            
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
                    try:
                        # Update environment variable for log level
                        os.environ['LOG_LEVEL'] = log_level
                        logging.getLogger().setLevel(getattr(logging, log_level))
                        
                        # Store other settings (in a real app, these would be saved to a config file)
                        global MAX_HISTORY_SIZE
                        if keep_history:
                            MAX_HISTORY_SIZE = 50
                        else:
                            MAX_HISTORY_SIZE = 0
                            download_history.clear()
                        
                        # Note: In a real implementation, you'd save these to a config file
                        logger.info(f"Settings updated - Log Level: {log_level}, Max Concurrent: {max_concurrent}")
                        
                        return "Settings saved successfully"
                    except Exception as e:
                        logger.error(f"Error saving settings: {str(e)}")
                        return f"Error saving settings: {str(e)}"
                
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
                
                ### Download Management
                - You can cancel active downloads
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
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down Steam Downloader...")
        # Terminate all active downloads
        with queue_lock:
            for download_id in list(active_downloads.keys()):
                cancel_download(download_id)
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    return app

def queue_processor():
    """Background thread to process the download queue."""
    while True:
        try:
            process_download_queue()
            time.sleep(5)  # Check queue every 5 seconds
        except Exception as e:
            logger.error(f"Error in queue processor: {str(e)}")
            # Continue the loop even if there's an error
            time.sleep(10)  # Wait a bit longer after an error

def is_game_installed(appid):
    """Check if a game is installed by looking for its manifest file"""
    try:
        appid = str(appid).strip()
        
        # First, make sure STEAMAPPS_PATH exists
        if not os.path.exists(STEAMAPPS_PATH):
            logger.warning(f"Steam apps directory does not exist: {STEAMAPPS_PATH}")
            return False
        
        # Check if appmanifest file exists (this is the most reliable method)
        manifest_path = os.path.join(STEAMAPPS_PATH, f"appmanifest_{appid}.acf")
        if os.path.exists(manifest_path):
            logger.info(f"Found manifest file for AppID {appid}: {manifest_path}")
            return True
        
        # Try to check for the common directory
        common_dir = os.path.join(STEAMAPPS_PATH, "common")
        if not os.path.exists(common_dir):
            # Try alternate potential locations
            logger.warning(f"Common games directory not found at {common_dir}")
            common_dir = os.path.join(STEAMAPPS_PATH, "..", "common")
            if not os.path.exists(common_dir):
                logger.warning(f"Common games directory not found at alternate location either")
                # If we can't find the common directory, we can't check by game name
                return False
        
        # If we reach here, we have a valid common directory
        logger.info(f"Using common directory: {common_dir}")
        
        # Try to check by game name
        try:
            game_info = get_game_info(appid)
            game_name = game_info.get('name')
            if game_name and os.path.exists(common_dir):
                # List all directories and check for matches
                for folder in os.listdir(common_dir):
                    folder_path = os.path.join(common_dir, folder)
                    if os.path.isdir(folder_path):
                        # Check if game name is in folder name (case-insensitive)
                        if game_name.lower() in folder.lower():
                            logger.info(f"Found potential game directory for {game_name}: {folder_path}")
                            return True
        except Exception as e:
            logger.warning(f"Error checking game by name: {e}")
        
        # If we get here, we couldn't find the game
        logger.info(f"Game with AppID {appid} does not appear to be installed")
        return False
    except Exception as e:
        logger.error(f"Error in is_game_installed: {e}")
        return False

def get_game_size(appid):
    """Get the size of an installed game in bytes"""
    try:
        # First check if the game is installed
        if not is_game_installed(appid):
            return 0
        
        # Try to get the size from the manifest file
        manifest_path = os.path.join(STEAMAPPS_PATH, f"appmanifest_{appid}.acf")
        if os.path.exists(manifest_path):
            # Estimate size from manifest if possible
            try:
                with open(manifest_path, 'r') as f:
                    content = f.read()
                    # Try to extract size info from manifest
                    size_match = re.search(r'"SizeOnDisk"\s+"(\d+)"', content)
                    if size_match:
                        return int(size_match.group(1))
            except Exception as e:
                logger.warning(f"Failed to read size from manifest: {e}")
        
        # If we reach here, try to find the game directory
        common_dir = os.path.join(STEAMAPPS_PATH, "common")
        if not os.path.exists(common_dir):
            common_dir = os.path.join(STEAMAPPS_PATH, "..", "common")
            if not os.path.exists(common_dir):
                logger.warning("Cannot find games directory to calculate size")
                return 0
        
        # Try to find by name
        game_info = get_game_info(appid)
        game_name = game_info.get('name', '')
        
        if game_name:
            # Check for directory with matching name
            for folder in os.listdir(common_dir):
                folder_path = os.path.join(common_dir, folder)
                if os.path.isdir(folder_path) and game_name.lower() in folder.lower():
                    return get_directory_size(folder_path)
        
        return 0
    except Exception as e:
        logger.error(f"Error in get_game_size: {e}")
        return 0

def get_directory_size(path):
    """Calculate the total size of a directory in bytes"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            if os.path.exists(file_path):
                total_size += os.path.getsize(file_path)
    return total_size

def format_size(size_bytes):
    """Format size in bytes to a human-readable string"""
    if size_bytes == 0:
        return "0B"
    size_names = ("B", "KB", "MB", "GB", "TB")
    i = int(math.log(size_bytes, 1024))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"

def get_game_info(appid):
    """Get information about a game from the Steam API or cache"""
    try:
        appid = str(appid).strip()
        
        # Check if we have cached info
        cache_file = os.path.join(CACHE_DIR, f"game_{appid}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cached game info: {e}")
        
        # Get from Steam API
        steam_api_url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
        try:
            response = requests.get(steam_api_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data and data.get(appid, {}).get('success', False):
                    game_data = data[appid]['data']
                    
                    # Save to cache
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(cache_file, 'w') as f:
                        json.dump(game_data, f)
                    
                    return game_data
        except Exception as e:
            logger.warning(f"Error fetching game info from Steam API: {e}")
        
        # Fallback: return minimal info
        return {"name": f"App {appid}", "appid": appid}
    except Exception as e:
        logger.error(f"Error in get_game_info: {e}")
        return {"name": f"App {appid}", "appid": appid}

# Make sure you have CACHE_DIR and STEAMAPPS_PATH defined
if not 'CACHE_DIR' in globals():
    CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
    os.makedirs(CACHE_DIR, exist_ok=True)

if not 'STEAMAPPS_PATH' in globals():
    # This should be set to your actual SteamApps path
    STEAMAPPS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "steamapps")
    if not os.path.exists(STEAMAPPS_PATH):
        # Try default paths
        possible_paths = [
            "/steamapps",
            "/Steam/steamapps", 
            "/home/steam/Steam/steamapps",
            "/app/steamapps"
        ]
        for path in possible_paths:
            if os.path.exists(path):
                STEAMAPPS_PATH = path
                break

if __name__ == "__main__":
    # Ensure necessary directories exist
    for directory in [get_default_download_location(), '/app/logs', '/app/steamcmd']:
        ensure_directory_exists(directory)
    
    # Ensure SteamCMD is installed
    if "not installed" in check_steamcmd():
        logger.info("SteamCMD not found, installing...")
        install_steamcmd()
    
    # Create the Gradio interface
    app_interface = create_gradio_interface()
    
    # Start the FastAPI server for file serving in a separate thread
    threading.Thread(
        target=lambda: uvicorn.run(fastapi_app, host="0.0.0.0", port=8081),
        daemon=True
    ).start()
    
    port = int(os.getenv("PORT", 7860))
    logger.info(f"Starting application on port {port}")
    
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
        logger.info(f"Gradio share URL: {launch_info.share_url}")
    else:
        logger.warning("Launch info does not contain a share URL.")
    
    # Keep the script running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Application stopped by user")

try:
    requests.get("https://store.steampowered.com", timeout=3)
    logger.info("Steam store website is reachable")
except Exception as e:
    logger.error(f"Cannot reach Steam store: {str(e)}")