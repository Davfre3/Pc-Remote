# PC Remote

Convierte tu móvil en un mando de Xbox / PlayStation / Switch para tu PC con Windows, por **WiFi local**,
**Bluetooth** (el móvil busca el PC y se conecta) o **cable USB** (con depuración USB). Hasta **6 móviles a la vez**,
cada uno por la vía que le funcione. Sin cuentas ni internet.
Opcionalmente ves la pantalla del PC detrás de los botones del mando.

## Instalar

| Dónde | Qué | Descarga |
|---|---|---|
| Móvil Android | App `PC-Remote.apk` | [Descargar APK](https://github.com/Davfre3/Pc-Remote/raw/main/releases/PC-Remote.apk) |
| PC con Windows | Instalador `PC-Remote-Instalador.exe` | [Releases](https://github.com/Davfre3/Pc-Remote/releases) |

1. Instala el programa en el PC (instala también el driver del mando virtual, ViGEmBus) y ábrelo. Al iniciar eliges las conexiones que usarás (WiFi, Bluetooth, cable); el panel se adapta y solo en WiFi muestra un **QR**.
2. Instala la app en el móvil (Android pedirá permitir «instalar apps desconocidas»).
3. En la app elige la misma vía: **WiFi** (escanea el QR o «Buscar PC»), **Bluetooth** o **Cable USB**, con el **PIN** del panel. Elige tu mando y juega.
4. **Actualizar**: el programa del PC y la app avisan solos cuando hay versión nueva (botón «Actualizar»); se instala encima sin perder ajustes.

Para WiFi, móvil y PC en la misma red. Para Bluetooth, el PC tiene que ser visible (el panel tiene un botón que abre la configuración de Bluetooth de Windows).

## Qué trae
- Mandos Xbox, PlayStation (iconos), DualShock 4 y Switch, hasta 6 jugadores (Windows solo da 4 mandos Xbox: los puestos 5 y 6 son DualShock 4).
- Modos: General, Carreras (giroscopio como volante), Peleas, Disparos (giroscopio para apuntar) y Deportes.
- Editor de botones: mover, cambiar tamaño, colores y transparencia; exportar e importar diseños.
- Pantalla del PC en el móvil (H.264 con NVENC/AMF/QSV o x264, con JPEG de respaldo).
- Panel en el PC con la dirección y el PIN de cada vía, jugadores conectados (y por dónde entran) y expulsar.

## Carpetas
- `PCRemote-EXE/` programa del PC (Python: aiohttp, vgamepad, PyAV, PyInstaller + Inno Setup). Ver su `LEEME.txt`.
- `PCRemote-APK/` app de Android (Capacitor). Ver su `LEEME.txt`.
- `releases/` APK listo para instalar.

La página del mando es una sola: `PCRemote-EXE/app/static/index.html` (el APK la copia con `npm run sync`).

## Compilar
- PC: `PCRemote-EXE/crear_exe.bat` (Python 3.12+ con las dependencias del proyecto; Inno Setup para el instalador).
- Android: `PCRemote-APK/crear_apk.bat` (Node, JDK 21 y Android SDK).

## Seguridad
Todas las vías piden el PIN que muestra el panel. No abras los puertos 8765 y 8766 a internet.
El APK está firmado con la clave de depuración: sirve para instalarlo, no para Google Play.
