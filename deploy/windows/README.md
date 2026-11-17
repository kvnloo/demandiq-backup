# Run DemandIQ on the home PC (free beta)

No Docker, no admin rights, no open router ports. The app runs as you on `127.0.0.1:8320`, starts when you log in and
restarts itself if it crashes. The public address `https://demand.aitransportationtools.com` comes from the one shared
Cloudflare Tunnel (trafficiq repo, `deploy/tunnel/README.md`). Same recipe as TrafficOpsIQ `deploy/windows`.

## Start or update
In PowerShell, in the checkout that serves the app:

```powershell
git pull
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
```

The first run asks you to **choose the beta password** (any user name works) and installs Poppler (reads the count-sheet
PDFs) if missing. Settings live in `%LOCALAPPDATA%\demandiq\app.env`, one `NAME=value` per line:
- `APP_PASSWORD`: required; the server refuses to start without it.
- `ANTHROPIC_API_KEY`: needed to read count sheets with AI. Add it yourself, then re-run the script.
- `TYPESAFE_API_KEY`: optional (Jev intersection names).

## What's where
- Captured runs: `%LOCALAPPDATA%\demandiq\data\captures.db` (SQLite, survives restarts; no `DATABASE_URL` needed here).
- Log: `%LOCALAPPDATA%\demandiq\logs\server.log`. Task Scheduler → `DemandIQ`.
- Second copy for testing: `install.ps1 -Port 8321 -TaskName DemandIQ_Test` (shares the data folder and settings).
