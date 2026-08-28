# __BRAND__ — plataforma con autenticación

Base generada con la skill `auth-service`: login (OpenID Connect), panel de
administración de **usuarios** y **grupos**, y un **menú** que se amplía por
proyecto.

## Arrancar

```bash
docker compose up -d          # Postgres + servidor de identidad + app web
docker compose logs -f web-shell
```

- App: **http://localhost:__WEB_PORT__**
- Consola de identidad (solo administración técnica): **http://localhost:__KC_PORT__**
  (usuario y contraseña en `.env`: `KEYCLOAK_ADMIN_USERNAME` / `KEYCLOAK_ADMIN_PASSWORD`)

## Primer acceso

El sistema arranca sin usuarios. En la consola de identidad:

1. Realm `__REALM__` → **Users** → *Add user* → pestaña **Credentials** → poner contraseña.
2. **Groups** → `platform-admins` → **Members** → añadir ese usuario.
3. Esa persona entra en la app y ya tiene el panel de administración; desde ahí
   crea al resto de usuarios.

## Añadir secciones del proyecto

En `web-shell/app/main.py`:

1. Añade una entrada a `MAIN_MENU` (`admin_only` o `roles: ["operador"]` para
   controlar quién la ve).
2. Añade la ruta protegida, copiando el patrón de `GET /proceso`.
3. Crea su plantilla en `web-shell/app/templates/`.

Editar plantillas o CSS → recargar el navegador. Editar `main.py` →
`docker compose restart web-shell`.

## Roles

`Administrador` ▸ `Supervisor` ▸ `Operador` (jerárquicos: el de arriba incluye a
los de abajo). Se asignan desde el panel de usuarios.

## Parar / limpiar

```bash
docker compose down            # para
docker compose down -v         # para y borra la base de datos
```
