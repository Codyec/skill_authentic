# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`camaron/` is the **DEMACYA monorepo** — a reusable base platform for internal industrial software.
It contains four independent parts that are *not* wired together into a single build:

| Path | What it is | Runtime |
|---|---|---|
| `platform/web-shell/` | FastAPI app: Keycloak OIDC login + dashboard with a dynamic menu + user/group admin | Docker (Python 3.11) |
| `infra/keycloak/` | Keycloak 26.7.1 + Postgres 16 (identity provider + DB) | Docker |
| `services/camera-service/` | Camera control/streaming service for MindVision/MVCAMSDK GigE cameras | Native (Windows today) |
| `apps/camera-yolo/` | Demo app: camera + YOLO26 object detection, consumes the camera directly | Native (Windows + CUDA) |
| `vendor/mindvision-sdk/` | Vendored camera SDK: `libMVSDK.so` (x64/arm64), headers, `install.sh`, GenTL, v4l2 | — |
| `docs/` | `architecture.md` (full A–F analysis), `camera-hardware.md`, `roadmap-skill.md` (phased plan) | — |

Long-term intent (see `docs/roadmap-skill.md`): the `platform/` layer becomes a reusable Claude Code
skill so new company projects start with auth + menu + admin out of the box.

## Commands

### web-shell + Keycloak (the active work)

Container names are pinned (`demacya-app`, `keycloak`, `keycloak-postgres`), so compose **project
names matter** — always pass `-p` to avoid creating duplicates:

```bash
# Keycloak + Postgres — project name MUST be keycloak-lab (reuses the existing volume)
cd infra/keycloak && docker compose -p keycloak-lab up -d

# web-shell — project name web-shell
cd platform/web-shell && docker compose -p web-shell up -d
docker compose -p web-shell restart demacya-app      # after editing app/main.py
docker compose -p web-shell up -d --build            # after editing requirements.txt
docker compose -p web-shell logs -f demacya-app
```

`platform/web-shell/docker-compose.yml` bind-mounts `./app` into the container, so **editing
templates (`app/templates/*.html`) or CSS (`app/static/app.css`) needs only a browser reload** — no
restart. Only `app/main.py` changes require `restart`.

URLs: web-shell `http://localhost:8000` · Keycloak `http://localhost:8081` (realm `DEMACYA`).

### Checking web-shell changes without Docker

There is no test suite. Validate edits with:

```bash
# Python 3.11 env that has cv2/fastapi/jinja2 available on this machine:
PY=C:/Users/dmore/anaconda3/envs/vision_leche/python.exe

$PY -m py_compile platform/web-shell/app/main.py
$PY -c "from jinja2 import Environment,FileSystemLoader; \
  [Environment(loader=FileSystemLoader('platform/web-shell/app/templates')).get_template(t) \
   for t in ('login.html','dashboard.html','admin_users.html','admin_groups.html','forbidden.html')]"
```

### Camera code

```bash
cd services/camera-service/legacy
C:/Users/dmore/anaconda3/envs/vision_leche/python.exe camera_service_FUNCIONAL_FINAL.py   # :8010
C:/Users/dmore/anaconda3/envs/vision_leche/python.exe test_properties.py                  # property scan
```

Requires a physical MindVision camera reachable on the NIC subnet **and** the native SDK installed
(`MVCAMSDK_X64.dll` on Windows / `vendor/mindvision-sdk/linux/install.sh` on Linux). With no camera
the scripts abort at `mvsdk.CameraEnumerateDevice()` — that is expected; there is no mock/simulator.

### Inspecting Keycloak state

```bash
MSYS_NO_PATHCONV=1 docker exec keycloak-postgres psql -U keycloak -d keycloak -c "SELECT ..."
```
`MSYS_NO_PATHCONV=1` is required in Git Bash for any `docker exec` whose args contain `/opt/...` or
`/realms/...` paths, otherwise Git Bash mangles them.

## web-shell architecture (`platform/web-shell/app/main.py`)

Single-file FastAPI app. Server-rendered Jinja templates, form POSTs, PRG redirects — no client-side
framework, no JS build.

- **Auth**: Authlib OIDC against Keycloak. On `/callback` the session stores `user`, `roles`
  (assignable realm roles only), `is_admin` (bool), `refresh_token`, `id_token`. Session is a signed
  cookie (`SessionMiddleware`); keep it small — do **not** store access tokens.
- **Admin API proxy**: the app has no service account. `admin_access_token()` mints a fresh access
  token from the logged-in user's `refresh_token`; `kc_admin(request, method, path, **kw)` calls the
  Keycloak Admin REST API with it. Keycloak re-validates every permission, so the UI gate
  (`is_admin`, `_require_admin`) is convenience only.
- **Permission model**: "platform admin" = realm role `admin` **or** client role
  `realm-management/manage-users`. Group **create/delete** additionally needs `manage-realm` (handled
  gracefully with `_NEED_MANAGE_REALM` when missing). `manage-users` alone covers users + group
  membership.
- **Role hierarchy**: `admin ▸ supervisor ▸ operador` are Keycloak *composite* roles.
  `_load_users` fetches both direct (`role-mappings/realm`) and effective
  (`role-mappings/realm/composite`) roles; the edit modal renders inherited roles as locked (`↳`).
- **Groups**: `_load_groups` returns a flattened tree (recurses `/groups/{id}/children`) with
  `depth`, `pretty` path, `member_count`. Groups support subgroups.
- **Dynamic menu**: `MAIN_MENU` list in `main.py` (`{id,title,description,href,enabled,admin_only}`),
  filtered by `visible_menu(is_admin)`. This is the per-project extension point.
- **Edit UX**: `/admin/usuarios` table is read-only; each row's "Editar" opens a `<dialog>` modal
  with sections (Datos/Estado/Roles/Grupos/Contraseña/Eliminar). Each section is its own form to an
  existing endpoint; on submit `_redir_users(..., edit=user_id)` re-opens that modal via `?edit=`.

### Conventions

- **User-facing text must not leak technical terms** — no "Keycloak", "realm", "manage-realm",
  container names, or raw provider error strings in templates or flash messages. `_kc_error()`
  translates HTTP status codes to plain Spanish. Code comments and `README.md` keep the real terms.
- UI language is Spanish.
- Keycloak realm config lives in `infra/keycloak/keycloak/import/DEMACYA-realm.json` — edits there
  only apply to **fresh** installs (`--import-realm` skips an existing realm). Changes to a running
  instance must be made via the Keycloak console/`kcadm`.
- `KC_HOSTNAME: http://localhost:8081` is set in the Keycloak compose so token `iss` stays constant
  regardless of whether the browser or the container makes the request — required for the
  refresh-token exchange to work. Don't remove it.

## Camera architecture

- `mvsdk.py` is the vendor's ctypes wrapper; it loads the native lib **at import time**
  (`windll.MVCAMSDK_X64` on Windows / `cdll libMVSDK.so` on Linux). Any import fails hard without it.
- `services/camera-service/legacy/` is **frozen reference code** — the scripts as they worked on
  2026-08-28. Do not edit; the refactor target is the empty `services/camera-service/src/`
  (see `docs/roadmap-skill.md` Phase 4: HAL + fake backend + API).
- Missing pieces for a real camera service: no `requirements.txt`-installable SDK, GigE needs
  `network_mode: host`, hardcoded absolute Windows paths in `apps/camera-yolo/main.py`.

## Git

Not previously a repo before this monorepo work; commit `f4edd91` is the pre-reorg baseline.
Camera files were relocated with `git mv` (renames, content unchanged).
The `.tar.gz` SDK archives and `vendor/mindvision-sdk/linux/lib/{x86,arm,arm_softfp}/` are gitignored.
