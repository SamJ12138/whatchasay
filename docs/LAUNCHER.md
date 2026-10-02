# One-Click Launcher Guide

This guide explains how to use the one-click launcher to start the Subtitle Translator without manually running commands.

## Quick Start

### Windows
1. Double-click `Start_Subtitle_Translator.bat`
2. Wait for setup to complete (first run may take several minutes)
3. The health check page will open in your browser when ready

### macOS
1. Double-click `Start_Subtitle_Translator.command`
   - If blocked by Gatekeeper, right-click → "Open" and confirm
   - **Note**: Scripts automatically fix permission issues from zip extraction
2. Wait for setup to complete
3. The health check page will open in your browser when ready

**Tip**: If double-clicking doesn't work, open Terminal and run:
```bash
cd /path/to/subtitle-translator
bash Start_Subtitle_Translator.command
```
The script will automatically make itself executable for future runs.

## What the Launcher Does

The launcher automates the entire setup process:

1. **Checks Python** - Verifies Python 3.10+ is installed
2. **Creates Virtual Environment** - Isolates dependencies in `backend/venv`
3. **Installs Dependencies** - Runs `pip install -r requirements.txt`
4. **Checks Ollama** - Verifies Ollama is installed for AI post-editing
5. **Sets Up Models**:
   - Pulls `qwen2.5:7b` base model
   - Creates `subedit:stable` custom model from Modelfile
6. **Starts Server** - Launches the backend on `http://127.0.0.1:8765`
7. **Opens Browser** - Opens the health check page to confirm success

## Troubleshooting

### macOS: "Permission denied" or script won't run
This typically happens after extracting from a zip file:

1. **Automatic fix**: The launcher scripts have self-repair built in. Try running via Terminal:
   ```bash
   bash Start_Subtitle_Translator.command
   ```
   This will automatically fix permissions for future double-click launches.

2. **Manual fix**: If needed, run:
   ```bash
   chmod +x Start_Subtitle_Translator.command Stop_Subtitle_Translator.command
   ```

3. **Gatekeeper block**: If macOS says the app is from an unidentified developer:
   - Right-click the .command file → "Open" → Click "Open" in the dialog
   - Or go to System Preferences → Security & Privacy → Click "Open Anyway"

### "Python is not installed"
- Download Python 3.10+ from https://python.org
- During installation, check "Add Python to PATH"
- Restart the launcher

### "Failed to create virtual environment"
- Ensure you have write permissions in the project folder
- Try running as administrator (Windows) or with sudo (macOS)

### "Failed to install dependencies"
- Check your internet connection
- Try running manually:
  ```bash
  cd backend
  python -m venv venv
  venv/Scripts/activate  # Windows
  # or: source venv/bin/activate  # macOS
  pip install -r requirements.txt
  ```

### "Ollama is not installed"
Ollama is optional but recommended for better translations.
- Download from https://ollama.ai
- Run the installer
- Restart the launcher

### "Server failed to start"
- Check if port 8765 is already in use
- Look for error messages in the terminal
- Try running manually: `python run.py`

### Model download is slow
First-time model downloads can take 10-20 minutes:
- Translation models: ~2GB total
- Ollama qwen2.5:7b: ~4GB

These are cached after first download.

## Stopping the Server

### Windows
- Close the launcher window, OR
- Double-click `Stop_Subtitle_Translator.bat`

### macOS
- Press Ctrl+C in the terminal, OR
- Run `./Stop_Subtitle_Translator.command`

## Custom Configuration

### Change Server Port
Edit `Start_Subtitle_Translator.bat` or `.command`:
```bash
set "SERVER_PORT=8080"  # Windows
SERVER_PORT="8080"       # macOS
```

### Skip Ollama Setup
If you don't want AI post-editing, Ollama is optional. The launcher will warn but continue without it.

### Use Different Ollama Model
Edit `backend/ollama/Modelfile` to use a different base model.

## Running Without Launcher

If you prefer manual control:

```bash
# 1. Navigate to backend
cd backend

# 2. Create and activate venv
python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Set up Ollama
ollama pull qwen2.5:7b
ollama create subedit:stable -f ollama/Modelfile

# 5. Start server
python run.py
```

## System Requirements

- **Python**: 3.10 or higher
- **RAM**: 8GB minimum (16GB recommended)
- **Storage**: ~10GB for models
- **OS**: Windows 10/11, macOS 10.15+, Linux
- **Ollama** (optional): For AI post-editing

## First Run Performance

The first translation after startup will be slow (~10-30 seconds) as models load into memory. Subsequent translations are much faster.

To reduce first-translation latency, enable model warmup:
```bash
python run.py --warmup
```
