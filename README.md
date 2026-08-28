# DEMACYA — Monorepo de plataforma

Base reutilizable para el software de DEMACYA: **gestión de usuarios** (Keycloak + Postgres) +
un **menú dinámico post-login** que cambia según el proyecto (consultas, dashboards en tiempo real,
con o sin cámara). Todo pensado para desplegarse con **Docker** en cualquier máquina.

> Estado: **reorganización inicial**. Esta ronda solo reordena los archivos existentes en una
> estructura de monorepo. Nada de lógica se ha reescrito todavía. Ver
> [`docs/roadmap-skill.md`](docs/roadmap-skill.md) para las siguientes fases.

## Estructura

| Carpeta | Qué es | Estado |
|---|---|---|
| [`infra/keycloak/`](infra/keycloak/) | Keycloak 26.7.1 + Postgres 16 (docker-compose), realm `DEMACYA` | Funcional (movido sin cambios) |
| [`platform/web-shell/`](platform/web-shell/) | FastAPI + OIDC: login + dashboard con "Menú principal" | Prototipo (movido sin cambios) |
| [`platform/auth/`](platform/) | Helper OIDC/roles reutilizable | Pendiente |
| [`services/camera-service/`](services/camera-service/) | Servicio de cámara industrial (MindVision / MVCAMSDK) | `legacy/` funcional; `src/` pendiente de refactor |
| [`apps/camera-yolo/`](apps/camera-yolo/) | App demo: cámara + detección YOLO26 (CUDA). Consumidora, no parte de la base | Movida sin cambios |
| [`vendor/mindvision-sdk/`](vendor/mindvision-sdk/) | SDK del fabricante (Linux `.so` + headers + GenTL + v4l2) | Vendorizado |
| [`docs/`](docs/) | Arquitectura, hardware de cámara, hoja de ruta hacia skill de Claude | — |

## Arranque rápido (piezas actuales)

```bash
# Identidad / SSO
cd infra/keycloak && cp .env.example .env && docker compose up -d

# Web shell (login + menú)
cd platform/web-shell && cp .env.example .env && docker compose up -d

# Servicio de cámara (hoy: script legacy, requiere SDK nativo + cámara)
python services/camera-service/legacy/camera_service_FUNCIONAL_FINAL.py   # http://localhost:8010
```

## Historia / git

- Commit `baseline`: estado del workspace **antes** de reorganizar (recuperable).
- Los `.py` de cámara solo cambiaron de ruta (`git mv`), su contenido es idéntico.
- Los `.tar.gz` del SDK **no** están en git (fuente pristina en `vendor/mindvision-sdk/archives/`, en disco).
