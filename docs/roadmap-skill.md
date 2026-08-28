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

- HAL `CameraBackend`: `MvsdkBackend` (real) + `FakeBackend` (frames sintéticos, para CI/dev).
- Core sin framework: adquisición, gestor de propiedades (basado en el `property_definitions()`
  actual), config declarativa `camera.yaml` idempotente, supervisor de reconexión.
- API `/api/v1` + `/capabilities` + `/health`; entrega MJPEG + bus.
- `Dockerfile` Linux real + `docker-compose.yml` con `network_mode: host`.

## Fase 5 — Empaquetar como skill

Crear `.claude/skills/`:

- `demacya-platform-init/` — instrucciones + `templates/` del monorepo (infra + web-shell + auth).
- `demacya-camera-service/` — instrucciones para añadir el servicio de cámara a un proyecto.

Cada skill: `SKILL.md` con pasos, `templates/` con los archivos parametrizables, y un checklist de
verificación (levantar compose, `/health`, login OIDC de punta a punta).
