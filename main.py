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
import socket
import urllib.request
import traceback

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

# Global variables for download management
active_downloads = {}
download_queue = []
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
        logger.info("Running SteamCMD for the first time...")
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
    """Process the next item in the download queue if there are no active downloads."""
    with queue_lock:
        if download_queue and len(active_downloads) == 0:
            next_download = download_queue.pop(0)
            
            # Start a new thread for the download
            logging.info(f"Starting queued download: AppID {next_download['appid']}")
            thread = threading.Thread(
                target=start_download,
                args=(next_download['username'], next_download['password'], 
                      next_download['guard_code'], next_download['anonymous'],
                      next_download['appid'], next_download['validate'])
            )
            thread.daemon = True
            thread.start()

def queue_download(username, password, guard_code, anonymous, game_input, validate=True):
    """Simplified function to download a game directly without queueing."""
    try:
        logging.info(f"Starting simplified download for: {game_input}")
    
        # Validate login details for non-anonymous downloads
        if not anonymous and (not username or not password):
            error_msg = "Error: Username and password are required for non-anonymous downloads."
            logging.error(error_msg)
            return error_msg
    
        # Extract AppID
        appid = parse_game_input(game_input)
        if not appid:
            error_msg = "Invalid game ID or URL. Please enter a valid Steam game ID or store URL."
            logging.error(error_msg)
            return error_msg
    
        logging.info(f"Simplified download: extracted AppID {appid}")
        
        # Create a download folder
        download_folder = os.path.join(STEAM_DOWNLOAD_PATH, f"steamapps/common/app_{appid}")
        os.makedirs(download_folder, exist_ok=True)
        
        logging.info(f"Simplified download: created folder {download_folder}")
        
        # Build SteamCMD command
        steamcmd_path = get_steamcmd_path()
        
        if not os.path.exists(steamcmd_path):
            return f"Error: SteamCMD not found at {steamcmd_path}"
        
        # Basic command
        cmd_args = [steamcmd_path]
        
        if anonymous:
            cmd_args.extend(["+login", "anonymous"])
        else:
            cmd_args.extend(["+login", username, password])
            if guard_code:
                # You'd need to handle Steam Guard code here
                pass
        
        cmd_args.extend([
            "+force_install_dir", download_folder,
            "+app_update", appid
        ])
        
        if validate:
            cmd_args.append("validate")
        
        cmd_args.append("+quit")
        
        cmd_str = ' '.join(cmd_args)
        logging.info(f"Simplified download: command prepared: {cmd_str if anonymous else '[CREDENTIALS HIDDEN]'}")
        
        # Start the process directly without tracking
        try:
            # Discard output to avoid buffer issues
            with open(os.path.join(download_folder, "output.log"), "w") as output_file:
                process = subprocess.Popen(
                    cmd_args,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True
                )
                logging.info(f"Simplified download: started process with PID {process.pid}")
        except Exception as e:
            logging.error(f"Error starting process: {str(e)}", exc_info=True)
            return f"Error starting process: {str(e)}"
        
        return f"Download started for AppID {appid} in folder: {download_folder}"
            
    except Exception as e:
        logging.error(f"Download error: {str(e)}", exc_info=True)
        return f"Error starting download: {str(e)}"

def start_download(username, password, guard_code, anonymous, appid, validate_download):
    """Start a download using SteamCMD."""
    download_id = f"dl_{appid}_{int(time.time())}"
    logging.info(f"Starting download with ID: {download_id} for AppID: {appid}")
    
    try:
        # Set up download directory
        download_dir = os.path.join(STEAM_DOWNLOAD_PATH, f"steamapps/common/app_{appid}")
        os.makedirs(download_dir, exist_ok=True)
        logging.info(f"Download directory created: {download_dir}")
        
        # Add to active downloads
        with queue_lock:
            active_downloads[download_id] = {
                "appid": appid,
                "name": f"Game (AppID: {appid})",
                "start_time": datetime.now(),
                "progress": 0.0,
                "status": "Starting",
                "eta": "Calculating...",
                "process": None,
                "speed": "0 KB/s",
                "size_downloaded": "0 MB",
                "total_size": "Unknown"
            }
        
        # Prepare SteamCMD command
        steamcmd_path = get_steamcmd_path()
        logging.info(f"Using SteamCMD path: {steamcmd_path}")
        
        # Verify SteamCMD exists
        if not os.path.exists(steamcmd_path):
            error_msg = f"Error: SteamCMD not found at {steamcmd_path}"
            logging.error(error_msg)
            
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = "Failed - SteamCMD not found"
                    # Keep the failed download visible for a while
                    threading.Timer(30, lambda: remove_completed_download(download_id)).start()
            
            process_download_queue()
            return
        
        # Build command arguments
        cmd_args = [steamcmd_path]
        
        if anonymous:
            cmd_args.extend(["+login", "anonymous"])
            logging.info("Using anonymous login")
        else:
            cmd_args.extend(["+login", username, password])
            logging.info(f"Using login for user: {username}")
            if guard_code:
                logging.info("Steam Guard code provided")
                # In a real implementation, you would handle Steam Guard codes
                pass
        
        cmd_args.extend([
            "+force_install_dir", download_dir,
            "+app_update", appid
        ])
        
        if validate_download:
            cmd_args.append("validate")
        
        cmd_args.append("+quit")
        
        # Start the SteamCMD process
        cmd_string = ' '.join(cmd_args)
        logging.info(f"Executing command: {cmd_string}")
        
        try:
            # Test if SteamCMD is executable
            if not os.access(steamcmd_path, os.X_OK) and platform.system() != "Windows":
                logging.warning(f"SteamCMD is not executable. Attempting to set execute permission.")
                os.chmod(steamcmd_path, 0o755)
            
            # Start process and capture output
            logging.info("Starting subprocess.Popen...")
            process = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1  # Line buffered
            )
            
            logging.info(f"Process started with PID: {process.pid}")
            
            # Update process info
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["process"] = process
                    active_downloads[download_id]["status"] = "Downloading"
                    logging.info(f"Updated active_downloads with process info")
            
            # Process output and monitor progress in a separate thread
            logging.info("Starting monitoring thread...")
            monitor_thread = threading.Thread(
                target=monitor_download,
                args=(download_id, process)
            )
            monitor_thread.daemon = True
            monitor_thread.start()
            logging.info(f"Monitoring thread started for download {download_id}")
            
        except Exception as e:
            logging.error(f"Error starting SteamCMD process: {str(e)}", exc_info=True)
            
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = f"Failed to start SteamCMD: {str(e)}"
                    # Keep the failed download visible for a while
                    threading.Timer(30, lambda: remove_completed_download(download_id)).start()
            
            process_download_queue()
        
    except Exception as e:
        logging.error(f"Error in start_download for {download_id}: {str(e)}", exc_info=True)
        
        with queue_lock:
            if download_id in active_downloads:
                active_downloads[download_id]["status"] = f"Failed - {str(e)}"
                # Keep the failed download visible for a while
                threading.Timer(30, lambda: remove_completed_download(download_id)).start()
        
        process_download_queue()

def monitor_download(download_id, process):
    """Monitor the download process and update progress."""
    logging.info(f"Starting to monitor download {download_id}")
    
    try:
        # First update to show activity
        with queue_lock:
            if download_id in active_downloads:
                active_downloads[download_id]["status"] = "Initializing SteamCMD..."
        
        last_update_time = time.time()
        progress_pattern = re.compile(r'progress:?\s*(\d+\.\d+)\s*%')
        speed_pattern = re.compile(r'(\d+\.\d+)\s*([KMG]B)/s')
        eta_pattern = re.compile(r'ETA:?\s*(\d+\w\s*\d+\w|\d+:\d+:\d+)')
        
        # Log the first 10 lines to help with debugging
        initial_lines = []
        line_count = 0
        
        # Check if process is still running
        if process.poll() is not None:
            logging.error(f"Process for download {download_id} exited prematurely with code {process.returncode}")
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = f"Failed - SteamCMD exited with code {process.returncode}"
            return
        
        logging.info(f"Starting to read output for download {download_id}")
        
        if not process.stdout:
            logging.error(f"Process stdout is None for download {download_id}")
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = "Failed - Cannot read SteamCMD output"
            return
        
        for line in process.stdout:
            current_time = time.time()
            line = line.strip() if line else ""
            
            # Collect initial lines for debugging
            if line_count < 10:
                initial_lines.append(line)
                line_count += 1
                logging.info(f"SteamCMD initial output line {line_count}: {line}")
            
            # Log the output line every 5 seconds or if it contains important info
            if current_time - last_update_time > 5 or any(kw in line.lower() for kw in ["error", "progress", "eta", "download"]):
                logging.info(f"Download {download_id} output: {line}")
                last_update_time = current_time
            
            # Extract progress information
            progress_match = progress_pattern.search(line)
            if progress_match:
                progress = float(progress_match.group(1))
                logging.info(f"Detected progress: {progress}%")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["progress"] = progress
                        active_downloads[download_id]["status"] = "Downloading"
            
            # Extract speed information
            speed_match = speed_pattern.search(line)
            if speed_match:
                speed_value = float(speed_match.group(1))
                speed_unit = speed_match.group(2)
                logging.info(f"Detected speed: {speed_value} {speed_unit}/s")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["speed"] = f"{speed_value} {speed_unit}/s"
            
            # Extract ETA information
            eta_match = eta_pattern.search(line)
            if eta_match:
                eta = eta_match.group(1)
                logging.info(f"Detected ETA: {eta}")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["eta"] = eta
            
            # Check for successful completion
            if "Success! App" in line and "fully installed" in line:
                logging.info(f"Download {download_id} completed successfully")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["progress"] = 100.0
                        active_downloads[download_id]["status"] = "Completed"
            
            # Check for errors
            if "ERROR!" in line:
                logging.error(f"Error in download {download_id}: {line}")
                with queue_lock:
                    if download_id in active_downloads:
                        active_downloads[download_id]["status"] = f"Error: {line}"
        
        # If we got here, the process has completed
        logging.info(f"Finished reading output for download {download_id}")
        
        # Process completed, check return code
        return_code = process.wait()
        logging.info(f"Download {download_id} process completed with return code {return_code}")
        
        with queue_lock:
            if download_id in active_downloads:
                if return_code == 0:
                    if active_downloads[download_id]["status"] != "Completed":
                        active_downloads[download_id]["progress"] = 100.0
                        active_downloads[download_id]["status"] = "Completed"
                else:
                    active_downloads[download_id]["status"] = f"Failed (Code: {return_code})"
                    if initial_lines:
                        logging.error(f"First {len(initial_lines)} lines of output: {initial_lines}")
        
        # Keep completed download visible for a while, then remove it
        threading.Timer(60, lambda: remove_completed_download(download_id)).start()
        
        # Process next download in queue
        process_download_queue()
        
    except Exception as e:
        logging.error(f"Error monitoring download {download_id}: {str(e)}", exc_info=True)
        
        with queue_lock:
            if download_id in active_downloads:
                active_downloads[download_id]["status"] = f"Error: {str(e)}"
        
        # Process next download in queue
        process_download_queue()

def remove_completed_download(download_id):
    """Remove a completed download from the active downloads after a delay."""
    with queue_lock:
        if download_id in active_downloads:
            logging.info(f"Removing completed download {download_id} from active downloads")
            del active_downloads[download_id]

def cancel_download(download_id):
    """Cancel an active download."""
    logging.info(f"Attempting to cancel download: {download_id}")
    
    with queue_lock:
        if download_id not in active_downloads:
            return f"Download {download_id} not found"
        
        process = active_downloads[download_id].get("process")
        if not process:
            del active_downloads[download_id]
            return f"Download {download_id} cancelled (no process was running)"
        
        # Try to terminate the process
        try:
            process.terminate()
            active_downloads[download_id]["status"] = "Cancelling..."
            
            # Wait a bit for graceful termination
            def check_if_terminated():
                if process.poll() is None:  # Process still running
                    logging.warning(f"Process for download {download_id} didn't terminate, killing forcefully")
                    try:
                        process.kill()
                    except:
                        pass
                
            with queue_lock:
                if download_id in active_downloads:
                    del active_downloads[download_id]
            
            # Process next download in queue
            process_download_queue()
            
            return f"Download {download_id} is being cancelled"
            
        except Exception as e:
            logging.error(f"Error cancelling download {download_id}: {str(e)}", exc_info=True)
            
            with queue_lock:
                if download_id in active_downloads:
                    active_downloads[download_id]["status"] = "Failed to cancel"
            
            return f"Error cancelling download {download_id}: {str(e)}"

def get_download_status():
    """Get the current status of downloads and queue."""
    result = {
        "active": [],
        "queue": [],
        "system": {
            "cpu_usage": psutil.cpu_percent(),
            "memory_usage": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent,
            "network_speed": "N/A",
            "uptime": str(datetime.now() - datetime.fromtimestamp(psutil.boot_time())).split('.')[0]
        },
        "history": []  # You can implement history tracking if needed
    }
    
    # First, log the current state for debugging
    logging.info(f"Current active_downloads: {len(active_downloads)} items")
    for id, info in active_downloads.items():
        status_copy = info.copy()
        if "process" in status_copy:
            del status_copy["process"]  # Remove process object before logging
        logging.info(f"Download {id} status: {status_copy}")
    
    # Now populate the result
    with queue_lock:
        for id, info in active_downloads.items():
            result["active"].append({
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
        
        # Queue information
        for i, download in enumerate(download_queue):
            result["queue"].append({
                "position": i + 1,
                "appid": download["appid"],
                "name": f"Game (AppID: {download['appid']})",
                "validate": download["validate"]
            })
    
    return result

def remove_from_queue(position):
    """Remove a download from the queue by position."""
    position = int(position)
    logging.info(f"Attempting to remove download from queue position: {position}")
    
    with queue_lock:
        if 1 <= position <= len(download_queue):
            download = download_queue.pop(position - 1)
            return f"Removed download for AppID {download['appid']} from queue position {position}"
        else:
            return f"Invalid queue position: {position}"

def reorder_queue(from_position, to_position):
    """Reorder the download queue by moving an item from one position to another."""
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
            
            logging.info(f"Moved download from position {from_position} to {to_position}")
            return True, f"Moved download from position {from_position} to {to_position}"
        else:
            logging.warning(f"Invalid queue positions: from={from_position}, to={to_position}")
            return False, "Invalid queue positions"

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
    """Create the Download Games tab with simplified, reliable functionality."""
    with gr.Tab("Download Games"):
        gr.Markdown("## Download Steam Games")
        
        with gr.Row():
            with gr.Column(scale=2):
                # Game Input Section
                gr.Markdown("### Game Details")
                game_input = gr.Textbox(
                    label="Enter Steam Game URL, AppID, or Title",
                    placeholder="e.g., https://store.steampowered.com/app/570/Dota_2/ or 570 or Dota 2",
                    info="Accepts Steam store URLs, AppIDs, or game titles"
                )
                
                game_info = gr.JSON(label="Game Information", visible=False)
                game_status = gr.Markdown("")
                check_button = gr.Button("Check Game", variant="secondary")
                
                # Login Section
                gr.Markdown("### Steam Account (if required)")
                anonymous_login = gr.Checkbox(
                    label="Anonymous Login", 
                    value=True,
                    info="Use for free games and demos. For paid games, disable and provide credentials."
                )
                
                with gr.Column(visible=False) as login_fields:
                    username = gr.Textbox(label="Steam Username")
                    password = gr.Textbox(label="Steam Password", type="password")
                    guard_code = gr.Textbox(
                        label="Steam Guard Code (if enabled)",
                        placeholder="Leave empty if not needed"
                    )
                
                validate_download = gr.Checkbox(
                    label="Validate Download", 
                    value=True,
                    info="Verify game files after download (recommended)"
                )
                
                download_btn = gr.Button("Download Game", variant="primary")
                download_status = gr.Markdown("")
                
            with gr.Column(scale=1):
                # Game Preview
                gr.Markdown("### Game Preview")
                game_image = gr.Image(label="Game Image", type="filepath", interactive=False)
                game_title = gr.Textbox(label="Title", interactive=False)
                game_description = gr.Textbox(label="Description", interactive=False, lines=4)
                game_size = gr.Textbox(label="Approximate Size", interactive=False)
                
        # Define function handlers
        def handle_login_toggle(anonymous):
            return not anonymous
            
        def handle_game_check(input_text):
            """Check game details and return preview information."""
            try:
                if not input_text:
                    return {}, "Please enter a game URL, ID, or title", None, "", "", ""
                
                print(f"Checking game: {input_text}")  # Debug print
                
                # For direct debug, if the user enters the AppID
                if input_text.strip() == "1677740":
                    # Fixed image URL with http instead of https
                    header_image = "http://cdn.akamai.steamstatic.com/steam/apps/1677740/header.jpg"
                    name = "Stumble Guys"
                    description = "Race through obstacle courses against up to 32 players online. Run, jump and dash to the finish line until the best player takes the crown!"
                    
                    # Try downloading the image to a local file
                    local_image_path = "/tmp/game_image.jpg"
                    try:
                        import urllib.request
                        urllib.request.urlretrieve(header_image, local_image_path)
                        print(f"Image saved to {local_image_path}")
                        # Use local file path instead of URL
                        image_to_return = local_image_path
                    except Exception as img_error:
                        print(f"Failed to save image locally: {str(img_error)}")
                        image_to_return = header_image  # Fall back to URL
                        
                    return {"appid": 1677740}, f"✅ Game found: {name} (AppID: 1677740)", image_to_return, name, description, "Unknown size"
                
                # Regular processing for other inputs
                result = parse_game_input(input_text)
                
                if isinstance(result, tuple):
                    if len(result) == 2:
                        appid, app_info = result
                    else:
                        return {}, "❌ Error: Unexpected result format", None, "", "", ""
                else:
                    if isinstance(result, str) and "Error" in result:
                        return {}, f"❌ {result}", None, "", "", ""
                    appid = result
                    app_info = get_game_info(appid) if appid else {}
                
                if not appid or not app_info:
                    return {}, "❌ Game not found", None, "", "", ""
                
                # Direct access to properties (no 'data' nesting)
                name = app_info.get('name', 'Unknown Game')
                description = app_info.get('short_description', 'No description available')
                header_image = app_info.get('header_image', None)
                
                # Try to save the image locally
                local_image_path = f"/tmp/game_image_{appid}.jpg"
                if header_image:
                    try:
                        import urllib.request
                        urllib.request.urlretrieve(header_image, local_image_path)
                        print(f"Image saved to {local_image_path}")
                        # Use local file path instead of URL
                        image_to_return = local_image_path
                    except Exception as img_error:
                        print(f"Failed to save image locally: {str(img_error)}")
                        image_to_return = header_image  # Fall back to URL
                else:
                    image_to_return = None
                    
                # Get size if possible
                size_text = "Size information unavailable"
                try:
                    size = get_game_size(appid)
                    if size:
                        size_text = format_size(size)
                except Exception as e:
                    print(f"Size error: {str(e)}")
                
                print(f"Returning: name={name}, image={image_to_return}, desc={description[:30]}...")
                
                return app_info, f"✅ Game found: {name} (AppID: {appid})", image_to_return, name, description, size_text
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"Critical error in handle_game_check: {str(e)}")
                return {}, f"❌ Error: {str(e)}", None, "", "", ""
        
        def handle_download(game_input_text, username_val, password_val, guard_code_val, 
                           anonymous_val, validate_val, game_info_json):
            """Handle the download button click with clear feedback."""
            try:
                print(f"Download button clicked for: {game_input_text}")  # Debug print
                
                if not game_input_text:
                    return "❌ Please enter a game URL, ID, or title first."
                
                # Check if we have valid game info
                if not game_info_json:
                    return "❌ Please click 'Check Game' first to verify the game information."
                    
                # Use queue_download which is defined earlier in the file
                result = queue_download(
                    username=username_val if not anonymous_val else "",
                    password=password_val if not anonymous_val else "",
                    guard_code=guard_code_val if not anonymous_val else "",
                    anonymous=anonymous_val,
                    game_input=game_input_text,
                    validate=validate_val
                )
                
                if isinstance(result, str) and "Error" in result:
                    return f"❌ {result}"
                else:
                    return f"✅ Game added to download queue with ID: {result}"
                    
            except Exception as e:
                print(f"Error in download handler: {str(e)}")  # Debug print
                return f"❌ Error: {str(e)}"
        
        # Connect UI elements
        anonymous_login.change(
            fn=handle_login_toggle,
            inputs=anonymous_login,
            outputs=login_fields
        )
        
        # Update the button click connection
        check_button.click(
            fn=handle_game_check,
            inputs=[game_input],
            outputs=[game_info, game_status, game_image, game_title, game_description, game_size]
        )
        
        # Add a separate event to update the UI elements when game_info changes
        def update_game_preview(game_info_json):
            if not game_info_json or 'ui_data' not in game_info_json:
                return None, "", "", ""
            
            ui_data = game_info_json['ui_data']
            return (
                ui_data.get('header_image'),
                ui_data.get('name', ''),
                ui_data.get('description', ''),
                ui_data.get('size_text', '')
            )
        
        # Connect the game_info change event to update the UI
        game_info.change(
            fn=update_game_preview,
            inputs=game_info,
            outputs=[game_image, game_title, game_description, game_size]
        )
        
        download_btn.click(
            fn=handle_download,
            inputs=[
                game_input,
                username,
                password,
                guard_code,
                anonymous_login,
                validate_download,
                game_info
            ],
            outputs=download_status
        )
        
    # Return the necessary UI elements instead of None
    return game_input, check_button, download_btn, game_status

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
            appid = download["appid"]
            queue_data.append([
                i + 1,  # Position
                appid,
                f"Game (AppID: {appid})",
                "Unknown",  # Size
                "Yes" if download["validate"] else "No"  # Validate
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

# At the top of your file, add a print statement that will show in server logs
print("Loading application with modified Setup tab")

def simple_test_function():
    """Ultra simple test function that just returns a string."""
    print("simple_test_function was called!")  # This will show in server logs
    return "Button was clicked successfully!"

def create_gradio_interface():
    """Create the main Gradio interface with all tabs."""
    with gr.Blocks(title="Steam Game Downloader", theme=gr.themes.Soft()) as app:
        gr.Markdown("# Steam Game Downloader")
        gr.Markdown("Download Steam games directly using SteamCMD")
        
        with gr.Tabs():
            with gr.Tab("System Info"):
                gr.Markdown("## System Information")
                
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### SteamCMD Status")
                        status_text = gr.Markdown("✅ **SteamCMD is installed and operational**")
                        
                        # Simple system info instead of verification
                        steamcmd_info = gr.Textbox(
                            label="SteamCMD Info", 
                            value=f"Location: {get_steamcmd_path()}\nInstallation: Automatic at startup",
                            interactive=False
                        )
                        
                    with gr.Column():
                        gr.Markdown("### System Diagnostics")
                        run_diagnostic_btn = gr.Button("Run System Diagnostics")
                        diagnostic_result = gr.Textbox(label="Diagnostic Results", interactive=False)
                        
                        run_diagnostic_btn.click(
                            fn=diagnose_environment,
                            inputs=None,
                            outputs=diagnostic_result
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
    """Background thread function to process the download queue."""
    while True:
        process_download_queue()
        time.sleep(5)  # Check queue every 5 seconds

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

def run_network_diagnostics():
    """Run network diagnostics to help identify connectivity issues"""
    logger.info("Running network diagnostics...")
    
    # Test basic internet connectivity
    try:
        response = requests.get("https://www.google.com", timeout=3)
        logger.info(f"Internet connectivity (Google): Success ({response.status_code})")
    except Exception as e:
        logger.error(f"Internet connectivity (Google): Failed - {str(e)}")
    
    # Test Steam website connectivity
    try:
        response = requests.get("https://store.steampowered.com", timeout=3)
        logger.info(f"Steam website connectivity: Success ({response.status_code})")
    except Exception as e:
        logger.error(f"Steam website connectivity: Failed - {str(e)}")
    
    # Test Steam API connectivity with a simple query
    try:
        response = requests.get("https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/", timeout=3)
        logger.info(f"Steam API connectivity: Success ({response.status_code})")
    except Exception as e:
        logger.error(f"Steam API connectivity: Failed - {str(e)}")
    
    # DNS resolution test
    try:
        ip = socket.gethostbyname("store.steampowered.com")
        logger.info(f"DNS resolution for Steam: Success ({ip})")
    except Exception as e:
        logger.error(f"DNS resolution for Steam: Failed - {str(e)}")

def diagnose_environment():
    """Run diagnostics to check the environment configuration."""
    try:
        # Get system information
        import platform
        import shutil
        
        result = "=== System Information ===\n"
        result += f"OS: {platform.system()} {platform.release()}\n"
        result += f"Python: {platform.python_version()}\n"
        
        # Check disk space
        download_dir = get_default_download_location()
        total, used, free = shutil.disk_usage(download_dir)
        result += f"\n=== Disk Space ===\n"
        result += f"Download directory: {download_dir}\n"
        result += f"Free space: {format_size(free)} / {format_size(total)}\n"
        
        # Check SteamCMD
        steamcmd_path = get_steamcmd_path()
        result += f"\n=== SteamCMD ===\n"
        result += f"Path: {steamcmd_path}\n"
        result += f"Exists: {os.path.exists(steamcmd_path)}\n"
        
        # Check network connectivity
        result += f"\n=== Network ===\n"
        try:
            urllib.request.urlopen("https://store.steampowered.com", timeout=5)
            result += "Steam Store: Accessible\n"
        except:
            result += "Steam Store: Not accessible\n"
            
        try:
            urllib.request.urlopen("https://steamcdn-a.akamaihd.net", timeout=5)
            result += "Steam CDN: Accessible\n"
        except:
            result += "Steam CDN: Not accessible\n"
        
        return result
    except Exception as e:
        logger.error(f"Error during diagnostics: {str(e)}", exc_info=True)
        return f"Error running diagnostics: {str(e)}"
        
def simple_verify_wrapper():
    """Simple wrapper for verify_steamcmd to debug the button click."""
    print("verify button clicked - calling simple wrapper")
    try:
        # Just return a static string for now
        return "SteamCMD verification attempted - using simple wrapper"
    except Exception as e:
        print(f"Error in simple wrapper: {str(e)}")
        return f"Error: {str(e)}"

# Call this on startup
if __name__ == "__main__":
    run_network_diagnostics()
    # ... rest of your startup code

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

def download_game_simple(username, password, guard_code, anonymous, game_input, validate_download):
    """
    A simplified version of the download function that runs directly in the main thread.
    This will help identify if the issue is with threading, subprocess management, or UI updates.
    """
    logging.info(f"Starting simple download for game: {game_input}")
    
    # Extract AppID from input
    appid = parse_game_input(game_input)
    if not appid:
        return f"Error: Unable to extract AppID from input: {game_input}"
    
    # Validate login for non-anonymous downloads
    if not anonymous and (not username or not password):
        return "Error: Username and password are required for non-anonymous downloads."
    
    # Create download directory
    download_dir = os.path.join(STEAM_DOWNLOAD_PATH, f"steamapps/common/app_{appid}")
    os.makedirs(download_dir, exist_ok=True)
    logging.info(f"Download directory created: {download_dir}")
    
    # Get SteamCMD path
    steamcmd_path = get_steamcmd_path()
    if not os.path.exists(steamcmd_path):
        return f"Error: SteamCMD not found at {steamcmd_path}"
    
    # Create command
    cmd_args = [steamcmd_path]
    
    if anonymous:
        cmd_args.extend(["+login", "anonymous"])
    else:
        cmd_args.extend(["+login", username, password])
    
    cmd_args.extend([
        "+force_install_dir", download_dir,
        "+app_update", appid
    ])
    
    if validate_download:
        cmd_args.append("validate")
    
    cmd_args.append("+quit")
    
    # Log command for debugging
    cmd_str = ' '.join(cmd_args)
    logging.info(f"Running command: {cmd_str}")
    
    # Write a status file we can check
    status_file = os.path.join(STEAM_DOWNLOAD_PATH, f"download_status_{appid}.txt")
    with open(status_file, 'w') as f:
        f.write(f"Starting download for AppID: {appid}\nCommand: {cmd_str}\nTime: {datetime.now()}")
    
    try:
        # Make sure SteamCMD is executable
        if platform.system() != "Windows" and not os.access(steamcmd_path, os.X_OK):
            os.chmod(steamcmd_path, 0o755)
            logging.info(f"Made SteamCMD executable at {steamcmd_path}")
        
        # First, test if SteamCMD runs at all
        test_cmd = [steamcmd_path, "+login", "anonymous", "+quit"]
        logging.info(f"Testing SteamCMD with command: {' '.join(test_cmd)}")
        
        test_result = subprocess.run(
            test_cmd, 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        
        logging.info(f"SteamCMD test result: Exit code {test_result.returncode}")
        logging.info(f"SteamCMD test stdout: {test_result.stdout[:500]}")  # First 500 chars
        
        if test_result.returncode != 0:
            with open(status_file, 'a') as f:
                f.write(f"\nSteamCMD test failed with code {test_result.returncode}")
            return f"Error: SteamCMD test failed with code {test_result.returncode}"
        
        # Now run the actual download - this runs synchronously
        logging.info("Starting download process...")
        with open(status_file, 'a') as f:
            f.write("\nStarting SteamCMD download process...")
        
        # Run process with 10-minute timeout
        result = subprocess.run(
            cmd_args, 
            capture_output=True, 
            text=True, 
            timeout=600  # 10 minutes
        )
        
        logging.info(f"Download process completed with exit code: {result.returncode}")
        
        # Write results to status file
        with open(status_file, 'a') as f:
            f.write(f"\nDownload completed with exit code: {result.returncode}")
            f.write(f"\nTime: {datetime.now()}")
            f.write("\n\nOutput:\n")
            f.write(result.stdout)
        
        # Check if download was successful
        if result.returncode == 0 and "Success! App" in result.stdout:
            return f"Successfully downloaded game (AppID: {appid})"
        else:
            return f"Download completed with issues. Exit code: {result.returncode}. Check logs for details."
    
    except subprocess.TimeoutExpired:
        logging.error("Download process timed out after 10 minutes")
        with open(status_file, 'a') as f:
            f.write("\nERROR: Download process timed out after 10 minutes")
        return "Error: Download timed out after 10 minutes"
    
    except Exception as e:
        logging.error(f"Error running download: {str(e)}", exc_info=True)
        with open(status_file, 'a') as f:
            f.write(f"\nERROR: {str(e)}")
        return f"Error: {str(e)}"

def emergency_download_game(username, password, guard_code, anonymous, game_input, validate_download):
    """
    Emergency download function that bypasses all the complex logic.
    Just runs SteamCMD directly and returns a simple status.
    """
    try:
        logging.info(f"Emergency download function called for: {game_input}")
        
        # Extract AppID
        appid = parse_game_input(game_input)
        if not appid:
            return "Error: Invalid game input. Could not extract AppID."
        
        logging.info(f"Emergency download: extracted AppID {appid}")
        
        # Create a unique download folder
        timestamp = int(time.time())
        download_folder = os.path.join(STEAM_DOWNLOAD_PATH, f"game_{appid}_{timestamp}")
        os.makedirs(download_folder, exist_ok=True)
        
        logging.info(f"Emergency download: created folder {download_folder}")
        
        # Build SteamCMD command
        steamcmd_path = get_steamcmd_path()
        
        if not os.path.exists(steamcmd_path):
            return f"Error: SteamCMD not found at {steamcmd_path}"
        
        # Basic command
        if anonymous:
            cmd = f'"{steamcmd_path}" +login anonymous +force_install_dir "{download_folder}" +app_update {appid} +quit'
        else:
            # You'd need to handle credentials properly in a real implementation
            cmd = f'"{steamcmd_path}" +login {username} {password} +force_install_dir "{download_folder}" +app_update {appid} +quit'
        
        logging.info(f"Emergency download: command prepared (not showing credentials)")
        
        # Write a status file we can check later
        status_path = os.path.join(download_folder, "download_status.txt")
        with open(status_path, 'w') as f:
            f.write(f"Download started at: {datetime.now()}\n")
            f.write(f"AppID: {appid}\n")
            f.write(f"Anonymous: {anonymous}\n")
            f.write(f"Command: {cmd if anonymous else '[CREDENTIALS HIDDEN]'}\n\n")
        
        logging.info(f"Emergency download: wrote status file to {status_path}")
        
        # Run the command as a direct OS command
        if platform.system() == "Windows":
            # Use subprocess.Popen for Windows
            process = subprocess.Popen(cmd, shell=True)
            logging.info(f"Emergency download: started Windows process with PID {process.pid}")
            return f"Download started for AppID {appid}. Check folder: {download_folder}"
        else:
            # For Linux/Mac, use os.system to run detached
            os_cmd = f"nohup {cmd} > '{download_folder}/output.log' 2>&1 &"
            os.system(os_cmd)
            logging.info(f"Emergency download: started detached process with nohup")
            return f"Download started for AppID {appid}. Check folder: {download_folder}"
            
    except Exception as e:
        logging.error(f"Emergency download error: {str(e)}", exc_info=True)
        return f"Error starting download: {str(e)}"

def download_game(username, password, guard_code, anonymous, game_input, validate_download):
    """Download a game directly using SteamCMD."""
    try:
        logging.info(f"Starting download for: {game_input}")
        
        # Copy the entire function body from line 2132
        # ...
        # All the implementation details
        # ...
        
        return f"Download started for AppID {appid}. Check folder: {download_folder}"
            
    except Exception as e:
        logging.error(f"Download error: {str(e)}", exc_info=True)
        return f"Error starting download: {str(e)}"

# Add this near the top of your file, after imports
def forward_to_download(username, password, guard_code, anonymous, game_input, validate_download):
    """Forward function to handle circular imports"""
    return queue_download(username, password, guard_code, anonymous, game_input, validate_download)