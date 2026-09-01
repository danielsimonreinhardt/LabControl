# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spec fuer LabControl -- ein Bild, gilt fuer jede Version.

Anders als die frueheren, pro Version manuell angelegten .spec-Dateien
(LabControl_v0.6.1.spec, LabControl_v0.8.0.spec, ...) liest diese Datei die
Versionsnummer dynamisch aus lab_gui/version.py, statt sie im Dateinamen und
im EXE-Namen hart zu verdrahten. Wird sowohl lokal als auch von der CI
(.github/workflows/build-exe.yml: `pyinstaller LabControl.spec`) verwendet --
beide lesen dieselbe Quelle, damit EXE-Name und .spec-Name nie auseinanderlaufen.

Anders als die aelteren .spec-Dateien ist diese NICHT in .gitignore (siehe
dort: "*.spec" mit Ausnahme fuer genau diesen Dateinamen), da die CI sie beim
Checkout braucht -- sie enthaelt Splash-Screen und Qt-Submodul-Excludes, die
sich nicht per PyInstaller-CLI-Flag nachbilden lassen (siehe FEATURES.md
Punkt 5 fuer den Hintergrund).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH) / "lab_gui"))
from version import __version__  # noqa: E402

EXE_NAME = f"LabControl_v{__version__}"

# Splash-Bild vor jedem Build frisch aus der aktuellen version.py erzeugen
# (siehe tools/generate_splash.py) statt sich auf ein manuell aktuell
# gehaltenes, in Git getracktes PNG zu verlassen -- genau das ging beim
# v0.9.0->v0.9.3-Bump schief (BUGS.md #13: Splash zeigte noch "v0.9.0" in
# der v0.9.3-.exe, weil das Regenerier-Skript vergessen wurde). Damit kann
# splash.png nie mehr von version.py abweichen.
sys.path.insert(0, str(Path(SPECPATH) / "tools"))
import generate_splash  # noqa: E402
generate_splash.main()

a = Analysis(
    ['lab_gui/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('lab_gui/icons', 'lab_gui/icons'), ('lab_gui/translations', 'lab_gui/translations')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Reines QtWidgets-Programm (kein QML/3D/WebEngine/Charts/Standort/
    # virtuelle Tastatur, siehe grep-Check "keine Treffer" ueber lab_gui/) --
    # der PySide6-PyInstaller-Hook buendelt diese Submodule trotzdem
    # standardmaessig mit (u.a. Qt6WebEngineCore.dll allein 205 MB von
    # zuletzt 258 MB Gesamtgroesse, siehe FEATURES.md Punkt 5). Explizit
    # ausschliessen spart den Loewenanteil der .exe-Groesse und damit der
    # Onefile-Entpackzeit bei jedem Start.
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
        "PySide6.QtQuickControls2", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtVirtualKeyboard",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DLogic",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
        "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSql", "PySide6.QtPdf",
        "PySide6.QtPdfWidgets", "PySide6.QtRemoteObjects", "PySide6.QtGraphs",
        "PySide6.QtGraphsWidgets", "PySide6.QtHttpServer", "PySide6.QtSpatialAudio",
        "PySide6.QtStateMachine", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
        "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtNetworkAuth",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Bootloader-Splash: erscheint bereits waehrend der Onefile-Selbstentpackung,
# also bevor der Python-Interpreter ueberhaupt laeuft -- deckt damit genau
# die Phase ab, die sich beim Start bisher wie "haengt" anfuehlt (siehe
# FEATURES.md Punkt 5). Geschlossen wird er in lab_gui/main.py per
# pyi_splash.close(), sobald das Hauptfenster steht.
splash = Splash(
    'lab_gui/icons/splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(20, 255),
    text_size=11,
    text_color='#e8e6e1',
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
