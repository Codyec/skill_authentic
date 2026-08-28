---
name: auth-service
description: >-
  Scaffold a Dockerized authentication + admin base: Keycloak (OpenID Connect)
  login with a branded theme, a FastAPI web-shell with user & group administration
  (roles with an admin▸supervisor▸operador hierarchy, subgroups), and a dashboard
  whose menu is extended per project. Use whenever the user is starting a service,
  app or internal tool that needs user login, accounts, roles, permissions or an
  admin panel — e.g. "creemos un servicio con autenticación", "necesito login para
  esta app", "una base con gestión de usuarios", "portal con usuarios y permisos".
  Everything runs from one `docker compose`.
---

# auth-service

Congela la base de autenticación de DEMACYA (`platform/web-shell` + `infra/keycloak`
del monorepo) en un generador reutilizable.

## Cuándo usarla

El usuario va a empezar un proyecto/servicio que necesita autenticación + gestión
de usuarios. NO usarla para añadir auth a un backend que ya existe con su propio
stack — esto genera un stack completo nuevo (Postgres + Keycloak + app web FastAPI).

## Qué genera

Un directorio autónomo con:
- `docker-compose.yml` — Postgres 16 + Keycloak 26.7 + la app web, red interna,
  nombre de proyecto fijo (no hace falta `-p`).
- `web-shell/` — app FastAPI: login OIDC, `/dashboard` con menú, `/admin/usuarios`
  y `/admin/grupos`, y una sección de ejemplo `/proceso` protegida por rol.
- `keycloak/import/realm.json` — realm con roles jerárquicos (`admin▸supervisor▸
  operador`), grupo `platform-admins` (con permisos de administración de identidad),
  cliente OIDC público con PKCE, y tema de login de marca.
- `.env` con secretos aleatorios (incluida la contraseña del admin de Keycloak).

## Pasos

1. **Reúne los parámetros** (pregunta lo que falte):
   - `slug`: identificador corto en minúsculas (`^[a-z][a-z0-9-]{2,30}$`), p. ej. `acme`.
   - `brand`: nombre visible, p. ej. `"ACME S.A."` (def. = slug capitalizado).
   - `dir`: carpeta destino del proyecto nuevo.
   - `realm`: def. = slug en mayúsculas. Normalmente se deja el default.
   - Puertos: por defecto `8000` (web) y `8081` (identidad). Cámbialos si están ocupados
     (`docker ps` / `curl localhost:8000`).
   - `logo`: PNG de marca opcional (fondo transparente, se ve sobre oscuro).

2. **Genera el proyecto**:
   ```bash
   python <ruta-de-esta-skill>/scripts/scaffold.py \
     --slug <slug> --brand "<brand>" --dir <dir> \
     [--realm <REALM>] [--web-port <N>] [--kc-port <N>] [--logo <ruta.png>]
   ```
   Comprueba que no queden marcadores sin resolver: `grep -R "__SLUG__\|__REALM__\|__RANDOM__" <dir>` vacío.

3. **Levanta el stack**:
   ```bash
   cd <dir> && docker compose up -d
   ```
   Espera ~20 s. Verifica:
   - `curl -s http://localhost:<KC_PORT>/realms/<REALM>/.well-known/openid-configuration`
     devuelve JSON con `"issuer"`.
   - `curl -s -o /dev/null -w '%{http_code}' http://localhost:<WEB_PORT>/` → `200`.
   Reporta al usuario las dos URLs y la contraseña del admin (está en `<dir>/.env`,
   `KEYCLOAK_ADMIN_PASSWORD`, y la imprime el script).

4. **Primer acceso** (el sistema arranca sin usuarios). Guía al usuario:
   - Consola de identidad `http://localhost:<KC_PORT>` → login con `admin` / la
     contraseña generada → realm `<REALM>` → **Users** → *Add user* → pestaña
     **Credentials** → poner contraseña.
   - **Groups** → `platform-admins` → **Members** → añadir ese usuario.
   - Esa persona entra en `http://localhost:<WEB_PORT>` y ya tiene el panel de
     administración; desde ahí crea al resto de usuarios.

5. **Explica cómo crecer**: las secciones del proyecto se añaden en
   `web-shell/app/main.py` — una entrada en `MAIN_MENU` (`admin_only` o
   `roles: ["operador"]`) + una ruta protegida copiando el patrón de `GET /proceso`
   + su plantilla en `web-shell/app/templates/`.

## Reglas al trabajar sobre lo generado

- Un solo `docker compose` (sin `-p`). Editar plantillas/CSS → recargar el
  navegador; editar `main.py` → `docker compose restart web-shell`.
- **Nada de jerga técnica en los textos que ve el usuario** (no "Keycloak",
  "realm", "token", nombres de contenedor, ni mensajes crudos del proveedor de
  identidad). La UI va en español.
- La jerarquía de roles ya viene en el realm import; no hace falta configurarla.
- No commitear `.env`.

## Mantenimiento de la skill

`platform/web-shell` + `infra/keycloak/themes/demacya` del monorepo son la copia
maestra. Tras mejorarlos, `python scripts/update-templates.py` re-sincroniza los
templates (avisa si quedan literales de marca sin genericizar).
