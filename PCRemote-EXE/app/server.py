"""PC Remote (programa del PC): cada movil se convierte en un mando virtual (Xbox 360, PlayStation 4 o Switch).

Requiere el driver ViGEmBus (el instalador lo trae). Hasta 6 moviles a la vez, por WiFi local, Bluetooth directo
o cable USB con depuracion; cada uno entra con el PIN (y, por WiFi, tambien con el QR).
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import mss
import numpy as np
import psutil
import qrcode
import qrcode.image.svg
import vgamepad as vg
from aiohttp import WSMsgType, web

try:  # captura por DXGI (la de OBS/Parsec): ~2 ms por foto frente a ~25 ms de mss. Si falta, se usa mss
    import dxcam
except Exception:
    dxcam = None

try:  # video H.264 propio (sin Moonlight); si falta, la pantalla sigue yendo en JPEG
    import av
    from fractions import Fraction
except ImportError:
    av = None

VERSION = "1.1.4"
# Aqui se publica la version mas nueva (version.json); el programa y la app del movil la consultan para actualizarse
UPDATE_URL = "https://raw.githubusercontent.com/Davfre3/Pc-Remote/main/releases/version.json"

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(__file__).parent
STATIC = APP_DIR / "static"
# Datos del usuario (PIN, ajustes, disenos, certificado): en el .exe van a %APPDATA%, porque junto al programa
# (Archivos de programa) no se puede escribir; en desarrollo, a una carpeta data/ al lado del codigo
DATA = (Path(os.environ.get("APPDATA", str(Path.home()))) / "PCRemote") if FROZEN else APP_DIR / "data"
try:
    DATA.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
MAX_PADS = 6  # hasta 6 moviles a la vez, cada uno por la via que quiera (WiFi, Bluetooth o cable)
# XInput solo expone 4 mandos Xbox: los puestos 5 y 6 se crean como DualShock 4, que Windows no limita a 4
XINPUT_PADS = 4
# Un toque rapido cuyo pulsar y soltar llegan juntos (rafaga de WiFi) cae dentro de un mismo frame del juego
# y este nunca lo ve: se mantiene pulsado al menos este tiempo (~2 frames a 60 fps)
MIN_HOLD = 0.035

XB = vg.XUSB_BUTTON
XBOX_BUTTONS = {
    "A": XB.XUSB_GAMEPAD_A, "B": XB.XUSB_GAMEPAD_B, "X": XB.XUSB_GAMEPAD_X, "Y": XB.XUSB_GAMEPAD_Y,
    "LB": XB.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": XB.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "BACK": XB.XUSB_GAMEPAD_BACK, "START": XB.XUSB_GAMEPAD_START,
    "L3": XB.XUSB_GAMEPAD_LEFT_THUMB, "R3": XB.XUSB_GAMEPAD_RIGHT_THUMB,
    "HOME": XB.XUSB_GAMEPAD_GUIDE,
    "UP": XB.XUSB_GAMEPAD_DPAD_UP, "DOWN": XB.XUSB_GAMEPAD_DPAD_DOWN,
    "LEFT": XB.XUSB_GAMEPAD_DPAD_LEFT, "RIGHT": XB.XUSB_GAMEPAD_DPAD_RIGHT,
}

DB = vg.DS4_BUTTONS
# Nombres logicos de la pagina -> botones del DualShock 4
PS4_BUTTONS = {
    "A": DB.DS4_BUTTON_CROSS, "B": DB.DS4_BUTTON_CIRCLE,
    "X": DB.DS4_BUTTON_SQUARE, "Y": DB.DS4_BUTTON_TRIANGLE,
    "LB": DB.DS4_BUTTON_SHOULDER_LEFT, "RB": DB.DS4_BUTTON_SHOULDER_RIGHT,
    "BACK": DB.DS4_BUTTON_SHARE, "START": DB.DS4_BUTTON_OPTIONS,
    "L3": DB.DS4_BUTTON_THUMB_LEFT, "R3": DB.DS4_BUTTON_THUMB_RIGHT,
}
PS4_SPECIAL = {
    "HOME": vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_PS,
    "TOUCHPAD": vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_TOUCHPAD,
}
DD = vg.DS4_DPAD_DIRECTIONS
# (arriba, abajo, izquierda, derecha) -> direccion de la cruceta del DS4
PS4_DPAD = {
    (1, 0, 0, 0): DD.DS4_BUTTON_DPAD_NORTH, (1, 0, 0, 1): DD.DS4_BUTTON_DPAD_NORTHEAST,
    (0, 0, 0, 1): DD.DS4_BUTTON_DPAD_EAST, (0, 1, 0, 1): DD.DS4_BUTTON_DPAD_SOUTHEAST,
    (0, 1, 0, 0): DD.DS4_BUTTON_DPAD_SOUTH, (0, 1, 1, 0): DD.DS4_BUTTON_DPAD_SOUTHWEST,
    (0, 0, 1, 0): DD.DS4_BUTTON_DPAD_WEST, (1, 0, 1, 0): DD.DS4_BUTTON_DPAD_NORTHWEST,
}


DIAG = DATA / "diag.log"


def diag(text):
    """Registro de diagnostico: conexiones, cortes y pausas largas entre mensajes del movil."""
    try:
        with DIAG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
    except OSError:
        pass


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class XboxPad:
    kind = "xbox"
    ready_delay = 0.0

    def __init__(self):
        self.pad = vg.VX360Gamepad()
        self.neutral()

    def neutral(self):
        # Un mando recien creado reporta valores sin inicializar hasta el primer update; los juegos
        # fijan ahi su punto neutro, asi que se manda un reposo explicito
        self.sticks(0, 0, 0, 0)
        self.triggers(0, 0)
        self.update()

    def button(self, name, down):
        b = XBOX_BUTTONS.get(name)
        if b is None:
            return False
        (self.pad.press_button if down else self.pad.release_button)(button=b)
        return True

    def sticks(self, lx, ly, rx, ry):
        self.pad.left_joystick_float(lx, ly)
        self.pad.right_joystick_float(rx, ry)

    def triggers(self, l, r):
        self.pad.left_trigger_float(l)
        self.pad.right_trigger_float(r)

    def on_rumble(self, cb):
        # vgamepad exige estos nombres de parametro exactos
        def handler(client, target, large_motor, small_motor, led_number, user_data):
            cb(max(large_motor, small_motor))
        self.pad.register_notification(callback_function=handler)

    def close(self):
        self.pad.unregister_notification()

    def update(self):
        self.pad.update()


class SwitchPad(XboxPad):
    """Windows no tiene un driver virtual de Switch Pro: se emula un mando XInput y es la pagina
    la que reparte los botones por posicion (A a la derecha, B abajo, X arriba, Y a la izquierda)."""
    kind = "switch"


class Ps4Pad:
    kind = "ps4"
    # Windows tarda ~1 s en registrar un DS4 nuevo; lo pulsado antes se pierde
    ready_delay = 1.2

    def __init__(self):
        self.pad = vg.VDS4Gamepad()
        self.dpad = {"UP": 0, "DOWN": 0, "LEFT": 0, "RIGHT": 0}
        self.neutral()

    neutral = XboxPad.neutral

    def button(self, name, down):
        if name in self.dpad:
            self.dpad[name] = 1 if down else 0
            d = PS4_DPAD.get((self.dpad["UP"], self.dpad["DOWN"], self.dpad["LEFT"], self.dpad["RIGHT"]),
                             DD.DS4_BUTTON_DPAD_NONE)
            self.pad.directional_pad(direction=d)
        elif name in PS4_BUTTONS:
            (self.pad.press_button if down else self.pad.release_button)(button=PS4_BUTTONS[name])
        elif name in PS4_SPECIAL:
            (self.pad.press_special_button if down else self.pad.release_special_button)(
                special_button=PS4_SPECIAL[name])
        else:
            return False
        return True

    def sticks(self, lx, ly, rx, ry):
        # En el DS4 el eje Y es 0 = arriba, asi que se invierte respecto a Xbox
        self.pad.left_joystick_float(lx, -ly)
        self.pad.right_joystick_float(rx, -ry)

    def triggers(self, l, r):
        self.pad.left_trigger_float(l)
        self.pad.right_trigger_float(r)
        # Algunos juegos leen L2/R2 como boton digital ademas del eje
        for val, b in ((l, DB.DS4_BUTTON_TRIGGER_LEFT), (r, DB.DS4_BUTTON_TRIGGER_RIGHT)):
            (self.pad.press_button if val > 0.5 else self.pad.release_button)(button=b)

    def on_rumble(self, cb):
        def handler(client, target, large_motor, small_motor, led_number, user_data):
            cb(max(large_motor, small_motor))
        self.pad.register_notification(callback_function=handler)

    def close(self):
        self.pad.unregister_notification()

    def update(self):
        self.pad.update()


class PsXInputPad(XboxPad):
    """Football Life 2026 lee mal el stick derecho de un DS4 virtual (con Xbox y Switch va bien), asi que
    la disposicion PlayStation de la pagina se emula sobre un mando XInput. 'ps4native' sigue siendo el DS4 virtual."""
    kind = "ps4"


PAD_TYPES = {"xbox": XboxPad, "ps4": PsXInputPad, "switch": SwitchPad, "ps4native": Ps4Pad}


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no envia nada; solo elige la interfaz
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


SKIP_NETS = ("virtualbox", "vbox", "vmware", "vethernet", "hyper-v", "docker", "wsl", "loopback",
             "bluetooth", "rndis", "android", "apple")   # virtuales y anclajes (no se usan): ningun movil llega por ahi


def connections():
    """Direcciones del PC en la red local (WiFi o cable de red) -> [("WiFi", ip)]; la ruta por defecto va primero."""
    found = []
    try:
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            if any(v in name.lower() for v in SKIP_NETS):
                continue
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.", "192.168.42.", "192.168.56.")):
                    found.append(("WiFi", a.address))
    except Exception:
        pass
    main_ip = lan_ip()
    if not any(ip == main_ip for _, ip in found) and main_ip != "127.0.0.1":
        found.append(("WiFi", main_ip))
    found.sort(key=lambda c: c[1] != main_ip)
    return found


_adb_used = False


def stop_adb():
    """adb deja un servidor en segundo plano que sigue vivo al cerrar el programa (y bloquea sus archivos):
    al salir se cierra, si lo hemos usado."""
    if not _adb_used:
        return
    adb = find_adb()
    if adb:
        try:
            subprocess.run([adb, "kill-server"], capture_output=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass


def quit_app(code=0):
    """Salida del programa: cierra adb y termina (os._exit no pasa por atexit)."""
    stop_adb()
    os._exit(code)


def find_adb():
    """adb del PATH o, si no, el de las herramientas de Android (platform-tools) donde suelen instalarse."""
    global _adb_used
    own = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "adb" / "adb.exe"   # el que lleva el programa
    if own.exists():
        _adb_used = True
        return str(own)
    found = shutil.which("adb")
    if found:
        _adb_used = True
        return found
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk")]
    for r in roots:
        if r and (Path(r) / "platform-tools" / "adb.exe").exists():
            _adb_used = True
            return str(Path(r) / "platform-tools" / "adb.exe")
    return None


def adb_reverse(port):
    """Con el movil por cable y depuracion USB, 'adb reverse' lo deja abrir localhost sin red."""
    adb = find_adb()
    if not adb:
        return False
    try:
        r = subprocess.run([adb, "reverse", f"tcp:{port}", f"tcp:{port}"], capture_output=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def saved_token():
    """PIN que se conserva entre arranques: asi la direccion (y el QR) del movil sigue valiendo al reiniciar."""
    f = DATA / "token.txt"
    try:
        t = f.read_text().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_hex(3)
    try:
        f.write_text(t)
    except OSError:
        pass
    return t


def make_ssl_context():
    """Certificado autofirmado (se crea una vez, sin openssl): los navegadores solo dan el giroscopio en https."""
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    cert_dir = DATA / "cert"
    crt, key = cert_dir / "pcremote.crt", cert_dir / "pcremote.key"
    if not (crt.exists() and key.exists()):
        cert_dir.mkdir(parents=True, exist_ok=True)
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PC Remote")])
        san = [x509.DNSName("localhost")]
        for ip in local_ips():
            try:
                san.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except ValueError:
                pass
        now = datetime.datetime.now(datetime.timezone.utc)
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=825))
             .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(k, hashes.SHA256()))
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
        crt.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    return ctx


NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}  # el navegador del movil no debe quedarse con una pagina vieja


async def index(request):
    return web.FileResponse(STATIC / "index.html", headers=NO_CACHE)


# ---- panel del PC: ventana de la aplicacion (QR, jugadores conectados, pantalla) ----
def local_ips():
    ips = {"127.0.0.1", "::1"}
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    ips.add(a.address.split("%")[0])
    except Exception:
        pass
    return ips


def panel_guard(request):
    """El panel solo se abre desde este mismo PC y con el PIN: un movil de la red no puede expulsar a nadie."""
    if request.query.get("k") != request.app["token"] or request.remote not in local_ips():
        raise web.HTTPForbidden(text="El panel solo se abre desde este PC")


async def panel_page(request):
    panel_guard(request)
    return web.FileResponse(STATIC / "panel.html", headers=NO_CACHE)


VIA_IDS = ["wifi", "bluetooth", "adb"]
VIA_KIND = {"wifi": "WiFi", "bluetooth": "BTDirecto", "adb": "ADB"}
KIND_NAMES = {"WiFi": "WiFi local", "BTDirecto": "Bluetooth", "ADB": "Cable USB"}
KIND_HINTS = {"WiFi": "El móvil y el PC en la misma red WiFi.",
              "BTDirecto": "Sin pantalla del PC (solo botones). En la app: Bluetooth > elige este PC.",
              "ADB": "Cable conectado y «Depuración USB» activada en el móvil."}
KIND_STEPS = {"WiFi": "Conecta el PC y el móvil a la misma red WiFi (puede ser la del punto de acceso de otro móvil).",
              "BTDirecto": "El Bluetooth de este PC no está disponible. Actívalo en Configuración > Bluetooth y vuelve a abrir PC Remote.",
              "ADB": "1) En el móvil activa Opciones de desarrollador > Depuración USB.\n2) Conéctalo con el cable y acepta el aviso.\n3) Aparecerá aquí sin más pasos."}


def all_connections(app):
    """Todas las vias por las que un movil puede llegar ahora mismo (el servidor escucha en todas a la vez):
    WiFi local, Bluetooth directo y cable con depuracion USB. Las credenciales son la direccion y el PIN."""
    port, token, https = app["port"], app["token"], app["info"].get("https")
    vias = app["info"].get("vias", VIA_IDS)
    out = []
    for kind, ip in (connections() if "wifi" in vias else []):
        out.append({"id": f"{kind}-{ip}", "kind": kind, "name": KIND_NAMES[kind], "hint": KIND_HINTS[kind],
                    "ip": ip, "address": f"{ip}:{port}", "url": f"http://{ip}:{port}/?k={token}",
                    "web_url": f"https://{ip}:{port + 1}/?k={token}" if https else f"http://{ip}:{port}/?k={token}"})
    if "bluetooth" in vias and app["info"].get("bt_on"):
        out.append({"id": "BT", "kind": "BTDirecto", "name": KIND_NAMES["BTDirecto"], "hint": KIND_HINTS["BTDirecto"],
                    "nourl": True, "ip": "", "address": "", "url": "", "web_url": ""})
    if "adb" in vias and adb_ready():
        out.append({"id": "ADB", "kind": "ADB", "name": KIND_NAMES["ADB"], "hint": KIND_HINTS["ADB"], "ip": "localhost",
                    "address": f"localhost:{port}", "url": f"http://localhost:{port}/?k={token}",
                    "web_url": f"http://localhost:{port}/?k={token}"})
    return out

_BT_KEEP = []


async def panel_state(request):
    panel_guard(request)
    app, now = request.app, time.monotonic()
    players = []
    for slot in range(MAX_PADS):
        m = app["players"].get(slot)
        players.append(None if not m else {
            "slot": slot + 1, "kind": m["kind"], "ip": m["ip"], "via": m.get("via", ""), "seconds": int(now - m["t0"]),
            "idle": round(now - m["last"], 1), "rtt": m.get("rtt")})
    hc = h264_choice()
    conns = await asyncio.get_running_loop().run_in_executor(None, all_connections, app)
    first = next((c for c in conns if not c.get("nourl")), None)
    present = {c["kind"] for c in conns}
    missing = [{"kind": k, "name": KIND_NAMES[k], "hint": KIND_HINTS[k], "steps": KIND_STEPS[k]}
               for k in (VIA_KIND[v] for v in app["info"].get("vias", VIA_IDS)) if k not in present]
    return web.json_response({**app["info"], "screen": bool(app["feed"]), "encoder": hc[0] if hc else None, "missing": missing,
                              "players": players, "connections": conns, "update": update_info(), "version": VERSION, "max_pads": MAX_PADS, "xinput_pads": XINPUT_PADS,
                              "url": first["url"] if first else "", "web_url": first["web_url"] if first else "",
                              "address": first["address"] if first else "—"}, headers=NO_CACHE)


async def panel_qr(request):
    """QR con la direccion y el PIN. Solo para WiFi: es la unica via que se escribe/escanea como direccion."""
    panel_guard(request)
    conns = await asyncio.get_running_loop().run_in_executor(None, all_connections, request.app)
    want = request.query.get("i")
    conn = next((c for c in conns if c["kind"] == "WiFi" and c["id"] == want), next((c for c in conns if c["kind"] == "WiFi"), None))
    if conn is None:
        raise web.HTTPNotFound(text="sin WiFi")
    img = qrcode.make(conn["url"], image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    return web.Response(body=img.to_string(), content_type="image/svg+xml", headers=NO_CACHE)


async def panel_relaunch(request):
    """«Cambiar conexiones»: vuelve a la pantalla de inicio."""
    panel_guard(request)
    asyncio.get_running_loop().call_later(0.4, relaunch_setup)
    return web.json_response({"ok": True})


async def panel_update_check(request):
    panel_guard(request)
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, update_check), headers=NO_CACHE)


async def panel_update_install(request):
    panel_guard(request)
    return web.json_response(update_start(), headers=NO_CACHE)


async def panel_bluetooth(request):
    """Deja a mano la pantalla de Bluetooth de Windows: mientras esta abierta, el PC es visible para los moviles que buscan."""
    panel_guard(request)
    try:
        os.startfile("ms-settings:bluetooth")
        return web.json_response({"ok": True})
    except OSError:
        return web.json_response({"ok": False})


async def panel_screen(request):
    """Enciende o apaga la pantalla en los moviles sin reiniciar: apagada, se corta la captura y se cierran los visores."""
    panel_guard(request)
    app = request.app
    on = (await request.json()).get("on")
    if on and not app["feed"]:
        app["feed"] = ScreenFeed()
    elif not on and app["feed"]:
        app["feed"] = None
        for w in list(app["screen_wss"]):
            await w.close()
    save_settings({"pantalla": bool(on)})
    return web.json_response({"screen": bool(app["feed"])})


async def panel_kick(request):
    panel_guard(request)
    m = request.app["players"].get(int(request.query.get("slot", "0")) - 1)
    if m:
        await m["ws"].close()
    return web.json_response({"ok": bool(m)})


# Ajustes del programa (como conecta, https, pantalla): se guardan para no preguntar en cada arranque
SETTINGS = DATA / "settings.json"


def load_settings():
    try:
        d = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(update):
    d = {**load_settings(), **update}
    try:
        SETTINGS.write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError:
        pass


# ---- actualizaciones: se mira version.json en GitHub y, si hay una mas nueva, se baja el instalador y se ejecuta en silencio ----
UPDATE = {"state": "idle", "pct": 0, "error": "", "latest": "", "notes": "", "url": "", "sha256": ""}


def _vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])


def update_check():
    """Consulta GitHub. Deja el resultado en UPDATE y lo devuelve."""
    import urllib.request
    try:
        req = urllib.request.Request(UPDATE_URL + f"?t={int(time.time())}", headers={"User-Agent": "PCRemote"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        UPDATE.update(latest=str(d.get("version", "")), notes=str(d.get("notes", "")), url=str(d.get("exe", "")),
                      sha256=str(d.get("exe_sha256", "")).lower(), error="")
    except Exception as e:
        UPDATE["error"] = "No se pudo comprobar (¿hay internet?)"
        diag(f"actualizar: {e}")
    return update_info()


def update_info():
    return {"current": VERSION, "latest": UPDATE["latest"], "notes": UPDATE["notes"], "state": UPDATE["state"],
            "pct": UPDATE["pct"], "error": UPDATE["error"], "frozen": FROZEN,
            "available": bool(UPDATE["latest"]) and _vt(UPDATE["latest"]) > _vt(VERSION)}


def _update_worker():
    import tempfile
    import urllib.request
    try:
        dest = Path(tempfile.gettempdir()) / "PC-Remote-Instalador-nuevo.exe"
        req = urllib.request.Request(UPDATE["url"], headers={"User-Agent": "PCRemote"})
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=30) as r, dest.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                UPDATE["pct"] = int(got * 100 / total) if total else 0
        if UPDATE["sha256"] and h.hexdigest() != UPDATE["sha256"]:
            dest.unlink(missing_ok=True)
            raise ValueError("la descarga esta corrupta (no coincide la huella)")
        UPDATE.update(state="installing", pct=100)
        time.sleep(0.8)   # la ventana alcanza a ensenar «Instalando»
        try:   # la ventana del panel es otro proceso del mismo .exe: si queda abierta bloquea los archivos
            for c in psutil.Process().children(recursive=True):
                c.terminate()
        except Exception:
            pass
        # /SILENT: solo la barra de progreso (Windows pedira permiso de administrador); al terminar abre el programa
        subprocess.Popen([str(dest), "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART"], close_fds=True)
        time.sleep(0.5)
        quit_app(0)
    except Exception as e:
        diag(f"actualizar: {e}")
        UPDATE.update(state="error", error=f"No se pudo actualizar: {e}")


def update_start():
    if not FROZEN:
        UPDATE.update(state="error", error="En modo desarrollo no se actualiza solo")
    elif UPDATE["state"] not in ("downloading", "installing") and UPDATE["url"]:
        UPDATE.update(state="downloading", pct=0, error="")
        threading.Thread(target=_update_worker, daemon=True).start()
    return update_info()


_window = None


def open_panel_window(url):
    """Abre el panel en una ventana propia de PC Remote (otro proceso del mismo programa, con WebView2).
    Si eso falla, usa Edge como ventana de aplicacion y, en ultimo caso, el navegador."""
    global _window
    if _window is not None and _window.poll() is None:
        return  # ya hay una ventana abierta
    cmd = [sys.executable, "--ventana", url] if FROZEN else [sys.executable, str(Path(__file__).resolve()), "--ventana", url]
    try:
        _window = subprocess.Popen(cmd, cwd=str(APP_DIR))
        return
    except OSError as e:
        diag(f"ventana propia: {e}")
    for exe in (shutil.which("msedge"), r"C:Program Files (x86)MicrosoftEdgeApplicationmsedge.exe",
                r"C:Program FilesMicrosoftEdgeApplicationmsedge.exe"):
        if exe and Path(exe).exists():
            subprocess.Popen([exe, f"--app={url}", "--window-size=1040,720", f"--user-data-dir={DATA / 'panel-profile'}"])
            return
    import webbrowser
    webbrowser.open(url)


def run_window(url):
    """Proceso de la ventana: muestra la pagina del panel dentro de una ventana nativa y termina al cerrarla."""
    import webview
    def to_front():
        # Un programa lanzado en segundo plano abre su ventana detras de las demas: se sube un momento por encima de todas
        try:
            import ctypes
            u = ctypes.windll.user32
            for _ in range(30):
                hwnd = u.FindWindowW(None, "PC Remote")
                if hwnd:
                    u.ShowWindow(hwnd, 9)
                    for top in (-1, -2):   # HWND_TOPMOST y luego HWND_NOTOPMOST: queda delante pero normal
                        u.SetWindowPos(hwnd, top, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                    u.SetForegroundWindow(hwnd)
                    return
                time.sleep(0.2)
        except (AttributeError, OSError):
            pass

    webview.create_window("PC Remote", url, width=1040, height=720, min_size=(760, 560), background_color="#0d1117")
    webview.start(to_front, storage_path=str(DATA / "webview"))


# Disenos de botones de cada persona, guardados en el PC: asi se recuperan aunque el movil cambie de via
# (WiFi y Bluetooth son direcciones distintas y el navegador no comparte lo guardado entre ellas)
LAYOUTS = DATA / "layouts.json"


def read_layouts():
    try:
        d = json.loads(LAYOUTS.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def check_layout_request(request):
    if request.query.get("k") != request.app["token"]:
        raise web.HTTPForbidden(text="PIN incorrecto")
    p = request.query.get("p", "").strip().lower()[:40]
    if not p:
        raise web.HTTPBadRequest(text="falta el perfil")
    return p


async def layouts_get(request):
    return web.json_response(read_layouts().get(check_layout_request(request), {}))


async def layouts_post(request):
    p = check_layout_request(request)
    body = await request.read()
    if len(body) > 65536:
        raise web.HTTPBadRequest(text="diseno demasiado grande")
    try:
        data = json.loads(body)
    except ValueError:
        raise web.HTTPBadRequest(text="JSON invalido")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="formato invalido")
    all_ = read_layouts()
    all_[p] = data
    tmp = LAYOUTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(all_, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LAYOUTS)  # escritura atomica: un corte a medias no rompe los disenos de los demas
    return web.json_response({"ok": True})


FULL = (0.0, 0.0, 1.0, 1.0)
# Calidad segun cuantos moviles miran: (ancho maximo, calidad JPEG, fotos por segundo).
# Con 4 mirando por WiFi la imagen baja para que la red no se llene y los botones no vayan con retraso
SCREEN_TIERS = {1: (960, 55, 25), 2: (854, 50, 25), 3: (640, 45, 20)}


def split_region(rank, players, side_by_side=False):
    """Parte de la pantalla dividida que le toca al jugador numero rank (0 = el primero conectado)."""
    if players <= 1:
        return FULL
    if players == 2:  # Mario Kart y casi todos: arriba/abajo; algunos juegos dejan ponerlo lado a lado
        return (0.5 * rank, 0.0, 0.5 * rank + 0.5, 1.0) if side_by_side else (0.0, 0.5 * rank, 1.0, 0.5 * rank + 0.5)
    if players <= 4:
        x, y = rank % 2, rank // 2  # 3 o 4: un cuarto cada uno, en orden de lectura
        return (0.5 * x, 0.5 * y, 0.5 * x + 0.5, 0.5 * y + 0.5)
    x, y = rank % 3, rank // 3   # 5 o 6: rejilla de 3 columnas por 2 filas
    return (x / 3, 0.5 * y, (x + 1) / 3, 0.5 * y + 0.5)


# Parte elegida a mano en el movil (no depende de cuantos jugadores haya ni de su orden)
PARTS = {"top": (0.0, 0.0, 1.0, 0.5), "bottom": (0.0, 0.5, 1.0, 1.0),
         "left": (0.0, 0.0, 0.5, 1.0), "right": (0.5, 0.0, 1.0, 1.0),
         "tl": (0.0, 0.0, 0.5, 0.5), "tr": (0.5, 0.0, 1.0, 0.5),
         "bl": (0.0, 0.5, 0.5, 1.0), "br": (0.5, 0.5, 1.0, 1.0)}


# Video H.264: (ancho maximo, bits por segundo, fotos por segundo) segun cuantos moviles miran.
# Con uno solo van 60 fps: cada foto llega ~8 ms antes de media y se ve mas fluido
H264_TIERS = {1: (1280, 8_000_000, 60), 2: (1152, 4_000_000, 30), 3: (960, 2_500_000, 30)}
H264_FPS = 30
# Fotos enviadas que el movil aun no ha confirmado. Por encima se saltan fotos: si no, se quedan en cola en la
# red (WiFi o el tunel de adb por cable) y la imagen va cada vez mas tarde aunque el movil pueda con ella
MAX_INFLIGHT = {False: 2, True: 4}
# Se prueba en este orden: tarjeta grafica primero (casi no gasta CPU) y x264 como ultimo recurso
H264_CHOICES = (
    ("h264_nvenc", {"preset": "p1", "tune": "ll", "zerolatency": "1", "rc": "cbr", "bf": "0", "delay": "0", "forced-idr": "1"}),
    ("h264_amf", {"usage": "ultralowlatency", "quality": "speed", "forced_idr": "1"}),
    ("h264_qsv", {"preset": "veryfast", "look_ahead": "0", "async_depth": "1", "forced_idr": "1"}),
    ("libx264", {"preset": "ultrafast", "tune": "zerolatency"}),
)
_h264 = False  # False = aun sin probar; None = ningun codificador funciona


def h264_choice():
    """(nombre, opciones) del primer codificador H.264 que funciona en este PC, o None."""
    global _h264
    if _h264 is not False:
        return _h264
    _h264 = None
    if av is None:
        return None
    for name, opts in H264_CHOICES:
        try:
            H264Encoder(320, 180, 1_000_000, (name, opts)).encode(np.zeros((180, 320, 4), np.uint8), True)
            _h264 = (name, opts)
            break
        except Exception as e:
            diag(f"  codificador {name} no disponible: {e}")
    return _h264


class H264Encoder:
    """Un codificador por recorte de pantalla. Salida Annex B con SPS/PPS en cada fotograma clave."""

    def __init__(self, w, h, bitrate, choice=None, fps=H264_FPS):
        name, opts = choice or h264_choice()
        c = av.CodecContext.create(name, "w")
        c.width, c.height, c.pix_fmt = w, h, "yuv420p"
        c.time_base, c.framerate = Fraction(1, fps), Fraction(fps, 1)
        c.bit_rate, c.gop_size, c.max_b_frames = bitrate, fps * 2, 0
        c.options = dict(opts)
        c.open()
        self.c, self.size, self.bitrate, self.fps, self.n = c, (w, h), bitrate, fps, 0

    def encode(self, bgra, key=False):
        """Devuelve el mensaje listo para el websocket: b'H' + (1 si es clave) + datos; None si aun no hay salida.
        La conversion a YUV la hace OpenCV (~1 ms a 720p; la de PyAV tarda ~7 ms)."""
        f = av.VideoFrame.from_ndarray(cv2.cvtColor(bgra, cv2.COLOR_BGRA2YUV_I420), format="yuv420p")
        f.pts, self.n = self.n, self.n + 1
        if key:
            f.pict_type = av.video.frame.PictureType.I
        pk = self.c.encode(f)
        if not pk:
            return None
        return b"H" + (b"\x01" if any(p.is_keyframe for p in pk) else b"\x00") + b"".join(bytes(p) for p in pk)


class Capture:
    """Pantalla principal en BGRA (sin copias: los recortes y el redimensionado trabajan sobre ella).
    DXGI con dxcam (~2 ms, la imagen sale de la tarjeta grafica); si no se puede (escritorio remoto, driver raro),
    GDI con mss (~25 ms). Tiene que vivir y usarse siempre en el mismo hilo."""

    def __init__(self):
        self.cam = self.sct = None
        if dxcam is not None:
            for dev in range(3):  # portatiles con dos graficas: la pantalla puede colgar de cualquiera
                try:
                    self.cam = dxcam.create(device_idx=dev, output_color="BGRA")
                    if self.grab() is not None:
                        break
                except Exception as e:
                    diag(f"  captura DXGI (grafica {dev}) no disponible: {e}")
                self.close()
        if self.cam is None:
            self.sct = mss.mss()

    def grab(self):
        if self.cam is not None:
            for _ in range(20):  # nada cambio desde la ultima: devuelve la anterior; la primera puede tardar
                f = self.cam.grab(copy=False, new_frame_only=False)
                if f is not None:
                    return f
                time.sleep(0.005)
            return None
        s = self.sct.grab(self.sct.monitors[1])
        return np.frombuffer(s.raw, np.uint8).reshape(s.height, s.width, 4)

    def close(self):
        for obj, fn in ((self.cam, "release"), (self.sct, "close")):
            if obj is not None:
                try:
                    getattr(obj, fn)()
                except Exception:
                    pass
        self.cam = self.sct = None


class Acks:
    """Cuantas fotos ha mandado el PC a un movil sin que este diga que le han llegado."""

    def __init__(self):
        self.pending, self.last = 0, time.monotonic()

    def got(self):
        self.pending, self.last = max(0, self.pending - 1), time.monotonic()

    def full(self, h264):
        if self.pending and time.monotonic() - self.last > 1.5:  # una confirmacion perdida no puede parar la imagen
            self.pending = 0
        return self.pending >= MAX_INFLIGHT[h264]


class ScreenFeed:
    """Captura la pantalla del PC y la reparte (H.264 o JPEG) a las paginas que la estan viendo.
    Se captura una sola vez por foto y se recorta para cada parte pedida (pantalla entera o la de un jugador).
    Solo captura mientras haya alguien mirando, y en un hilo aparte para no retrasar los mandos."""

    def __init__(self):
        self.frames, self.seq, self.task = {}, 0, None
        self.wanted = {}  # visor -> (recorte que quiere ahora, si pide H.264)
        self.enc = {}     # recorte -> codificador H.264 (solo existe mientras alguien lo pide)
        self.force = set()  # recortes que deben mandar un fotograma clave ya (entra un visor o se perdio uno)
        self.cond = asyncio.Condition()
        self.wake = asyncio.Event()

    def tier(self):
        n = min(3, max(1, len(self.wanted)))
        return SCREEN_TIERS[n], H264_TIERS[n]

    def _paced(self, due, cap, *args):
        """Espera en el hilo de captura hasta la hora de la foto: el sleep de asyncio en Windows va de 15 en 15 ms
        y no llegaba a 60 fps; time.sleep usa el temporizador de alta resolucion."""
        wait = due - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        return self._grab(cap, *args)

    def _grab(self, cap, wants, width, quality, h264):
        """wants = {(recorte, es_h264)}. Devuelve {(recorte, es_h264): mensaje} (JPEG a pelo; H.264 con prefijo 'H')."""
        img = cap.grab()
        if img is None:
            raise RuntimeError("sin imagen")
        H, W = img.shape[:2]
        out = {}
        for r in {r for r, _ in wants}:
            part = img[round(r[1] * H):round(r[3] * H), round(r[0] * W):round(r[2] * W)]
            h, w = part.shape[:2]
            if (r, False) in wants:
                ow = min(width, w)
                small = cv2.cvtColor(cv2.resize(part, (ow, round(ow * h / w)), interpolation=cv2.INTER_AREA), cv2.COLOR_BGRA2BGR)
                out[(r, False)] = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()
            if (r, True) in wants:
                hw, bitrate, fps = h264
                ow = max(2, min(hw, w) & ~1)  # H.264 pide medidas pares
                oh = max(2, round(ow * h / w) & ~1)
                enc = self.enc.get(r)
                if enc is None or enc.size != (ow, oh) or enc.bitrate != bitrate or enc.fps != fps:
                    enc = self.enc[r] = H264Encoder(ow, oh, bitrate, fps=fps)  # recorte nuevo o calidad distinta: empieza con clave
                    self.force.discard(r)
                    key = True
                else:
                    key = r in self.force
                    self.force.discard(r)
                msg = enc.encode(cv2.resize(part, (ow, oh), interpolation=cv2.INTER_AREA), key)  # sobre BGRA: ~2 ms, no ~25
                if msg is not None:
                    out[(r, True)] = msg
        for r in [r for r in self.enc if (r, True) not in wants]:
            del self.enc[r]  # nadie lo pide ya: se libera, y si vuelve empezara con un fotograma clave
        return out

    async def _run(self):
        """Captura mientras haya alguien mirando. Con nadie mirando 10 s se apaga de verdad (cierra mss y el
        hilo, borra la ultima foto) y se reabre limpia cuando vuelve un visor: no queda nada vivo ni
        con la calidad de antes."""
        loop = asyncio.get_running_loop()
        # la captura guarda estado por hilo: un solo hilo dedicado para todas las capturas
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(1)
        cap, due = None, 0.0
        try:
            while True:
                if not self.wanted:
                    self.wake.clear()
                    try:
                        await asyncio.wait_for(self.wake.wait(), 10)
                    except asyncio.TimeoutError:
                        if not self.wanted:
                            break
                        continue
                (width, quality, fps), h264 = self.tier()
                wants = set(self.wanted.values())
                if any(v for _, v in wants):
                    fps = h264[2]
                try:
                    if cap is None:
                        cap = await loop.run_in_executor(pool, Capture)
                        diag(f"  captura de pantalla: {'DXGI' if cap.cam is not None else 'GDI (mss)'}")
                    frames = await loop.run_in_executor(pool, self._paced, due, cap, wants, width, quality, h264)
                except Exception as e:  # p. ej. pantalla bloqueada o cambio de resolucion: se reabre la captura
                    diag(f"  captura fallida: {e}")
                    if cap is not None:
                        await loop.run_in_executor(pool, cap.close)
                    cap = None
                    await asyncio.sleep(0.5)
                    continue
                async with self.cond:
                    self.frames, self.seq = frames, self.seq + 1
                    self.cond.notify_all()
                due = max(due + 1 / fps, time.perf_counter())  # si una foto se retrasa no se recupera a rafagas
        finally:
            self.frames, self.enc = {}, {}
            self.force.clear()
            if cap is not None:
                try:
                    await loop.run_in_executor(pool, cap.close)
                except Exception:
                    pass
            pool.shutdown(wait=False)
            # alguien pudo entrar mientras se cerraba: se arranca de nuevo en vez de dejarlo sin imagen
            self.task = asyncio.create_task(self._run()) if self.wanted else None
            if self.task:
                self.wake.set()

    async def serve(self, ws, region, h264=False, acks=None):
        """region() dice en cada foto que parte mandar: cambia sola si entra o sale un jugador.
        En H.264 cada foto depende de la anterior: si este movil se salta alguna (red lenta) se descarta todo
        hasta el siguiente fotograma clave, que se pide al momento."""
        me = object()
        self.wanted[me] = (region(), h264)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())
        self.wake.set()
        seen, told, synced = 0, None, not h264
        try:
            while not ws.closed:
                r = region()
                self.wanted[me] = (r, h264)
                if r != told:  # la pagina muestra que parte esta viendo (p. ej. "arriba" o "entera")
                    told = r
                    synced = not h264
                    if h264:
                        self.force.add(r)
                    await asyncio.wait_for(ws.send_str(json.dumps({"r": r})), 5)
                async with self.cond:
                    await self.cond.wait_for(lambda: self.seq != seen)
                    missed = self.seq != seen + 1
                    frame, seen = self.frames.get((r, h264)), self.seq
                if frame is None:  # recorte recien cambiado: llega en la proxima foto
                    continue
                if acks is not None and acks.full(h264):  # el movil va atrasado: esta foto no sale
                    synced = not h264
                    continue
                if h264:
                    if missed:
                        synced = False
                    if not synced:
                        if frame[1] != 1:  # delta suelta: inutil sin su clave
                            self.force.add(r)
                            continue
                        synced = True
                # send_bytes espera a que salga el anterior: un movil lento salta fotogramas en vez de acumularlos.
                # Con la red muerta (movil que se fue sin cerrar) la espera no acabaria nunca: se corta
                await asyncio.wait_for(ws.send_bytes(frame), 5)
                if acks is not None:
                    acks.pending += 1
        except (ConnectionResetError, asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            self.wanted.pop(me, None)


async def screen_ws(request):
    app = request.app
    if request.query.get("k") != app["token"]:
        raise web.HTTPForbidden(text="PIN incorrecto")
    if not app["feed"]:
        raise web.HTTPNotFound(text="pantalla apagada")
    mine = request.query.get("view") == "mine"
    side = request.query.get("s2") == "v"
    cid = request.query.get("c", "")
    part = PARTS.get(request.query.get("part", ""))

    def region():
        if mine and part:
            return part
        # "Solo mi parte" automatico: el orden de los jugadores es el de sus mandos (el hueco mas bajo es el jugador 1)
        if not mine or cid not in app["clients"]:
            return FULL
        slots = sorted(app["slots"])
        slot = app["clients"][cid][1]
        return split_region(slots.index(slot), len(slots), side) if slot in slots else FULL

    ws = web.WebSocketResponse(heartbeat=4, compress=False)
    await ws.prepare(request)
    app["screen_wss"].add(ws)
    # Las apps nuevas confirman cada foto que reciben ("a"); las viejas no, y para ellas se envia como antes
    acks = Acks() if request.query.get("ack") == "1" else None

    async def read():  # tambien detecta el cierre aunque no llegue ningun mensaje
        async for m in ws:
            if m.type == WSMsgType.TEXT and acks is not None:
                acks.got()

    reader = asyncio.create_task(read())
    h264 = request.query.get("v") == "h264" and h264_choice() is not None
    sender = asyncio.create_task(app["feed"].serve(ws, region, h264, acks))
    await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
    sender.cancel()
    reader.cancel()
    app["screen_wss"].discard(ws)
    await ws.close()
    return ws


async def info(request):
    """La pagina pregunta al cargar si hay pantalla y si el PC sabe mandar video H.264."""
    if request.query.get("k") != request.app["token"]:
        raise web.HTTPForbidden(text="PIN incorrecto")
    # ack: este PC entiende las confirmaciones de foto (un PC viejo cortaria la imagen al recibir la primera)
    return web.json_response({"live": bool(request.app["feed"]), "h264": h264_choice() is not None, "ack": True})


async def hello(request):
    """Sin PIN: la app del movil lo usa para comprobar que esta direccion es un PC Remote (y el PIN con /info)."""
    return web.json_response({"app": "pcremote", "v": 1, "name": socket.gethostname()}, headers=NO_CACHE)


async def ws_handler(request):
    app = request.app
    if request.query.get("k") != app["token"]:
        raise web.HTTPForbidden(text="PIN incorrecto")
    pad_cls = PAD_TYPES.get(request.query.get("type", "xbox"), XboxPad)
    cid_q = request.query.get("c", "")
    via = "Bluetooth" if cid_q.startswith("bt") else "Cable USB" if request.remote in ("127.0.0.1", "::1") else "WiFi"

    # Sin compresion por mensaje: anade CPU y latencia a mensajes de pocos bytes.
    # Latido corto: un movil que se cae de la red se detecta en pocos segundos y libera su hueco
    ws = web.WebSocketResponse(heartbeat=4, compress=False)
    await ws.prepare(request)

    slots, clients = app["slots"], app["clients"]
    cid = request.query.get("c", "")
    # Un movil que reconecta aun puede tener su conexion anterior abierta (red caida, sin cerrar):
    # se cierra para recuperar su mismo hueco de jugador en vez de ocupar otro
    prev = clients.get(cid) if cid else None
    if prev is not None:
        prev_ws, prev_slot = prev
        try:
            await asyncio.wait_for(prev_ws.close(), 1.0)
        except (asyncio.TimeoutError, ConnectionResetError):
            pass
        for _ in range(30):
            if prev_slot not in slots:
                break
            await asyncio.sleep(0.05)
    slot = next((i for i in range(MAX_PADS) if i not in slots), None)
    if slot is None:
        await ws.send_json({"t": "full"})
        await ws.close()
        return ws

    if slot >= XINPUT_PADS:   # XInput solo da 4 mandos Xbox: del 5.º en adelante, un DualShock 4 virtual
        pad_cls = Ps4Pad
    pad = pad_cls()
    slots[slot] = pad
    if cid:
        clients[cid] = (ws, slot)
    meta = app["players"][slot] = {"kind": pad.kind, "ip": request.remote, "via": via, "ws": ws,
                                   "t0": time.monotonic(), "last": time.monotonic()}  # lo que ensena el panel
    loop = asyncio.get_running_loop()

    def rumble(value):
        if not ws.closed:
            asyncio.run_coroutine_threadsafe(ws.send_json({"t": "rumble", "v": value}), loop)

    pad.on_rumble(rumble)
    pressed_at, pending = {}, {}  # boton -> momento en que se pulso / soltado diferido

    def release(name):
        pending.pop(name, None)
        pad.button(name, False)
        pad.update()

    def on_button(name, down):
        """Aplica el boton; devuelve False si no existe. Los soltados demasiado rapidos se retrasan."""
        late = pending.pop(name, None)
        if late is not None:  # se vuelve a pulsar antes del soltado diferido: se suelta ya
            late.cancel()
            release(name)
        if down:
            pressed_at[name] = time.monotonic()
            return pad.button(name, True)
        wait = MIN_HOLD - (time.monotonic() - pressed_at.get(name, 0))
        if wait > 0:
            pending[name] = loop.call_later(wait, release, name)
            return False  # aun no hay nada que mandar al mando
        return pad.button(name, False)

    print(f"[+] Jugador {slot + 1} conectado como {pad.kind} ({request.remote})")
    t0 = last = time.monotonic()
    msgs = gaps = 0
    diag(f"+ J{slot + 1} {pad.kind} desde {request.remote}")
    try:
        await asyncio.sleep(pad.ready_delay)
        pad.neutral()  # el primer reposo puede perderse mientras Windows registra el mando
        await ws.send_json({"t": "hello", "player": slot + 1, "type": pad.kind})
    except ConnectionResetError:
        pass  # el movil se fue durante la espera; el bucle de abajo limpia el mando

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            # La pagina manda un ping por segundo como minimo: una pausa mucho mayor es la red atascada
            now = time.monotonic()
            msgs += 1
            if now - last > 1.5:
                gaps += 1
                diag(f"  J{slot + 1} pausa de {int((now - last) * 1000)} ms sin recibir nada")
            last = meta["last"] = now
            try:
                m = json.loads(msg.data)
                t = m["t"]
                if t == "ping":  # la pagina mide aqui su latencia de ida y vuelta
                    meta["rtt"] = m.get("rtt")  # la ultima que midio el movil, para el panel
                    await ws.send_json({"t": "pong", "ts": m["ts"]})
                    continue
                if t == "btn":
                    if not on_button(m["b"], bool(m["d"])):
                        continue
                elif t == "stick":
                    pad.sticks(clamp(float(m["lx"]), -1, 1), clamp(float(m["ly"]), -1, 1),
                               clamp(float(m["rx"]), -1, 1), clamp(float(m["ry"]), -1, 1))
                elif t == "trig":
                    pad.triggers(clamp(float(m["l"]), 0, 1), clamp(float(m["r"]), 0, 1))
                else:
                    continue
                pad.update()
            except (KeyError, ValueError, TypeError):
                continue  # mensaje mal formado: se ignora
    finally:
        for h in pending.values():
            h.cancel()
        pad.close()
        slots.pop(slot, None)
        if app["players"].get(slot) is meta:
            del app["players"][slot]
        if cid and clients.get(cid, (None,))[0] is ws:
            del clients[cid]
        del pad  # al liberar el objeto, ViGEm quita el mando virtual
        print(f"[-] Jugador {slot + 1} desconectado")
        diag(f"- J{slot + 1} tras {int(time.monotonic() - t0)} s, {msgs} mensajes, {gaps} pausas;"
             f" cierre={ws.close_code} {ws.exception() or ''}")
    return ws


def make_app(token, live=False, details=None):
    app = web.Application(middlewares=[cors])
    app["token"] = token
    app["slots"] = {}
    app["clients"] = {}  # id del navegador -> (websocket, hueco)
    app["players"] = {}  # hueco -> datos del movil conectado, para el panel
    app["screen_wss"] = set()
    app["port"] = (details or {}).get("port", 8765)
    app["info"] = details or {}  # direccion, via, https... que ensena el panel
    app["feed"] = ScreenFeed() if live else None  # pantalla del PC dentro de la pagina del mando
    app.router.add_get("/panel", panel_page)
    app.router.add_get("/panel/state", panel_state)
    app.router.add_post("/panel/bluetooth", panel_bluetooth)
    app.router.add_get("/panel/qr.svg", panel_qr)
    app.router.add_post("/panel/relaunch", panel_relaunch)
    app.router.add_post("/panel/update/check", panel_update_check)
    app.router.add_post("/panel/update/install", panel_update_install)
    app.router.add_post("/panel/screen", panel_screen)
    app.router.add_post("/panel/kick", panel_kick)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/info", info)
    app.router.add_get("/hello", hello)
    app.router.add_get("/screen", screen_ws)
    app.router.add_get("/layouts", layouts_get)
    app.router.add_post("/layouts", layouts_post)
    return app


_adb_cache = (0.0, False)


def adb_ready():
    """Hay adb y un movil con depuracion USB autorizado (se cachea 5 s: se pregunta a cada refresco de la pantalla de inicio)."""
    global _adb_cache
    if time.monotonic() - _adb_cache[0] < 5:
        return _adb_cache[1]
    ok = False
    adb = find_adb()
    if adb:
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            ok = any(l.strip().endswith("\tdevice") for l in out.splitlines()[1:])
        except (OSError, subprocess.SubprocessError):
            pass
    _adb_cache = (time.monotonic(), ok)
    return ok


# ---- pantalla de inicio: elegir como conectar, con deteccion en vivo (en lugar de preguntas en la consola) ----
def setup_state(saved):
    """Estado de las tres vias para la pantalla de inicio. El programa las abre todas a la vez."""
    wifi = connections()
    try:
        import btlink
        bt = btlink.available()
    except Exception:
        bt = False
    vias = [
        {"id": "wifi", "name": "WiFi local", "detected": bool(wifi), "ip": wifi[0][1] if wifi else None,
         "help": "El móvil y el PC en la misma red WiFi. La opción más fluida y la única con pantalla del PC."},
        {"id": "bluetooth", "name": "Bluetooth", "detected": bt, "ip": None,
         "help": "El móvil busca el PC y se conecta, sin red. Solo botones, sin pantalla."},
        {"id": "adb", "name": "Cable USB", "detected": adb_ready(), "ip": None,
         "help": "Cable con «Depuración USB» activada en el móvil. Sin red y casi sin retraso."},
    ]
    hc = h264_choice()
    return {"pc": socket.gethostname(), "vias": vias, "saved": saved, "encoder": hc[0] if hc else None,
            "update": update_info()}


def run_setup(saved, port, token):
    """Abre la pantalla de inicio y espera a que se pulse Iniciar. Devuelve {https, pantalla, panel_url},
    o None si se cierra la ventana sin iniciar."""
    key = secrets.token_hex(4)
    result, last_poll = {}, [None]

    def guard(request):
        if request.query.get("k") != key:
            raise web.HTTPForbidden()

    async def page(request):
        guard(request)
        return web.FileResponse(STATIC / "setup.html", headers=NO_CACHE)

    async def state(request):
        guard(request)
        last_poll[0] = time.monotonic()  # la ventana sigue abierta mientras pregunta
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, setup_state, saved),
                                 headers=NO_CACHE)

    async def start(request):
        guard(request)
        b = await request.json()
        vias = [v for v in b.get("vias", []) if v in VIA_IDS]
        if not vias:
            raise web.HTTPBadRequest()
        wifi = "wifi" in vias   # la pantalla del PC y el navegador solo existen por WiFi
        https = wifi and bool(b.get("https"))
        ip = "127.0.0.1"   # el panel siempre se abre desde este mismo PC
        result.update(vias=vias, https=https, pantalla=wifi and bool(b.get("pantalla")),
                      panel_url=f"http://{ip}:{port}/panel?k={token}")
        return web.json_response({"panel_url": result["panel_url"]})

    async def run():
        app = web.Application()
        app.router.add_get("/setup", page)
        app.router.add_get("/setup/state", state)
        app.router.add_post("/setup/start", start)

        async def upd_check(request):
            guard(request)
            return web.json_response(await asyncio.get_running_loop().run_in_executor(None, update_check))

        async def upd_install(request):
            guard(request)
            return web.json_response(update_start())
        app.router.add_post("/setup/update/check", upd_check)
        app.router.add_post("/setup/update/install", upd_install)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sport = site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{sport}/setup?k={key}"
        print(f"  Si no se abre la ventana, entra en: {url}", flush=True)
        open_panel_window(url)
        try:
            while not result:
                await asyncio.sleep(0.3)
                if last_poll[0] and time.monotonic() - last_poll[0] > 15:
                    return None  # cerraron la ventana sin iniciar
            await asyncio.sleep(0.5)  # deja que la ventana reciba la respuesta
            return result
        finally:
            await runner.cleanup()

    return asyncio.run(run())


def message_box(title, text, error=False):
    """Aviso grafico: el .exe no tiene consola, asi que los errores no se verian de otra forma."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except (AttributeError, OSError):
        print(f"{title}: {text}")


def vigem_ok():
    """True si el driver ViGEmBus esta instalado (se prueba creando un mando y quitandolo)."""
    try:
        p = vg.VX360Gamepad()
        del p
        return True
    except Exception as e:
        diag(f"ViGEmBus no disponible: {e}")
        return False


def install_vigem():
    """Instala el driver ViGEmBus que trae el programa (pide permiso de administrador una vez)."""
    msi = Path(vg.__file__).parent / "win" / "vigem" / "install" / "x64" / "ViGEmBusSetup_x64.msi"
    if not msi.exists():
        return False
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process msiexec -Verb RunAs -Wait -ArgumentList '/i','\"{msi}\"','/passive','/norestart'"],
                       timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    time.sleep(2)
    return vigem_ok()


def ensure_vigem():
    if vigem_ok():
        return
    message_box("PC Remote", "Falta el driver ViGEmBus, que crea el mando virtual en Windows.\n\n"
                "Se va a instalar ahora (Windows pedira permiso de administrador).")
    if not install_vigem():
        message_box("PC Remote", "No se pudo instalar el driver ViGEmBus.\n\nInstalalo a mano desde "
                    "github.com/nefarius/ViGEmBus/releases y vuelve a abrir PC Remote.", error=True)
        sys.exit(1)


def other_instance(port, token):
    """Si PC Remote ya esta abierto en este puerto devuelve la direccion de su panel."""
    import urllib.request
    for host in ("127.0.0.1", lan_ip()):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/hello", timeout=1.5) as r:
                if json.loads(r.read()).get("app") == "pcremote":
                    return f"http://{host}:{port}/panel?k={token}"
        except (OSError, ValueError):
            continue
    return None


def relaunch_setup():
    """Cierra el programa y lo abre otra vez con la pantalla de inicio (para cambiar las vias de conexion)."""
    exe = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
    stop_adb()   # el nuevo arranque abre el suyo
    subprocess.Popen(exe + ["--preguntar"], cwd=str(APP_DIR))
    os._exit(0)


def start_tray(panel_url, port, token):
    """Icono junto al reloj: abrir el panel, ajustes de inicio o salir. El programa vive aqui cuando no hay ventana."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return
    icon_file = STATIC / "icon.png"
    try:
        image = Image.open(icon_file)
    except OSError:
        image = Image.new("RGB", (64, 64), "#4f8cff")

    def relaunch(_icon=None, _item=None):
        relaunch_setup()

    def show_url(_icon=None, _item=None):
        open_panel_window(panel_url)

    def quit_(_icon=None, _item=None):
        quit_app(0)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir panel", show_url, default=True),
        pystray.MenuItem("Ajustes de inicio…", relaunch),
        pystray.MenuItem("Salir", quit_))
    pystray.Icon("PCRemote", image, "PC Remote", menu).run_detached()


@web.middleware
async def cors(request, handler):
    """La app del movil abre la pagina desde su propio origen (https://localhost): el servidor tiene que permitirlo.
    El panel no lleva CORS: solo se usa desde este PC. Lo demas pide el PIN."""
    if request.path.startswith(("/panel", "/setup")):
        return await handler(request)
    try:
        resp = web.Response(status=204) if request.method == "OPTIONS" else await handler(request)
    except web.HTTPException as e:
        resp = e
    if not getattr(resp, "prepared", False):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    if isinstance(resp, web.HTTPException) and request.method != "OPTIONS":
        raise resp
    return resp


def adb_watch(port):
    """Mientras haya un movil conectado por cable con depuracion USB, mantiene abierto su acceso a localhost:puerto."""
    while True:
        try:
            if adb_ready():
                adb_reverse(port)
        except Exception as e:
            diag(f"adb: {e}")
        time.sleep(5)


async def serve_forever(app, host, port, ctx):
    """http en `port` (la app del movil) y, si hay certificado, https en `port + 1` (navegador con giroscopio)."""
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    if ctx:
        await web.TCPSite(runner, host, port + 1, ssl_context=ctx).start()
    await asyncio.Event().wait()


def main():
    try:
        _main()
    finally:
        stop_adb()   # cierre normal, Ctrl+C o error: adb no se queda abierto


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--pin", default=None, help="PIN fijo (por defecto, uno que se guarda)")
    ap.add_argument("--https", action=argparse.BooleanOptionalAction, default=None,
                    help="ofrecer tambien https (navegador con giroscopio)")
    ap.add_argument("--pantalla", action=argparse.BooleanOptionalAction, default=None,
                    help="mostrar la pantalla del PC en el movil")
    ap.add_argument("--preguntar", action="store_true", help="ignorar los ajustes guardados y preguntar otra vez")
    ap.add_argument("--sin-panel", action="store_true", help="no abrir la ventana del panel")
    ap.add_argument("--ventana", metavar="URL", help="(interno) abre una ventana con esta direccion")
    args = ap.parse_args()
    if args.ventana:
        run_window(args.ventana)
        return
    # Sin consola (el .exe) no hay stdout/stderr: todo lo que se imprima va a un archivo de registro
    if sys.stdout is None or sys.stderr is None:
        log = open(DATA / "pcremote.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    saved = {} if args.preguntar else load_settings()
    token = args.pin or saved_token()
    again = other_instance(args.port, token)
    if again:   # ya esta abierto: solo se muestra su panel
        open_panel_window(again)
        return
    ensure_vigem()

    # El servidor escucha en todas las vias a la vez; la elegida solo decide cual se enseña primero en el panel.
    # Con una ya elegida otra vez se arranca directo; si no, pantalla de inicio grafica
    # El servidor escucha en las tres vias a la vez (WiFi local, Bluetooth y cable): cada uno de los 6 moviles
    # puede entrar por la que le funcione. Con los ajustes ya guardados se arranca directo; si no, pantalla de inicio
    quick = not args.sin_panel and bool(saved.get("vias"))
    choice = None
    if not args.sin_panel and not quick:
        try:
            choice = run_setup(load_settings(), args.port, token)
        except KeyboardInterrupt:
            return
        if choice is None:
            return
    vias = [v for v in ((choice and choice["vias"]) or saved.get("vias") or VIA_IDS) if v in VIA_IDS] or VIA_IDS
    # Sin WiFi el servidor solo atiende a este mismo PC (el cable USB entra por localhost): nadie de la red puede llegar
    host = "0.0.0.0" if "wifi" in vias else "127.0.0.1"

    use_https = args.https
    if use_https is None:
        use_https = choice["https"] if choice else saved.get("https", True)
    use_https = bool(use_https) and "wifi" in vias
    ctx = None
    if use_https:
        try:
            ctx = make_ssl_context()
        except Exception as e:   # sin https el programa funciona igual (la app del movil no lo usa)
            diag(f"certificado: {e}")
            use_https = False
    screen = args.pantalla
    if screen is None:
        screen = choice["pantalla"] if choice else saved.get("pantalla", True)
    screen = bool(screen) and "wifi" in vias   # la pantalla solo llega por WiFi
    save_settings({"vias": vias, "pantalla": screen, "https": bool(use_https)})

    print(f"== PC Remote {VERSION} · {', '.join(vias)} · puerto {args.port} ==")
    app = make_app(token, live=bool(screen), details={
        "pc": socket.gethostname(), "port": args.port,
        "https": bool(use_https), "pin": token, "vias": vias})
    panel_url = f"http://127.0.0.1:{args.port}/panel?k={token}"
    if "adb" in vias:
        threading.Thread(target=adb_watch, args=(args.port,), daemon=True).start()

    async def on_start(_app):
        try:
            if "bluetooth" not in vias:
                raise RuntimeError("Bluetooth no elegido")
            import btlink
            bt = btlink.BtServer(args.port, token, asyncio.get_running_loop(), diag)
            if bt.start():
                _app["info"]["bt_on"] = True
                _BT_KEEP.append(bt)
        except Exception as e:   # sin Bluetooth en el PC el resto funciona igual
            diag(f"bluetooth: {e}")   # un segundo despues: el servidor aun no escucha mientras corre on_startup
        if not args.sin_panel and not choice:   # con pantalla de inicio, esa misma ventana pasa sola al panel
            asyncio.get_running_loop().call_later(1.0, open_panel_window, panel_url)
    app.on_startup.append(on_start)
    if not args.sin_panel:
        start_tray(panel_url, args.port, token)
    try:
        asyncio.run(serve_forever(app, host, args.port, ctx))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        message_box("PC Remote", f"No se pudo abrir el puerto {args.port}: {e}\n\n¿Ya hay otro PC Remote abierto?", error=True)


if __name__ == "__main__":
    main()
