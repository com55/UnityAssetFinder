# Unity Asset Finder

Desktop utility to search Unity asset bundle files for a keyword.

The app scans files with UnityPy and provides a simple PySide6 GUI to:
- search by object Container
- search by object Name
- search by object Path ID
- browse matched files and open them in explorer
- copy file path or copy the file to another location

## Features

- Fast parallel search with selectable CPU cores
- Start, Pause, Resume in one main action button
- Stop button appears only while a search is running
- Hard pause behavior:
  - active worker processes are terminated
  - unfinished in-flight jobs are re-queued
  - resume starts a new worker pool and continues remaining jobs
- You can change CPU Used while paused, and the new value is applied on resume
- Progress states with visual feedback: Ready, Running, Paused, Stopping, Stopped, Done
- Remembers last used path, extension, CPU cores, and copy destination

## Quick Start (Windows Recommended)

Run the launcher script:
```bash
  run.cmd
```
What this does automatically:
- detects Python or uv
- creates/reuses .venv when needed
- installs or updates requirements
- runs the app

You do not need to run a separate install step first when using run.cmd.

## Manual Setup (Optional)

Use this only if you do not want to use run.cmd.

### Requirements

- Python 3.10 or newer
- Dependencies listed in requirements.txt:
  - UnityPy==1.23.0
  - PySide6==6.11.0

### Install & Run

Option 1: pip
```bash
  pip install -r requirements.txt
  python main.py
```
Option 2: uv
```bash
  uv pip install -r requirements.txt
  uv run main.py
```

## How To Use

1. Select a folder path.
2. Enter a keyword.
3. Choose where to search:
   - Name
   - Container
   - Path ID
4. Select file extension and CPU Used (cores).
5. Click Start.
6. While running:
   - Click Pause to hard-pause workers.
   - While paused, adjust CPU Used if needed.
   - Click Resume to continue.
   - Click Stop to cancel and return to idle state.

## Notes

- The keyword match is case-sensitive.
- Invalid or unreadable files are skipped.
- If no files match the selected extension, the progress maximum is set to 0.
