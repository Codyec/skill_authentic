# camera-yolo

App: captura de la cámara MindVision GigE + detección **YOLO26** + web (`:8090`) con
vídeo en vivo, métricas y **panel de configuración de la cámara**.

Es una **aplicación consumidora**, no parte de la base reutilizable. En la arquitectura
objetivo la inferencia sería un consumidor del bus del `camera-service`; hoy abre la cámara
directamente.

## Panel de configuración · `camera_config.json` como fuente de verdad

`config/camera_config.json` guarda todas las propiedades de la cámara (exposición, ganancia,
gamma, trigger, strobe, red GigE, …). El ciclo es:

```
arranque  → se carga config/camera_config.json y se aplica a la cámara
panel web → "Configuración de cámara" → editar propiedad → Aplicar
            → se escribe la cámara Y se re-guarda el archivo
```

Todas las escrituras al SDK ocurren en el hilo de cámara (`camera_loop`), nunca desde el
hilo HTTP. Rutas:

| Ruta | Qué hace |
|---|---|
| `GET /api/props` | definiciones + valores + estado por propiedad |
| `POST /control/apply` | `{"properties": {"gamma": 120, ...}}` → encola cambios |
| `GET /control/save_config` | relee la cámara y reescribe el archivo |
| `GET /control/reload_config` | recarga el archivo y lo re-aplica |

El módulo `camera_props.py` (definiciones + lectura/escritura/persistencia) cubre **todas
las propiedades escalares R/W** que expone el SDK para esta cámara (~48: exposición, ganancias
RGB, gamma/contraste/nitidez, LUT, balance de blancos, disparo interno y externo, strobe,
red GigE…), según el escaneo `services/camera-service/legacy/properties_report.txt`.

Además hay **acciones** (`POST /control/action` con `{"name": ...}`): `once_wb` (balance de
blancos una vez), `once_bb` (nivel de negro una vez), `save_to_camera` (escribir en el flash
de la cámara).

Notas de comportamiento de esta cámara (HT-GE134GM):
- Varias propiedades solo se escriben **en vivo**: si se hace `CameraStop` + `CameraPlay`
  la cámara recarga su grupo de parámetros y revierte el cambio. `apply_batch` solo detiene
  la adquisición para `_NEEDS_STOP` (`isp_processor`, `trans_pack_len`, `parameter_mode`).
- Algunos `CameraGetXxx` no reflejan de inmediato el `CameraSetXxx`. Por eso el archivo se
  guarda desde la **intención** (lo que se aplicó / lo que se cargó), no releyendo la cámara.

## Configuración por entorno

| Variable | Defecto | Uso |
|---|---|---|
| `MODEL_PATH` | `C:\Python\YOLO\yolo26x.pt` | pesos YOLO |
| `YOLO_DEVICE` | `cuda:0` | `cpu`, `cuda:0`, … |
| `CAMERA_IP` | `192.168.0.216` | informativo (se guarda en el JSON) |
| `CAMERA_CONFIG_PATH` | `config/camera_config.json` | fuente de verdad |
| `MVSDK_PATH` | ruta local Windows | dónde buscar `mvsdk.py` |
| `WEB_SHELL_URL` | `http://localhost:8000/dashboard` | destino de "Volver al menú" |
| `PORT` | `8090` | puerto web |

La UI usa el mismo sistema de diseño que el web-shell (tema oscuro, acento
`#1f83d6`, Inter + JetBrains Mono). Barra superior con estado en vivo y botón
"Volver al menú"; el panel de configuración tiene "Guardar configuración",
"Recargar desde archivo" y "Volver al menú".

## Ejecutar — nativo (Windows, cámara real)

```bash
run.bat
#   o:  C:/Users/dmore/anaconda3/envs/vision_leche/python.exe main.py
```

Requiere cámara MindVision + GPU NVIDIA + `yolo26x.pt`. El arranque tarda **~25 s**
(init CUDA + carga YOLO) — hasta entonces `http://localhost:8090` da "conexión rechazada".
Sin cámara, aborta en `CameraEnumerateDevice()` (esperado); la web sigue respondiendo.

**Debe estar corriendo** para que el enlace "Visión" del web-shell (`:8000`) funcione:
esa tarjeta solo abre `http://localhost:8090`, no arranca nada.

## Ejecutar — Docker (multi-arquitectura)

Base: familia oficial de **Ultralytics** (trae torch/opencv/numpy/ultralytics).

| Destino | `BASE_IMAGE` |
|---|---|
| GPU NVIDIA (x86) | `ultralytics/ultralytics:latest` |
| Solo CPU (x86) | `ultralytics/ultralytics:latest-cpu` |
| Raspberry Pi 5 (arm64) | `ultralytics/ultralytics:latest-arm64` |
| Jetson | `ultralytics/ultralytics:latest-jetson-jetpack6` |

```bash
# CPU / Raspberry Pi 5 — desde apps/camera-yolo/
docker compose -p camera-yolo up -d --build

# GPU NVIDIA
docker compose -p camera-yolo -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

- Contexto de build = **raíz del repo** (necesita `vendor/mindvision-sdk/`).
- `network_mode: host` — GigE Vision necesita la red del host (en Docker Desktop para
  Windows no alcanza una NIC física; probar el contenedor en un host Linux).
- Pesos en `./models/yolo26x.pt`; config en `./config/camera_config.json`.

> **Licencia:** la imagen incluye `libMVSDK.so` (SDK propietario MindVision).
> **No publicar la imagen en un registro público.** Build local únicamente.

## Pendientes / limitaciones conocidas

**Propiedades y métodos** (detalle y tabla completa en
[`../../docs/camera-hardware.md`](../../docs/camera-hardware.md#pendientes-de-resolver--propiedades-y-métodos)):

1. **Resolución / ROI / binning fijos** — `ImageResolution` es un struct, no está expuesto;
   la resolución está clavada en 1280×1024 en `camera_loop()`.
2. **`_NEEDS_STOP` sin verificar** — qué propiedades exigen parar la adquisición es conjetura.
3. **Rangos no aplicados en la UI** — el panel no usa el `range` de cada propiedad (sin min/max).
4. **Enums como enteros** — `media_type`, `clr_temp_mode`, `hdr_gain_mode`, `preset_clr_temp`
   (falta mapear los enum de `CameraDefine.h`).
5. **`ae_algorithm` / `bayer_dec_algorithm` fuera** — llevan un arg extra `iIspProcessor`.
6. **`gain_r/g/b`** expuestas pero sin efecto en sensor monocromo.
7. **Structs sin exponer** — `AeWindow`, `WbWindow`, `UserClrTempMatrix`, `Denoise3DParams`, LUT…
8. **Métodos sin exponer** — perfiles a archivo, cargar de grupo del flash, reset de fábrica,
   LED/anillo de luz, calibración de píxeles muertos.
9. **`acquisition_frame_rate`** depende del parche ctypes `camera_get/set_frame_rate`.
10. **Ciclo de vida** — falta `CameraStop` antes de `CameraUnInit` + manejador de `SIGTERM` +
    `atexit`; un kill forzado deja la cámara tomada ~1 min.
11. **Contenedor sin probar con cámara** — host Linux con NIC GigE.
12. **`camera_config.json`** genera ruido en git (cambia `saved_at` en cada guardado).

**Otros:**

- `WIDTH/HEIGHT`, `FRAME_SPEED` y otros ajustes siguen fijos en `main.py`.
