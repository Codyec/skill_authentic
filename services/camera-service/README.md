# camera-service

Servicio de cámara industrial (MindVision / MVCAMSDK). Aísla el SDK nativo y expone la cámara
al resto de la plataforma.

## Estado

- **`legacy/`** — código tal cual venía funcionando el 2026-08-28. Ejecutable, **no editar**.
  Es la referencia de comportamiento para el refactor.
- **`src/`** — vacío. Destino del refactor (HAL + core + API). Ver
  [`../../docs/roadmap-skill.md`](../../docs/roadmap-skill.md), Fase 4.

## Ejecutar el servicio legacy

Requiere: cámara MindVision accesible + SDK nativo instalado en el SO
(`vendor/mindvision-sdk/linux/install.sh` en Linux, o el instalador del fabricante en Windows) +
`pip install -r requirements.txt`.

```bash
python legacy/camera_service_FUNCIONAL_FINAL.py   # http://localhost:8010
python legacy/test_properties.py                  # diagnóstico de propiedades
```

Sin cámara, aborta en `CameraEnumerateDevice()` (comportamiento esperado; no hay mock todavía).

## Docker

`Dockerfile` y `docker-compose.yml` son **plantillas comentadas**. No construyen todavía: primero
hay que decidir la imagen base Linux y validar `libMVSDK.so` contra una cámara real. Ver
`docs/camera-hardware.md` para los requisitos de red (GigE necesita `network_mode: host`).
