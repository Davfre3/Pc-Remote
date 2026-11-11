# -*- mode: python ; coding: utf-8 -*-
# Empaqueta PC Remote en una carpeta (arranca rapido, sin descomprimir): dist/PC Remote/PC Remote.exe
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = [("app/static", "static"), ("app/adb", "adb")] + collect_data_files("vgamepad") + collect_data_files("webview")   # vgamepad trae ViGEmClient.dll y el instalador del driver
binaries = collect_dynamic_libs("vgamepad") + collect_dynamic_libs("av") + collect_dynamic_libs("webview") + collect_dynamic_libs("pythonnet") + collect_dynamic_libs("clr_loader")
hidden = collect_submodules("pystray") + collect_submodules("webview") + ["clr", "clr_loader", "pythonnet"] + ["cryptography.hazmat.backends.openssl"] + collect_submodules("dxcam") + collect_submodules("comtypes")   # captura DXGI

a = Analysis(["app/server.py"], pathex=["app"], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PC Remote", console=False, icon="app/icon.ico",
          disable_windowed_traceback=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PC Remote")
