# legacy/ — código congelado

Copia **verbatim** de los scripts de cámara tal cual funcionaban el **2026-08-28**, movidos aquí
con `git mv` (contenido idéntico al commit `baseline`).

**No editar.** Sirve como referencia de comportamiento para el refactor en `../src/`.

| Archivo | Qué es |
|---|---|
| `camera_service_FUNCIONAL_FINAL.py` | Servicio de configuración de propiedades + video MJPEG (`:8010`) |
| `main.py` — *(movido a `apps/camera-yolo/`)* | — |
| `test_properties.py` | Diagnóstico: descubre pares Get/Set y clasifica RO/RW; genera `properties_report.txt` |
| `mvsdk.py` | Wrapper ctypes del SDK (Windows `windll.MVCAMSDK_X64` / Linux `cdll libMVSDK.so`). Archivo del fabricante |
| `Logo_DEMA.png` | Logo servido en `/logo.png` (lo busca junto al script) |
| `camera_config.json` | Salida de `save_config` (no se reaplica) |
| `camera_properties.json` | Volcado de `CameraGetCapability` |
| `properties_report.txt` | Salida de `test_properties.py` |
| `cv_grab*.py`, `grab.py`, `readme.txt` | Demos del fabricante (Windows SDK, 2020) |

`camera_service_FUNCIONAL_FINAL.py` usa `os.path.dirname(__file__)` para `Logo_DEMA.png`,
`camera_config.json` y para escribir `save_config`: por eso todos viven en esta misma carpeta y el
script corre igual desde aquí.
