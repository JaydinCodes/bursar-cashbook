# Building the Windows release

This document is for developers, not bursars.

## Prerequisites

- Windows build machine with Python and project dependencies installed.
- PyInstaller (`python -m pip install -r requirements-build.txt`).
- Inno Setup 6 if an installer is required (`ISCC.exe` on `PATH`).

## Build

```powershell
./scripts/build_windows.ps1
```

The script runs the unit tests, creates `dist\\BursarCashbook.exe`, and, when Inno Setup is available, creates `release\\BursarCashbook-Setup.exe`.

`BursarCashbook.spec` packages only application modules and UI static files; fixtures, patch files, Git metadata, and repository data are not installer inputs. The executable is windowed (`console=False`).

## Storage and upgrades

The installed executable never writes accounting data beside itself. `app.config` uses `%LOCALAPPDATA%\\BursarCashbook` on Windows. Set `CASHBOOK_APP_DATA_DIR` to a disposable directory for local development or tests. Existing databases are opened in place; initialization never seeds or overwrites them.
