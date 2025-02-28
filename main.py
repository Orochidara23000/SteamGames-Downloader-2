import gradio as gr
import os
import subprocess
import re
import requests
import zipfile
import tarfile
import platform
import time

# Function to check if SteamCMD is installed
def check_steamcmd():
    exe_path = "./steamcmd/steamcmd.exe" if platform.system() == "Windows" else "./steamcmd/steamcmd.sh"
    return "SteamCMD is installed." if os.path.exists(exe_path) else "SteamCMD is not installed."

# Function to install SteamCMD
def install_steamcmd():
    os_type = platform.system()
    if os_type == "Windows":
        url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
        response = requests.get(url)
        with open("steamcmd.zip", "wb") as f:
            f.write(response.content)
        with zipfile.ZipFile("steamcmd.zip", "r") as zip_ref:
            zip_ref.extractall("./steamcmd")
        os.remove("steamcmd.zip")
    elif os_type == "Linux":
        url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
        response = requests.get(url)
        with open("steamcmd.tar.gz", "wb") as f:
            f.write(response.content)
        with tarfile.open("steamcmd.tar.gz", "r:gz") as tar:
            tar.extractall("./steamcmd")
        os.remove("steamcmd.tar.gz")
    else:
        return "Unsupported OS."
    return "SteamCMD installed successfully."

# Function to parse game App ID from input
def parse_game_input(input_str):
    if input_str.isdigit():
        return input_str
    parts = input_str.split('/')
    for i, part in enumerate(parts):
        if part == 'app' and i + 1 < len(parts) and parts[i + 1].isdigit():
            return parts[i + 1]
    return None

# Function to parse SteamCMD output for progress and size
def parse_progress(line):
    # Look for progress percentage
    progress_match = re.search(r'Progress: (\d+\.\d+)%', line)
    if progress_match:
        return {"progress": float(progress_match.group(1))}
    
    # Look for total size in lines like "Downloading depot ... (size: 10.0 GB)"
    size_match = re.search(r'size: (\d+\.\d+) (\w+)', line)
    if size_match:
        size = float(size_match.group(1))
        unit = size_match.group(2)
        return {"total_size": size, "unit": unit}
    return None

# Function to download the game with progress updates
def download_game(username, password, guard_code, anonymous, game_input):
    appid = parse_game_input(game_input)
    if not appid:
        yield "Invalid game ID or URL"
        return

    # Construct SteamCMD login command
    login_cmd = "+login anonymous" if anonymous else f"+login {username} {password}"
    if guard_code and not anonymous:
        login_cmd += f" {guard_code}"
    cmd = f"{('./steamcmd/steamcmd.exe' if platform.system() == 'Windows' else './steamcmd/steamcmd')} {login_cmd} +app_update {appid} +quit"

    # Variables for progress tracking
    total_size = None
    unit = None
    start_time = time.time()

    # Run SteamCMD
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        info = parse_progress(line)
        if info:
            if "total_size" in info:
                total_size = info["total_size"]
                unit = info["unit"]
            elif "progress" in info and total_size:
                progress = info["progress"]
                downloaded = (progress / 100) * total_size
                elapsed = time.time() - start_time
                speed = downloaded / elapsed if elapsed > 0 else 0  # GB/s or unit/s
                remaining_size = total_size - downloaded
                remaining_time = remaining_size / speed if speed > 0 else 0
                yield (f"Downloading: {progress}% - {downloaded:.2f}/{total_size} {unit}\n"
                       f"Elapsed: {elapsed:.0f}s - Remaining: {remaining_time:.0f}s")
            else:
                yield f"Downloading: {info.get('progress', 0)}%"
        else:
            yield line.strip()

    process.wait()
    yield "Download completed successfully" if process.returncode == 0 else "Download failed"

# Gradio interface
with gr.Blocks(title="Steam Games Downloader") as app:
    gr.Markdown("# Steam Games Downloader")
    
    # System check section
    with gr.Row():
        check_button = gr.Button("Check SteamCMD Installation")
        install_button = gr.Button("Install SteamCMD", visible=False)
    
    # Login section
    with gr.Row():
        username = gr.Textbox(label="Username")
        password = gr.Textbox(label="Password", type="password")
        guard_code = gr.Textbox(label="Steam Guard Code (optional)")
        anonymous = gr.Checkbox(label="Anonymous Login")
    
    # Download section
    with gr.Row():
        game_input = gr.Textbox(label="Game ID or URL")
        download_button = gr.Button("Download")
    
    # Output display
    output = gr.Textbox(label="Status", lines=10)

    # Event handlers
    def show_install_button(result):
        return gr.update(visible="not installed" in result)
    
    check_button.click(check_steamcmd, outputs=output).then(
        show_install_button, inputs=output, outputs=install_button
    )
    install_button.click(install_steamcmd, outputs=output)
    download_button.click(download_game, inputs=[username, password, guard_code, anonymous, game_input], outputs=output)

app.launch(share=True)  # Share=True makes the URL publicly accessible