"""Generate PyInstaller version metadata from app.version (the sole source)."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.version import APP_VERSION


def main() -> None:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", APP_VERSION)
    if not match:
        raise SystemExit(f"Unsupported application version: {APP_VERSION}")
    numeric = ",".join((*match.groups(), "0"))
    target = Path(__file__).resolve().parents[1] / "installer/version_info.txt"
    target.write_text(
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers=({numeric}), prodvers=({numeric}), mask=0x3f, flags=0x0, OS=0x4, fileType=0x1, subtype=0x0, date=(0,0)),\n"
        "  kids=[StringFileInfo([StringTable('040904B0', ["
        f"StringStruct('CompanyName', 'Ledgerly'), StringStruct('FileDescription', 'Ledgerly school cashbook'), StringStruct('FileVersion', '{APP_VERSION}'), "
        f"StringStruct('ProductName', 'Ledgerly'), StringStruct('ProductVersion', '{APP_VERSION}')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n"
        ")\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
