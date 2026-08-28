# vendor/mindvision-sdk

SDK del fabricante de la cámara (MindVision / MVCAMSDK). Vendorizado tal cual se recibió.

## Versión

- Linux SDK: **V2.1.0.49** (build 202511141513).
- Wrapper Python de referencia del servicio: `services/camera-service/legacy/mvsdk.py`
  (más completo que el `demo/python_demo/mvsdk.py` de este SDK).

## Contenido

| Ruta | Qué es |
|---|---|
| `linux/lib/{x64,arm64}/libMVSDK.so` | Biblioteca nativa — **versionada** (arquitecturas de despliegue) |
| `linux/lib/{x86,arm,arm_softfp}/libMVSDK.so` | En disco pero **fuera de git** (ver `.gitignore` raíz) |
| `linux/include/*.h` | `CameraApi.h`, `CameraDefine.h`, `CameraStatus.h` |
| `linux/install.sh` | Instalador en el host: copia `.so` a `/lib`, headers a `/usr/include`, udev a `/etc/udev/rules.d/`. Requiere root + reinicio |
| `linux/88-mvusb.rules`, `linux/99-mvusb.rules` | udev para cámaras **USB** (`idVendor==f622`) |
| `linux/demo/python_demo/` | Demos Python del fabricante (referencia) |
| `linux/document/*.chm` | API reference (CHS / ENG) |
| `gentl/` | GenTL Producer para **Windows** (`WinMVGenTL_*.cti`) + proyecto Visual Studio |
| `v4l2/` | `mv_v4l2` + `v4l2loopback`: exponer la cámara como `/dev/videoN` (opcional) |
| `archives/` | `.tar.gz` originales — **fuera de git**, fuente pristina en disco |

## Instalar en un host Linux

```bash
cd linux && sudo ./install.sh && sudo reboot
```

## Windows

`MVCAMSDK_X64.dll` no está aquí: viene del instalador del fabricante para Windows (deja además
entradas de registro que el SDK necesita).

## Licencia

SDK propietario del fabricante. Revisar los términos antes de redistribuir imágenes Docker que
incluyan `libMVSDK.so`.
