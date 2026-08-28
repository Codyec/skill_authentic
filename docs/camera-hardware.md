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

`camera_config.json` (en `services/camera-service/legacy/`) se **escribe** con `save_config` pero
ningún script lo vuelve a leer/aplicar. Reaplicar configuración es trabajo del refactor futuro.

## Sin hardware

`CameraEnumerateDevice()` devuelve vacío → `RuntimeError` → el proceso termina. No hay mock.
El backend `Fake` está en la hoja de ruta ([`roadmap-skill.md`](roadmap-skill.md)).
