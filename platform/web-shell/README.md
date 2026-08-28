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
- La edición de cada usuario ocurre en un diálogo modal con secciones
  (Datos · Estado · Roles · Grupos · Contraseña · Eliminar).

### Jerarquía de roles

Los roles son **compuestos**: `admin` ▸ `supervisor` ▸ `operador`. Quien tiene `admin`
hereda `supervisor` y `operador`; quien tiene `supervisor` hereda `operador`. En el
diálogo, los roles heredados salen con `↳` y no se quitan por separado.

- Instalaciones nuevas: ya viene configurado en `DEMACYA-realm.json`
  (`composites.realm`).
- Instancia ya creada: en la consola de Keycloak → *Realm roles* → `admin` →
  pestaña *Associated roles* → *Assign role* → `supervisor`. Repetir con
  `supervisor` → `operador`. Después, cerrar sesión y volver a entrar.

## Grupos (`/admin/grupos`)

- Árbol de grupos con **subgrupos** (p. ej. `Producción → Turno A`), nº de miembros,
  y **crear / eliminar** (crear pide "dentro de" un grupo padre o "Grupo principal").
- La **pertenencia** de cada usuario se asigna en el diálogo de edición del usuario
  (sección *Grupos*: fichas para quitar, desplegable jerárquico para añadir).
- Ver y editar la pertenencia solo necesita `manage-users` + `query-groups`.
  **Crear o eliminar** un grupo necesita el permiso `manage-realm` de Keycloak.
- Qué roles otorga cada grupo se configura en la consola de Keycloak (fuera de este panel).

### Dar `manage-realm` (una vez, en la instancia ya creada)

El grupo `platform-admins` solo existe en instalaciones nuevas. En la actual, con una
cuenta admin del realm `master`:

```bash
docker exec -it keycloak /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user <admin-maestro>
docker exec -it keycloak /opt/keycloak/bin/kcadm.sh add-roles -r DEMACYA \
  --uusername empresa-admin --cclientid realm-management --rolename manage-realm
```

o en la consola: Users → (usuario) → Role mapping → Assign role → filtrar por clients →
`realm-management` → `manage-realm`. Después, cerrar sesión y volver a entrar en el panel.

## Ejecutar

```bash
cp .env.example .env      # completar KEYCLOAK_CLIENT_SECRET y SESSION_SECRET
docker compose up -d
```

Editar plantillas (`app/templates/`) o CSS (`app/static/app.css`) → recargar el navegador.
Editar `app/main.py` → `docker compose restart demacya-app`.
Cambiar `requirements.txt` → `docker compose up -d --build`.
