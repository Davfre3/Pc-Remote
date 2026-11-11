"""Bluetooth directo (RFCOMM) para PC Remote: solo botones, nunca pantalla.

El movil, ya emparejado con el PC, abre un canal Bluetooth serie. Cada linea es un mensaje JSON igual
que los del WebSocket; el puente los reenvia al servidor local (/ws), asi el mando funciona igual
que por red sin tocar el resto del programa. Primera linea del movil: {"k": PIN, "type": "xbox", "c": id}.
El PC responde al momento con {"t": "btok"} (PIN valido) o {"t": "auth"} (PIN incorrecto): asi el movil sabe que
ha llegado a PC Remote y no a otro servicio Bluetooth del PC. Pueden conectarse varios moviles a la vez (un hilo cada uno).
"""
import asyncio
import ctypes
import json
import threading
import time
import uuid
from ctypes import wintypes

import aiohttp

SERVICE_UUID = uuid.UUID("7a1f2b64-3c5d-4e8a-9b10-5043524d4f54")   # el mismo en la app del movil
AF_BTH, BTHPROTO_RFCOMM, SOCK_STREAM = 32, 3, 1
NS_BTH, RNRSERVICE_REGISTER, RNRSERVICE_DELETE = 16, 0, 2
INVALID = ctypes.c_size_t(-1).value

ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("D1", ctypes.c_ulong), ("D2", ctypes.c_ushort), ("D3", ctypes.c_ushort), ("D4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, u):
        b = u.bytes_le
        g = cls()
        g.D1, g.D2, g.D3 = int.from_bytes(b[0:4], "little"), int.from_bytes(b[4:6], "little"), int.from_bytes(b[6:8], "little")
        for i in range(8):
            g.D4[i] = b[8 + i]
        return g


class SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("family", ctypes.c_ushort), ("addr", ctypes.c_ulonglong), ("svc", GUID), ("port", ctypes.c_ulong)]


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_void_p), ("len", ctypes.c_int)]


class CSADDR_INFO(ctypes.Structure):
    _fields_ = [("local", SOCKET_ADDRESS), ("remote", SOCKET_ADDRESS), ("socktype", ctypes.c_int), ("proto", ctypes.c_int)]


class WSAQUERYSET(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("name", wintypes.LPWSTR), ("class_id", ctypes.POINTER(GUID)),
                ("version", ctypes.c_void_p), ("comment", wintypes.LPWSTR), ("ns", wintypes.DWORD),
                ("provider", ctypes.c_void_p), ("context", wintypes.LPWSTR), ("n_protocols", wintypes.DWORD),
                ("protocols", ctypes.c_void_p), ("query", wintypes.LPWSTR), ("n_cs", wintypes.DWORD),
                ("cs", ctypes.POINTER(CSADDR_INFO)), ("flags", wintypes.DWORD), ("blob", ctypes.c_void_p)]


ws2.socket.restype = ctypes.c_size_t
ws2.accept.restype = ctypes.c_size_t
SOL_RFCOMM, SO_BTH_AUTHENTICATE = 3, 0x80000001
for fn in (ws2.bind, ws2.getsockname, ws2.listen, ws2.accept, ws2.recv, ws2.send, ws2.closesocket, ws2.setsockopt):
    fn.argtypes = None


def _sock_arg(s):
    return ctypes.c_size_t(s)


def available():
    """True si este PC tiene un adaptador Bluetooth utilizable (se prueba abriendo y cerrando un socket RFCOMM)."""
    ws2.WSAStartup(0x0202, ctypes.create_string_buffer(512))
    s = ws2.socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM)
    if s == INVALID:
        return False
    ws2.closesocket(_sock_arg(s))
    return True


class BtServer:
    """Escucha en un canal RFCOMM y se anuncia (SDP) con SERVICE_UUID. start() devuelve False si el PC no tiene Bluetooth."""

    def __init__(self, port, token, loop, log=print):
        self.port, self.token, self.loop, self.log = port, token, loop, log
        self.sock = INVALID
        self.channel = 0
        self._reg = None
        self.peers = 0
        self.authed = False   # True: Windows exige que el movil este emparejado, y entonces no hace falta PIN

    def start(self):
        data = ctypes.create_string_buffer(512)
        ws2.WSAStartup(0x0202, data)
        s = ws2.socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM)
        if s == INVALID:
            return False
        addr = SOCKADDR_BTH(family=AF_BTH, addr=0, port=0xFFFFFFFF)   # BT_PORT_ANY
        if ws2.bind(_sock_arg(s), ctypes.byref(addr), ctypes.sizeof(addr)) != 0:
            ws2.closesocket(_sock_arg(s))
            return False
        n = ctypes.c_int(ctypes.sizeof(addr))
        ws2.getsockname(_sock_arg(s), ctypes.byref(addr), ctypes.byref(n))
        self.channel = int(addr.port)
        # Solo moviles emparejados con este PC (el emparejamiento ya es la confirmacion): asi se conecta sin escribir PIN
        one = ctypes.c_ulong(1)
        self.authed = ws2.setsockopt(_sock_arg(s), SOL_RFCOMM, SO_BTH_AUTHENTICATE, ctypes.byref(one), ctypes.sizeof(one)) == 0
        if ws2.listen(_sock_arg(s), 8) != 0:
            ws2.closesocket(_sock_arg(s))
            return False
        self.sock = s
        self._advertise(addr)
        self.log(f"bluetooth: escuchando en el canal {self.channel}; solo emparejados: {'si' if self.authed else 'no (pide PIN)'}")
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return True

    def _advertise(self, addr):
        self._cls = GUID.of(SERVICE_UUID)
        local = SOCKADDR_BTH(family=AF_BTH, addr=0, port=self.channel)
        self._local = local
        self._cs = CSADDR_INFO(SOCKET_ADDRESS(ctypes.addressof(local), ctypes.sizeof(local)), SOCKET_ADDRESS(ctypes.addressof(local), ctypes.sizeof(local)),
                               SOCK_STREAM, BTHPROTO_RFCOMM)
        q = WSAQUERYSET()
        q.size = ctypes.sizeof(q)
        q.name = "PC Remote"
        q.comment = "Mando virtual de PC Remote"
        q.ns = NS_BTH
        q.class_id = ctypes.pointer(self._cls)
        q.n_cs = 1
        q.cs = ctypes.pointer(self._cs)
        self._reg = q
        if ws2.WSASetServiceW(ctypes.byref(q), RNRSERVICE_REGISTER, 0) != 0:
            self.log(f"bluetooth: no se pudo anunciar el servicio (error {ctypes.get_last_error()})")

    def stop(self):
        if self._reg is not None:
            ws2.WSASetServiceW(ctypes.byref(self._reg), RNRSERVICE_DELETE, 0)
        if self.sock != INVALID:
            ws2.closesocket(_sock_arg(self.sock))
            self.sock = INVALID

    # ---- conexiones entrantes ----
    def _accept_loop(self):
        while self.sock != INVALID:
            c = ws2.accept(_sock_arg(self.sock), None, None)
            if c == INVALID:
                if self.sock == INVALID:
                    return
                time.sleep(0.2)   # un error repetido de accept() no debe ocupar toda la CPU
                continue
            self.log("bluetooth: conexion entrante")
            threading.Thread(target=self._peer, args=(c,), daemon=True).start()

    def _recv_line(self, c, buf):
        """Lee hasta un salto de linea; devuelve None si se cerro."""
        while b"\n" not in buf:
            chunk = ctypes.create_string_buffer(1024)
            n = ws2.recv(_sock_arg(c), chunk, 1024, 0)
            if n <= 0:
                return None
            buf += chunk.raw[:n]
        line, _, rest = buf.partition(b"\n")
        buf.clear()
        buf.extend(rest)
        return line.decode("utf-8", "replace")

    def _send_line(self, c, text):
        data = (text + "\n").encode("utf-8")
        ws2.send(_sock_arg(c), data, len(data), 0)

    def _linger(self, c):
        """Tras una respuesta final (PIN incorrecto, comprobacion) no se cierra al instante: si el PC corta nada mas
        escribir, Android puede perder la respuesta sin leer y el movil cree que el PC no contesto. Se espera a que
        cierre el movil (lo hace en cuanto lee la respuesta), como mucho 2 s."""
        ms = ctypes.c_ulong(2000)
        ws2.setsockopt(_sock_arg(c), 0xFFFF, 0x1006, ctypes.byref(ms), ctypes.sizeof(ms))   # SOL_SOCKET, SO_RCVTIMEO
        t0 = time.monotonic()
        chunk = ctypes.create_string_buffer(256)
        while time.monotonic() - t0 < 2 and ws2.recv(_sock_arg(c), chunk, 256, 0) > 0:
            pass

    def _peer(self, c):
        buf = bytearray()
        try:
            first = self._recv_line(c, buf)
            try:
                hello = json.loads(first) if first else {}
            except ValueError:
                hello = {}
            if not isinstance(hello, dict):
                hello = {}
            # Con el PIN correcto se entra siempre; sin el, solo si Windows ya exige que el movil este emparejado
            if hello.get("k") != self.token and not (self.authed and hello):
                self.log("bluetooth: PIN incorrecto" if hello else "bluetooth: el otro lado no habla PC Remote")
                self._send_line(c, json.dumps({"t": "auth"}))
                self._linger(c)
                return
            self._send_line(c, json.dumps({"t": "btok"}))
            if hello.get("probe"):   # solo comprobar canal y PIN desde la pantalla de inicio de la app: no crea jugador
                self.log("bluetooth: comprobacion correcta")
                self._linger(c)
                return
            self.log(f"bluetooth: movil aceptado ({hello.get('c', '')})")
            fut = asyncio.run_coroutine_threadsafe(self._bridge(c, buf, hello), self.loop)
            fut.result()
        except Exception as e:
            self.log(f"bluetooth: {e}")
        finally:
            ws2.closesocket(_sock_arg(c))

    async def _bridge(self, c, buf, hello):
        url = f"http://127.0.0.1:{self.port}/ws?k={self.token}&type={hello.get('type', 'xbox')}&c=bt{hello.get('c', '')}"
        loop = asyncio.get_running_loop()
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(url, heartbeat=4) as ws:
                async def down():   # PC -> movil
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            await loop.run_in_executor(None, self._send_line, c, m.data)
                    # al terminar (expulsado desde el panel, servidor cerrado) el canal lo cierra _peer, una sola vez

                async def up():   # movil -> PC
                    while True:
                        line = await loop.run_in_executor(None, self._recv_line, c, buf)
                        if line is None:
                            return
                        if line:
                            await ws.send_str(line)

                tasks = [asyncio.ensure_future(down()), asyncio.ensure_future(up())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in tasks:
                    t.cancel()
