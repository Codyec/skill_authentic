# Hardware de cámara — notas de despliegue

## Cámara

- Modelo observado en el código: **HT-GE134GM** (GigE, 1.3 MP, mono).
- Sensor mono 1280x1024, salida `MONO8`.
- IP estática `192.168.0.216`, máscara `/24`. Un proceso a la vez por cámara.

## SDK del fabricante (MindVision / MVCAMSDK)

Vendorizado en [`../vendor/mindvision-sdk/`](../vendor/mindvision-sdk/):

- `linux/lib/{x64,arm64,arm,arm_softfp,x86}/libMVSDK.so` — biblioteca nativa (x86/arm/arm_softfp
  quedan en disco pero fuera de git; ver README de vendor).
- `linux/include/CameraApi.h | CameraDefine.h | CameraStatus.h`.
- `linux/install.sh` — copia `libMVSDK.so` a `/lib`, headers a `/usr/include`, reglas udev a
  `/etc/udev/rules.d/`. Requiere root y reinicio.
- `linux/88-mvusb.rules`, `linux/99-mvusb.rules` — udev para cámaras **USB** (`idVendor==f622`,
  grupo `mvusb_dev`). No aplican a GigE.
- `linux/demo/python_demo/mvsdk.py` — wrapper Linux de referencia (el de
  `services/camera-service/legacy/mvsdk.py` es más completo).
- `gentl/` — GenTL Producer para Windows (`WinMVGenTL_*.cti`).
- `v4l2/` — `mv_v4l2` + `v4l2loopback`: exponer la cámara como `/dev/videoN` (ruta alternativa,
  opcional).

## GigE Vision en Linux / Docker

- El SDK habla GigE por **socket raw**: el contenedor necesita `network_mode: host` y
  `cap_add: [NET_ADMIN, NET_RAW]` (o `privileged`). Con red bridge/NAT el **descubrimiento falla**.
- Configurar en la NIC del host: IP en la subred de la cámara, **jumbo frames** (MTU 9000) si el
  `TransPackLen` de la cámara lo requiere.
- Puertos: GVCP UDP 3956 + rango GVSP.
- El driver/módulo del fabricante para GigE va en el **host**, no en el contenedor.

## Persistencia de parámetros

`camera_properties.json` reporta `bParamInDevice=0`: los parámetros **no** se guardan en la cámara,
sino en el PC (por modelo / nombre / número de serie). En Windows es el registro; en Linux es un
directorio del SDK que hay que **montar como volumen** para que sobreviva a la recreación del contenedor.

Desde 2026-08-28, `apps/camera-yolo` **sí** usa `camera_config.json` como fuente de verdad:
`apps/camera-yolo/config/camera_config.json` se carga al arrancar y se aplica a la cámara; el
panel edita y "Guardar" lo persiste. La copia congelada en `services/camera-service/legacy/`
sigue siendo solo referencia.

## Gestión de propiedades (`apps/camera-yolo/camera_props.py`)

Cubre **~48 propiedades escalares R/W** + 3 acciones (`once_wb`, `once_bb`, `save_to_camera`).
Definiciones basadas en `services/camera-service/legacy/properties_report.txt` (escaneo real de
esta cámara) + pares `CameraGet/CameraSet` de `mvsdk.py`.

### Comportamientos verificados de la HT-GE134GM

- **`CameraStop` + `CameraPlay` revierte los cambios en vivo**: la cámara recarga su grupo de
  parámetros al reanudar la adquisición. `apply_batch()` solo detiene la adquisición para el
  conjunto `_NEEDS_STOP` (`isp_processor`, `trans_pack_len`, `parameter_mode`, `media_type`);
  el resto se escribe en vivo.
- **Algunos `CameraGetXxx` no reflejan de inmediato el `CameraSetXxx`** correspondiente. Por eso
  `config_save()` guarda desde la *intención* (lo aplicado / lo cargado), no releyendo la cámara.

### Pendientes de resolver — propiedades y métodos

| # | Tema | Detalle |
|---|---|---|
| 1 | **`ImageResolution` / ROI / binning** | No expuesto (es struct). La resolución está fija en 1280×1024 en `camera_loop()`. Necesario para recortes/binning/otras resoluciones. |
| 2 | `_NEEDS_STOP` sin verificar | El conjunto de propiedades que realmente exigen parar la adquisición es una conjetura; hay que probar una a una. |
| 3 | Rangos no aplicados en la UI | Varias props traen `range` (`CameraGetExposureTimeRange`, etc.) pero el panel muestra un `number` sin min/max → se pueden meter valores fuera de rango. |
| 4 | Enums sin opciones legibles | `media_type`, `clr_temp_mode`, `hdr_gain_mode`, `preset_clr_temp` se muestran como enteros; falta extraer los enum de `CameraDefine.h` o probar empíricamente. `wb_mode` y `strobe_polarity` se asumen booleanos. |
| 5 | `ae_algorithm` / `bayer_dec_algorithm` | Excluidas: sus getters/setters llevan un arg extra `iIspProcessor` (no encajan en el patrón `fn(h)` / `fn(h, v)`). |
| 6 | Ganancias digitales RGB en cámara mono | `gain_r/g/b` expuestas pero sin efecto visible en un sensor monocromo. |
| 7 | Structs/arrays sin exponer | `AeWindow` (ventana de medición AE), `WbWindow`, `UserClrTempMatrix`, `Denoise3DParams`, `CustomLut`, `UndistortParams`, `CrossLine`, `TransferRoi`. |
| 8 | Métodos sin exponer | `CameraSaveParameterToFile` / `LoadParameterFromFile` (perfiles), `CameraLoadParameter` (cargar de un grupo del flash), reset de fábrica, control de LED/anillo de luz, calibración de píxeles muertos. |
| 9 | `acquisition_frame_rate` nativa | Depende del parche ctypes `camera_get/set_frame_rate` (la función no está en `mvsdk.py`). `0` = frecuencia máxima. Frágil. |
| 10 | Ciclo de vida de la cámara | `main.py` no llama `CameraStop` antes de `CameraUnInit`, y solo captura `KeyboardInterrupt`. Un kill forzado / cerrar la ventana deja la cámara tomada hasta el timeout de heartbeat (~1 min). Falta `CameraStop` + manejador de señales (`SIGTERM`) + `atexit`. |
| 11 | Prueba del contenedor con cámara | Pendiente en un host Linux con NIC GigE (Docker Desktop en Windows no alcanza la NIC física). |
| 12 | Ruido en git de `camera_config.json` | `saved_at` cambia en cada guardado. Evaluar gitignorearlo y dejar solo un `.example`. |

## Sin hardware

`CameraEnumerateDevice()` devuelve vacío → `RuntimeError` → el proceso termina. No hay mock.
El backend `Fake` está en la hoja de ruta ([`roadmap-skill.md`](roadmap-skill.md)).
