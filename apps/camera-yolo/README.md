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

El módulo `camera_props.py` (definiciones + lectura/escritura/persistencia) está portado de
`services/camera-service/legacy/camera_service_FUNCIONAL_FINAL.py`.

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
| `PORT` | `8090` | puerto web |

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

## Deuda técnica restante

- `WIDTH/HEIGHT` (1280×1024), `FRAME_SPEED` y varios ajustes siguen fijos en `main.py`.
- La prueba del contenedor contra la cámara real está pendiente (host Linux con NIC GigE).
