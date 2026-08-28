# Arquitectura actual y objetivo

## A) Mapa de la arquitectura actual

Tres bloques que hoy **no** están integrados:

1. **Servicio/UI de cámara** — `services/camera-service/legacy/` (antes en la raíz):
   `main.py` (cámara + YOLO26/CUDA, `:8090`), `camera_service_FUNCIONAL_FINAL.py`
   (configuración de propiedades + video MJPEG, `:8010`), `test_properties.py` (diagnóstico),
   `mvsdk.py` (wrapper ctypes del SDK nativo).
2. **App de autenticación** — `platform/web-shell/` (antes `demacya-app/`): FastAPI + OIDC contra Keycloak.
   Sin código de cámara.
3. **Infra de identidad** — `infra/keycloak/` (antes `keycloak-lab/`): Keycloak 26.7.1 + Postgres 16.
   Realm `DEMACYA`, roles `admin` / `supervisor` / `operador`, cliente `demacya-app`.

### Cadena de la cámara

```
Cámara GigE Vision HT-GE134GM (1280x1024 mono, IP 192.168.0.216/24)
      │  Ethernet (GVCP/GVSP UDP) + driver de filtro kernel (Windows)
MVCAMSDK_X64.dll  /  libMVSDK.so     ← nativo del fabricante
      │  ctypes
mvsdk.py  (wrapper oficial, NO escrito por DEMACYA)
      │
      ├── camera_service_FUNCIONAL_FINAL.py : lee propiedades 1 vez, video MJPEG, apply transaccional, save_config
      └── main.py : cámara + YOLO26 (ultralytics, CUDA) + web con métricas + extrapolación de cajas
```

## B) Flujo de ejecución

Dos programas independientes, cada uno con su `main()`.

**`camera_service_FUNCIONAL_FINAL.py`** (el más cercano a "servicio"):
`CameraEnumerateDevice` → `devs[0]` → `CameraInit(-1,-1)` → `CameraGetCapability` →
parche ctypes de `CameraGetFrameRate` / `CameraGetStatisticResend` (no están en `mvsdk.py`) →
`powershell Get-NetAdapter` para link speed → **lectura única** de ~27 propiedades a `INITIAL_PROPERTIES`
(congelado) → `CameraSetIspOutFormat` + `CameraSetTriggerMode(0)` + `CameraPlay` (NO toca exposición /
gain / resolución / fps: los preserva) → hilo `camera_capture_loop` (`GetImageBuffer` → `ImageProcess`
→ numpy → JPEG, todo bajo `camera_lock`) → `ThreadingHTTPServer :8010` con `/`, `/stream.mjpg`,
`/status`, `POST /control/apply` (transacción `Stop → Set → read-back → Play`), `POST /control/save_config`.

**`main.py`**: igual arranque pero además fija resolución 1280x1024, `FrameSpeed(2)`, `CameraSetFrameRate(0)`
(ctypes), AE manual + exposición; 3 hilos (`camera_loop` con drenado de backlog, `yolo_loop` con
`YOLO(r"C:\Python\YOLO\yolo26x.pt").to("cuda:0")`, `web_loop`); web `:8090`.

## C) Dependencias

- **Nativas (críticas, no en el repo salvo la variante Linux):** `MVCAMSDK_X64.dll` (Windows) o
  `libMVSDK.so` (Linux, ahora en `vendor/mindvision-sdk/linux/lib/`). `mvsdk.py` la carga en el `import`.
- Instalador del fabricante: entradas de **registro de Windows**, archivos auxiliares.
- **Driver de filtro kernel GigE** (Windows) o equivalente + socket raw (Linux).
- Python cámara: `ctypes`, `numpy`, `opencv-python`. **No había `requirements.txt`** para estos scripts.
- Python `main.py` (extra): `torch` + CUDA, `ultralytics`, `psutil`, pesos `yolo26x.pt`, `nvidia-smi`.
- `platform/web-shell`: `fastapi`, `uvicorn[standard]`, `jinja2`, `authlib`, `itsdangerous`, `python-dotenv`, `httpx`.
- Infra: Docker + Compose; imágenes `quay.io/keycloak/keycloak:26.7.1`, `postgres:16`.
- **Rutas hardcodeadas:** `C:\Python\YOLO\yolo26x.pt`, `C:\Users\dmore\OneDrive\...\python_demo`,
  IP `192.168.0.216`, puertos fijos.

## D) Elementos de hardware / Windows

| Elemento | Dónde | Portable a Linux |
|---|---|---|
| `windll.MVCAMSDK_X64` | `mvsdk.py` | Sí — el wrapper ya tiene la rama `libMVSDK.so`, y el SDK Linux está vendorizado |
| Driver de filtro kernel GigE | Instalador Windows | Necesita driver/módulo Linux del fabricante en el **host** |
| `powershell Get-NetAdapter` (link speed) | `camera_service` | No; degrada a `N/D` sin romper |
| Persistencia de parámetros vía **registro** (`bParamInDevice=0`) | SDK | En Linux usa archivos; montar ese dir en volumen |
| `nvidia-smi`, `torch.cuda`, `cuda:0` | `main.py` | Solo con host Linux + NVIDIA Container Toolkit |

Sin cámara en la subred, `CameraEnumerateDevice()` devuelve vacío y el proceso aborta.
**No hay modo simulación.** Acceso **exclusivo**: una app por cámara (`CAMERA_STATUS_ACCESS_DENY`).

## E) Docker: posibilidades y límites

**Puede ir en Docker:** `platform/web-shell`, `infra/keycloak`, la lógica HTTP/servicio, la web UI,
YOLO (contenedor aparte con GPU) y — con **host Linux** + `libMVSDK.so` + driver del fabricante en el
host + `network_mode: host` + `cap_add: [NET_ADMIN, NET_RAW]` + volumen de persistencia — el acceso a
la cámara GigE.

**No puede, tal como está:** `import mvsdk` en Linux (hoy el código de arranque asume Windows en varios
puntos); host Windows (Docker Desktop no pasa NIC/USB/GPU a contenedores Linux → hace falta host Linux
bare-metal); driver kernel de Windows; discovery GigE con red bridge/NAT; rutas absolutas `C:\`.

## F) Arquitectura objetivo (resumen)

Aislar el SDK nativo en **un** servicio dueño exclusivo de la cámara. Capas:
**HAL** (`CameraBackend` con impl. real `mvsdk` + `Fake` sintética) → **core** sin framework
(loop de adquisición, gestor de propiedades, gestor de config, stats, supervisor de reconexión) →
**API** (FastAPI/gRPC, `/api/v1`, OpenAPI, `/capabilities`, `/health`) → **adapters de entrega**
(MJPEG, RTSP/WebRTC, bus ZeroMQ/NATS). Un contenedor por cámara física. La inferencia (YOLO) es un
**consumidor** del bus, nunca en la misma imagen que la cámara. Auth vía el Keycloak existente.
Config declarativa (`camera.yaml`) aplicada de forma idempotente + overrides por API + estado
persistido en volumen. Observabilidad Prometheus.

Detalle completo y los 18 puntos de análisis: ver el historial de la conversación de diagnóstico.
