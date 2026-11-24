# PyInstaller spec for Sushi: Windows (.exe folder) and macOS (.app).
# Build from the repo root:  pyinstaller packaging/sushi.spec --noconfirm
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).parent
IS_MAC = sys.platform == "darwin"

datas = [
    (str(ROOT / "sushi" / "data" / "logo.png"), "sushi/data"),
    (str(ROOT / "sushi" / "data" / "logo.ico"), "sushi/data"),
    (str(ROOT / "sushi" / "data" / "bubble.js"), "sushi/data"),
    (str(ROOT / "sushi" / "data" / "extension"), "sushi/data/extension"),
    (str(ROOT / "sushi" / "assets"), "sushi/assets"),
]
# The company list seeds the job feed on first run, if present.
if (ROOT / "sushi" / "data" / "companies.jsonl").exists():
    datas.append((str(ROOT / "sushi" / "data" / "companies.jsonl"), "sushi/data"))

binaries, hiddenimports = [], []
# Packages that load files or plugins by name at run time.
for pkg in ("browser_use", "pdfplumber", "pdfminer", "tokenizers", "onnxruntime",
            "anthropic", "openai", "reportlab", "docx"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass
hiddenimports += ["sushi"]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "tensorflow", "matplotlib", "tkinter", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Sushi",
    console=False,
    icon=str(ROOT / "sushi" / "data" / ("logo.icns" if IS_MAC else "logo.ico")),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Sushi", upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Sushi.app",
        icon=str(ROOT / "sushi" / "data" / "logo.icns"),
        bundle_identifier="app.sushi.desktop",
        info_plist={"NSHighResolutionCapable": True,
                    "LSMinimumSystemVersion": "11.0",
                    "CFBundleShortVersionString": "1.0.0"},
    )
