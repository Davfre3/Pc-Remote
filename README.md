# PC Remote

Convierte tu móvil en un mando de Xbox / PlayStation / Switch para tu PC con Windows, por la **red local**
(WiFi), **cable USB**, **Bluetooth** (anclaje) o **cable con depuración USB**. Sin cuentas ni internet.
Opcionalmente ves la pantalla del PC detrás de los botones del mando.

## Instalar

| Dónde | Qué | Descarga |
|---|---|---|
| Móvil Android | App `PC-Remote.apk` | [Descargar APK](https://github.com/Davfre3/Pc-Remote/raw/main/releases/PC-Remote.apk) |
| PC con Windows | Instalador `PC-Remote-Instalador.exe` | [Releases](https://github.com/Davfre3/Pc-Remote/releases) |

1. Instala el programa en el PC (instala también el driver del mando virtual, ViGEmBus) y ábrelo.
2. Instala la app en el móvil (Android pedirá permitir «instalar apps desconocidas»).
3. En la app: **Escanear código QR** (el del panel del PC) o **Buscar PC**. Elige tu mando y juega.

El móvil y el PC deben estar en la misma red, o conectados por cable / Bluetooth con el anclaje activado.

## Qué trae
- Mandos Xbox, PlayStation (iconos), DualShock 4 y Switch, hasta 4 jugadores.
- Modos: General, Carreras (giroscopio como volante), Peleas, Disparos (giroscopio para apuntar) y Deportes.
- Editor de botones: mover, cambiar tamaño, colores y transparencia; exportar e importar diseños.
- Pantalla del PC en el móvil (H.264 con NVENC/AMF/QSV o x264, con JPEG de respaldo).
- Panel en el PC con QR por cada conexión, jugadores conectados y expulsar.

## Carpetas
- `PCRemote-EXE/` programa del PC (Python: aiohttp, vgamepad, PyAV, PyInstaller + Inno Setup). Ver su `LEEME.txt`.
- `PCRemote-APK/` app de Android (Capacitor). Ver su `LEEME.txt`.
- `releases/` APK listo para instalar.

La página del mando es una sola: `PCRemote-EXE/app/static/index.html` (el APK la copia con `npm run sync`).

## Compilar
- PC: `PCRemote-EXE/crear_exe.bat` (Python 3.12+ con las dependencias del proyecto; Inno Setup para el instalador).
- Android: `PCRemote-APK/crear_apk.bat` (Node, JDK 21 y Android SDK).

## Seguridad
Cada conexión pide un PIN que muestra el panel. No abras los puertos 8765 y 8766 a internet.
El APK está firmado con la clave de depuración: sirve para instalarlo, no para Google Play.
