"""Keeps the DemandIQ server up on this PC (started at logon by install.ps1).

Reads APP_PASSWORD (and ANTHROPIC_API_KEY, TYPESAFE_API_KEY if you add them) from %LOCALAPPDATA%\\demandiq\\app.env,
keeps captured runs in ...\\data\\captures.db and the log in ...\\logs\\server.log, listens on 127.0.0.1 only:
the Cloudflare Tunnel is the one way in. Same recipe as TrafficOpsIQ deploy/windows.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

home = Path(os.environ["LOCALAPPDATA"]) / "demandiq"
repo = Path(__file__).resolve().parents[2]
port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "8320"

env = dict(os.environ, PYTHONUNBUFFERED="1", MPLBACKEND="Agg",
           CAPTURE_DB_PATH=str(home / "data" / "captures.db"))
# pdf2image shells out to Poppler's pdftoppm to read uploaded count sheets (winget oschwartz10612.Poppler).
poppler = sorted((Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages").glob(
    "oschwartz10612.Poppler_*/poppler-*/Library/bin"))
if poppler:
    env["PATH"] = f"{poppler[-1]}{os.pathsep}{env['PATH']}"
for line in (home / "app.env").read_text(encoding="utf-8-sig").splitlines():
    key, sep, value = line.partition("=")
    if sep:
        env[key.strip()] = value
if not env.get("APP_PASSWORD"):
    sys.exit("APP_PASSWORD is empty in app.env; refusing to serve the app (and its API key) with no password")

cmd = [sys.executable, "-m", "uvicorn", "app:app", "--app-dir", str(repo / "backend"),
       "--host", "127.0.0.1", "--port", port]

# shortcut: server.log grows forever (a few KB a day of access lines); rotate if it ever matters.
with open(home / "logs" / "server.log", "a", buffering=1, encoding="utf-8") as log:
    while True:  # restart a crashed server; the task's own restart covers this script dying
        log.write(f"--- start {time.ctime()} port {port}\n")
        code = subprocess.call(cmd, cwd=repo / "backend", env=env, stdout=log, stderr=subprocess.STDOUT)
        log.write(f"--- exited {code} {time.ctime()}, restarting in 5 s\n")
        time.sleep(5)
