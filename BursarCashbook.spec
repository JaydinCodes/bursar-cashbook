# PyInstaller build definition. Run ``pyinstaller BursarCashbook.spec`` on Windows.
# Application imports are analysed normally; keep this explicit list small so
# development-only packages and unrelated repository files cannot enter a build.
datas = [("app/static", "app/static")]
binaries = []
hiddenimports = ["app.main", "multipart.multipart", "xlrd", "xlutils.copy", "webview", "webview.platforms.winforms"]

a = Analysis(
    ["app/launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # These are unrelated packages present in some developer Python installs.
    # None is imported by Bursar Cashbook; excluding them keeps builds bounded.
    excludes=["torch", "IPython", "jedi", "black", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas,
    name="BursarCashbook", console=False, debug=False,
    version="installer/version_info.txt",
)
