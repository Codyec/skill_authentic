# web-shell

Cáscara web de la plataforma DEMACYA: login vía Keycloak (OIDC) + panel con
**menú dinámico** + **administración de usuarios**.

## Rutas

| Ruta | Descripción |
|---|---|
| `/` | Login (redirige a `/dashboard` si ya hay sesión) |
| `/login` `/callback` `/logout` | Flujo OIDC contra Keycloak |
| `/dashboard` | Panel principal. El menú se arma desde `MAIN_MENU` en `app/main.py` |
| `/admin/usuarios` | Alta/baja/edición de usuarios y roles. Solo para administradores |

## Menú dinámico

`MAIN_MENU` (en `app/main.py`) es una lista de items `{id, title, description, href, enabled, admin_only}`.
Se agregan/quitan por proyecto. Los `admin_only` solo se ven si el usuario es administrador.

## Administración de usuarios

- Se considera **administrador** quien tenga el rol de realm `admin` **o** el permiso
  `realm-management/manage-users` de Keycloak.
- Las operaciones se ejecutan contra la **Admin REST API de Keycloak** usando el
  `refresh_token` del propio usuario en sesión (Keycloak revalida cada permiso).
- Para dar acceso de administración a alguien: en Keycloak, añadir al usuario al grupo
  **`platform-admins`** (Realm settings → Groups) o asignarle el rol `admin`.
- Permite: crear usuario (con contraseña temporal), habilitar/deshabilitar, cambiar
  contraseña, eliminar y asignar/quitar los roles `admin` / `supervisor` / `operador`.

## Ejecutar

```bash
cp .env.example .env      # completar KEYCLOAK_CLIENT_SECRET y SESSION_SECRET
docker compose up -d
```

Editar plantillas (`app/templates/`) o CSS (`app/static/app.css`) → recargar el navegador.
Editar `app/main.py` → `docker compose restart demacya-app`.
Cambiar `requirements.txt` → `docker compose up -d --build`.
