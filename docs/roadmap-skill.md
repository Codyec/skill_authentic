# Hoja de ruta — de monorepo a habilidad (skill) de Claude

Objetivo: que en un proyecto futuro baste decir *"crea la base DEMACYA con autenticación y menú
principal"* y Claude genere el esqueleto sin re-derivar nada.

## Fase 0 — Reorganización (HECHO)

Estructura de monorepo, cámara aislada en `services/camera-service/`, SDK vendorizado, git baseline.

## Fase 1 — `platform/auth` reutilizable

- Extraer de `platform/web-shell/app/main.py` la integración OIDC (registro del cliente, `/login`,
  `/callback`, `/logout`, middleware de sesión) a un paquete instalable `demacya_auth`.
- Dependencia de config por entorno: `KEYCLOAK_*`, `REDIRECT_URI`, `SESSION_SECRET`.
- Decorador / dependencia FastAPI `require_roles("admin", "supervisor")` usando los roles del realm
  (`admin` / `supervisor` / `operador`).

## Fase 2 — `platform/web-shell` con menú dinámico

- `menu.config.yaml` por proyecto: lista de entradas `{ id, label, icon, path, roles, module }`.
- El dashboard renderiza el menú desde ese YAML; cada `module` es un componente montable
  (consulta, dashboard de monitor en tiempo real, visor de cámara, etc.).
- Módulos como plugins: contrato mínimo `mount(app, config)` + template parcial.

## Fase 3 — `infra` unificada

- Un `docker-compose.yml` raíz que orqueste Postgres (compartido: Keycloak + datos de app),
  Keycloak, web-shell y los servicios que el proyecto seleccione (perfiles de compose).
- `infra/postgres/` con init SQL para el esquema de datos de aplicación (no solo Keycloak).

## Fase 4 — `services/camera-service` refactor

> **Nota (2026-08-28):** el panel de propiedades + `camera_config.json` como fuente de verdad
> ya se integró en `apps/camera-yolo` (contenedor de inferencia), con `Dockerfile` multi-arch
> sobre la base Ultralytics (GPU / CPU / Raspberry Pi 5). El módulo `apps/camera-yolo/camera_props.py`
> cubre ~48 propiedades escalares R/W + acciones (`once_wb`, `once_bb`, `save_to_camera`) y es
> candidato a compartirse con este refactor. Esto NO sustituye la Fase 4 (HAL + FakeBackend + API).
> **Pendientes de propiedades/métodos** (rangos en UI, enums sin mapear, ROI/binning, ciclo de
> vida `CameraStop`+`SIGTERM`, structs sin exponer): ver la tabla en
> [`camera-hardware.md`](camera-hardware.md#pendientes-de-resolver--propiedades-y-métodos).

- HAL `CameraBackend`: `MvsdkBackend` (real) + `FakeBackend` (frames sintéticos, para CI/dev).
- Core sin framework: adquisición, gestor de propiedades (basado en el `property_definitions()`
  actual), config declarativa `camera.yaml` idempotente, supervisor de reconexión.
- API `/api/v1` + `/capabilities` + `/health`; entrega MJPEG + bus.
- `Dockerfile` Linux real + `docker-compose.yml` con `network_mode: host`.

## Fase 5 — Empaquetar como skill  (HECHO para auth)

`.claude/skills/auth-service/` — genera un proyecto autónomo (Postgres + Keycloak + web-shell
FastAPI) parametrizado por `slug / realm / marca / puertos / logo`.

- `SKILL.md` — cuándo se dispara + pasos (scaffold, `docker compose up`, verificación,
  primer usuario, cómo añadir secciones).
- `templates/` — `docker-compose.yml` combinado, `keycloak/import/realm.json`
  (roles jerárquicos + grupo `platform-admins` + cliente OIDC + tema), `web-shell/` (copia
  genérica de `platform/web-shell` — marca vía globals de Jinja `brand`/`logo`/`tagline`).
- `scripts/scaffold.py` — copia + sustituye marcadores `__SLUG__` etc + genera `.env` con secretos.
- `scripts/update-templates.py` — re-sincroniza los templates desde el repo (copia maestra).

Instalada también en `~/.claude/skills/auth-service/` para dispararse en cualquier proyecto.
Para actualizarla: mejorar `platform/web-shell` / `infra/keycloak/themes/demacya`, correr
`update-templates.py`, y volver a copiar la carpeta a `~/.claude/skills/`.

Pendiente: `camera-service` como skill (tras la Fase 4).

### Mejora opcional anotada

Migrar la instancia DEMACYA en marcha al `docker-compose.yml` único de la skill
(hoy usa los dos compose `infra/keycloak` + `platform/web-shell` con `host.docker.internal`).
Implica recrear contenedores; el volumen de Postgres tiene datos manuales (usuarios, grupos).
