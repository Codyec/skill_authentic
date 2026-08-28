import base64
import json
import os
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from authlib.integrations.starlette_client import OAuth


# ============================================================
# VARIABLES DE ENTORNO
# ============================================================

load_dotenv()

# Identidad visible (marca y logo). Se define por proyecto vía entorno;
# los defaults mantienen la identidad DEMACYA.
BRAND = os.getenv("BRAND", "DEMACYA")
LOGO_FILE = os.getenv("LOGO_FILE", "logo.png")
TAGLINE = os.getenv("TAGLINE", "Ingeniería de automatización industrial")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")


# ============================================================
# APP
# ============================================================

app = FastAPI(title=BRAND)


# ============================================================
# ARCHIVOS ESTÁTICOS
# ============================================================

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)


# ============================================================
# TEMPLATES
# ============================================================

templates = Jinja2Templates(
    directory="app/templates"
)

# Disponibles en todas las plantillas sin pasarlos en cada ruta.
templates.env.globals["brand"] = BRAND
templates.env.globals["logo"] = LOGO_FILE
templates.env.globals["tagline"] = TAGLINE


# ============================================================
# CONFIGURACIÓN KEYCLOAK
# ============================================================

KEYCLOAK_BROWSER_URL = os.getenv(
    "KEYCLOAK_BROWSER_URL",
    "http://localhost:8081",
)

KEYCLOAK_INTERNAL_URL = os.getenv(
    "KEYCLOAK_INTERNAL_URL",
    "http://host.docker.internal:8081",
)

KEYCLOAK_REALM = os.getenv(
    "KEYCLOAK_REALM",
    "DEMACYA",
)

CLIENT_ID = os.getenv(
    "KEYCLOAK_CLIENT_ID",
    "demacya-app",
)

CLIENT_SECRET = os.getenv(
    "KEYCLOAK_CLIENT_SECRET",
)

REDIRECT_URI = os.getenv(
    "REDIRECT_URI",
    "http://localhost:8000/callback",
)

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
)

# Endpoints internos (server-to-server) para la API de administración
KC_TOKEN_URL = (
    f"{KEYCLOAK_INTERNAL_URL}"
    f"/realms/{KEYCLOAK_REALM}"
    f"/protocol/openid-connect/token"
)

KC_ADMIN_BASE = (
    f"{KEYCLOAK_INTERNAL_URL}"
    f"/admin/realms/{KEYCLOAK_REALM}"
)

# Roles del realm que la plataforma sabe asignar desde el panel.
ASSIGNABLE_ROLES = ["admin", "supervisor", "operador"]


# ============================================================
# SESIONES
# ============================================================

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie=os.getenv("SESSION_COOKIE", "demacya_session"),
    max_age=3600,
    path="/",
    same_site="lax",
    https_only=False,
)


# ============================================================
# OIDC / KEYCLOAK
# ============================================================

oauth = OAuth()


oauth.register(
    name="keycloak",

    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,

    # --------------------------------------------------------
    # PKCE
    # --------------------------------------------------------

    code_challenge_method="S256",

    # --------------------------------------------------------
    # Authorization endpoint
    # --------------------------------------------------------

    authorize_url=(
        f"{KEYCLOAK_BROWSER_URL}"
        f"/realms/{KEYCLOAK_REALM}"
        f"/protocol/openid-connect/auth"
    ),

    # --------------------------------------------------------
    # Token endpoint
    # --------------------------------------------------------

    access_token_url=(
        f"{KEYCLOAK_INTERNAL_URL}"
        f"/realms/{KEYCLOAK_REALM}"
        f"/protocol/openid-connect/token"
    ),

    # --------------------------------------------------------
    # UserInfo endpoint
    # --------------------------------------------------------

    userinfo_endpoint=(
        f"{KEYCLOAK_INTERNAL_URL}"
        f"/realms/{KEYCLOAK_REALM}"
        f"/protocol/openid-connect/userinfo"
    ),

    # --------------------------------------------------------
    # JWK / Public keys
    # --------------------------------------------------------

    jwks_uri=(
        f"{KEYCLOAK_INTERNAL_URL}"
        f"/realms/{KEYCLOAK_REALM}"
        f"/protocol/openid-connect/certs"
    ),

    # --------------------------------------------------------
    # OIDC scopes
    # --------------------------------------------------------

    client_kwargs={
        "scope": "openid profile email",
    },
)


# ============================================================
# MENÚ PRINCIPAL
# ============================================================
# Definición del menú post-login. AQUÍ es donde cada proyecto
# añade o quita secciones. Cada item admite:
#   admin_only: True     -> solo para administradores
#   roles: ["operador"]  -> solo si el usuario tiene alguno de esos roles
#   enabled: False        -> se muestra atenuado como "Próximamente"
# Ver la ruta de ejemplo /proceso más abajo.

MAIN_MENU = [
    {
        "id": "usuarios",
        "title": "Usuarios",
        "description": "Alta, baja y modificación de usuarios y de sus permisos.",
        "href": "/admin/usuarios",
        "enabled": True,
        "admin_only": True,
    },
    {
        "id": "grupos",
        "title": "Grupos",
        "description": "Crear grupos y organizar a los usuarios por área o función.",
        "href": "/admin/grupos",
        "enabled": True,
        "admin_only": True,
    },
    {
        "id": "proceso",
        "title": "Proceso",
        "description": "Sección de ejemplo: solo la ve quien tenga el rol de operador o superior.",
        "href": "/proceso",
        "enabled": True,
        "roles": ["operador"],
    },
]


def visible_menu(request):
    """Filtra el menú según los permisos del usuario en sesión."""
    admin = is_admin(request)
    roles = set(get_roles(request))
    result = []
    for item in MAIN_MENU:
        if item.get("admin_only") and not admin:
            continue
        needed = item.get("roles")
        if needed and not (roles & set(needed)) and not admin:
            continue
        result.append(item)
    return result


# ============================================================
# SESIÓN / AUTORIZACIÓN
# ============================================================

def _claims_from_token(access_token):
    """Decodifica el JWT SIN verificar firma (solo para pintar el menú y
    esconder rutas; el control real lo hace Keycloak con el token)."""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def get_user(request):
    return request.session.get("user")


def get_roles(request):
    return request.session.get("roles", [])


def is_admin(request):
    """Admin de la plataforma = rol de realm 'admin', o permiso de Keycloak
    para gestionar usuarios (realm-management/manage-users). Se calcula en
    el callback y se guarda como booleano para no engordar la cookie."""
    return bool(request.session.get("is_admin"))


async def admin_access_token(request):
    """Devuelve un access_token fresco del usuario en sesión, usando su
    refresh_token. Keycloak rota el refresh_token: se guarda el nuevo."""
    refresh_token = request.session.get("refresh_token")
    if not refresh_token:
        print("[admin] sin refresh_token en la sesión")
        return None

    base = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }

    # El cliente puede ser público (sin secreto) o confidencial. Se prueba
    # primero sin secreto; si Keycloak lo rechaza y hay secreto, se reintenta.
    attempts = [base]
    if CLIENT_SECRET:
        attempts.append({**base, "client_secret": CLIENT_SECRET})

    async with httpx.AsyncClient(timeout=10) as client:
        for data in attempts:
            resp = await client.post(KC_TOKEN_URL, data=data)
            if resp.status_code == 200:
                tok = resp.json()
                if tok.get("refresh_token"):
                    request.session["refresh_token"] = tok["refresh_token"]
                return tok.get("access_token")
            print(
                f"[admin] refresh_token -> {resp.status_code} "
                f"{resp.text[:200]}"
            )

    return None


async def kc_admin(request, method, path, **kwargs):
    """Llama a la Admin REST API de Keycloak con el token del usuario."""
    token = await admin_access_token(request)
    if not token:
        return None

    async with httpx.AsyncClient(base_url=KC_ADMIN_BASE, timeout=15) as client:
        resp = await client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )

    if resp.status_code >= 400:
        print(f"[admin] {method} {path} -> {resp.status_code} {resp.text[:200]}")
    return resp


# ============================================================
# INICIO
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):

    user = request.session.get("user")

    # Si ya está autenticado → dashboard
    if user:

        return RedirectResponse(
            url="/dashboard",
            status_code=302,
        )

    # Si no está autenticado → login
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "title": BRAND,
        },
    )


# ============================================================
# LOGIN → KEYCLOAK
# ============================================================

@app.get("/login")
async def login(request: Request):

    return await oauth.keycloak.authorize_redirect(
        request,
        REDIRECT_URI,
    )


# ============================================================
# CALLBACK DE KEYCLOAK
# ============================================================

@app.get("/callback")
async def callback(request: Request):

    # --------------------------------------------------------
    # Intercambiar authorization code por tokens
    # --------------------------------------------------------

    token = await oauth.keycloak.authorize_access_token(
        request
    )

    # --------------------------------------------------------
    # Authlib obtiene la información del usuario
    # desde el ID Token OIDC
    # --------------------------------------------------------

    userinfo = token.get("userinfo")

    if not userinfo:

        # Fallback: consultar UserInfo
        userinfo = await oauth.keycloak.userinfo(
            token=token
        )

    # --------------------------------------------------------
    # Guardar usuario en la sesión
    # --------------------------------------------------------

    request.session["user"] = {
        "sub": userinfo.get("sub"),
        "preferred_username": userinfo.get(
            "preferred_username"
        ),
        "email": userinfo.get("email"),
        "given_name": userinfo.get("given_name"),
        "family_name": userinfo.get("family_name"),
    }

    # --------------------------------------------------------
    # Roles del realm + refresh_token para la Admin API
    # --------------------------------------------------------

    claims = _claims_from_token(token.get("access_token", ""))

    realm_roles = claims.get("realm_access", {}).get("roles", [])
    mgmt_roles = (
        claims.get("resource_access", {})
        .get("realm-management", {})
        .get("roles", [])
    )

    request.session["roles"] = [
        r for r in realm_roles if r in ASSIGNABLE_ROLES
    ]
    request.session["is_admin"] = (
        "admin" in realm_roles or "manage-users" in mgmt_roles
    )

    request.session["refresh_token"] = token.get(
        "refresh_token"
    )

    # --------------------------------------------------------
    # Guardar ID Token para logout OIDC
    # --------------------------------------------------------

    request.session["id_token"] = token.get(
        "id_token"
    )

    # --------------------------------------------------------
    # Ir al dashboard
    # --------------------------------------------------------

    return RedirectResponse(
        url="/dashboard",
        status_code=302,
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.get(
    "/dashboard",
    response_class=HTMLResponse
)
async def dashboard(request: Request):

    user = request.session.get("user")

    # --------------------------------------------------------
    # Si no existe sesión → volver al inicio
    # --------------------------------------------------------

    if not user:

        return RedirectResponse(
            url="/",
            status_code=302,
        )

    # --------------------------------------------------------
    # Mostrar dashboard
    # --------------------------------------------------------

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "title": f"Panel · {BRAND}",
            "user": user,
            "is_admin": is_admin(request),
            "menu": visible_menu(request),
        },
    )


# ============================================================
# SECCIÓN DE EJEMPLO — plantilla para las secciones del proyecto
# ============================================================
# Copia este patrón para cada sección nueva: una ruta protegida
# por rol + su plantilla + una entrada en MAIN_MENU.

@app.get("/proceso", response_class=HTMLResponse)
async def proceso(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=302)

    if not is_admin(request) and "operador" not in get_roles(request):
        return templates.TemplateResponse(
            request=request,
            name="forbidden.html",
            context={"request": request, "title": "Sin permiso", "user": user},
            status_code=403,
        )

    return templates.TemplateResponse(
        request=request,
        name="proceso.html",
        context={"request": request, "title": "Proceso", "user": user},
    )


# ============================================================
# ADMINISTRACIÓN DE USUARIOS
# ============================================================
# Requiere el rol de realm "admin". Las operaciones se ejecutan
# contra la Admin REST API de Keycloak con el token del propio
# usuario, así que Keycloak vuelve a validar cada permiso.

def _require_admin(request):
    """Devuelve (None, response) si no puede pasar; (user, None) si sí."""
    user = get_user(request)
    if not user:
        return None, RedirectResponse(url="/", status_code=302)
    if not is_admin(request):
        return None, templates.TemplateResponse(
            request=request,
            name="forbidden.html",
            context={"request": request, "title": "Sin permiso", "user": user},
            status_code=403,
        )
    return user, None


async def _load_users(request):
    resp = await kc_admin(
        request, "GET", "/users",
        params={"briefRepresentation": "false", "max": 200},
    )
    if resp is None or resp.status_code != 200:
        return None

    users = resp.json()

    # Roles de realm y grupos por usuario (para mostrar y editar).
    for u in users:
        r = await kc_admin(
            request, "GET", f"/users/{u['id']}/role-mappings/realm"
        )
        u["realm_roles"] = (
            sorted(
                x["name"] for x in r.json()
                if x["name"] in ASSIGNABLE_ROLES
            )
            if r is not None and r.status_code == 200
            else []
        )

        # Roles efectivos (incluye los heredados por jerarquía de roles).
        eff = await kc_admin(
            request, "GET", f"/users/{u['id']}/role-mappings/realm/composite"
        )
        u["effective_roles"] = (
            sorted(
                x["name"] for x in eff.json()
                if x["name"] in ASSIGNABLE_ROLES
            )
            if eff is not None and eff.status_code == 200
            else list(u["realm_roles"])
        )

        g = await kc_admin(request, "GET", f"/users/{u['id']}/groups")
        u["groups"] = (
            [
                {"id": x["id"], "name": x["name"], "path": x.get("path", "")}
                for x in g.json()
            ]
            if g is not None and g.status_code == 200
            else []
        )
    return users


def _pretty_path(path):
    """/Produccion/Turno A  ->  Produccion / Turno A"""
    return " / ".join(p for p in (path or "").split("/") if p)


async def _load_groups(request):
    """Árbol de grupos del realm (plano, con profundidad) y nº de miembros."""
    resp = await kc_admin(
        request, "GET", "/groups",
        params={"briefRepresentation": "false", "max": 500},
    )
    if resp is None or resp.status_code != 200:
        return None

    flat = []

    async def walk(node, depth):
        item = {
            "id": node["id"],
            "name": node["name"],
            "path": node.get("path", "/" + node["name"]),
            "pretty": _pretty_path(node.get("path", "/" + node["name"])),
            "depth": depth,
        }
        flat.append(item)

        children = node.get("subGroups")
        if children is None and node.get("subGroupCount"):
            c = await kc_admin(
                request, "GET", f"/groups/{node['id']}/children",
                params={"briefRepresentation": "false", "max": 500},
            )
            children = c.json() if c is not None and c.status_code == 200 else []

        for child in children or []:
            await walk(child, depth + 1)

    for root in resp.json():
        await walk(root, 0)

    for grp in flat:
        m = await kc_admin(
            request, "GET", f"/groups/{grp['id']}/members", params={"max": 1000},
        )
        grp["member_count"] = (
            len(m.json()) if m is not None and m.status_code == 200 else "?"
        )
    return flat


@app.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_users(request: Request, msg: str = "", err: str = "", edit: str = ""):
    user, deny = _require_admin(request)
    if deny:
        return deny

    users = await _load_users(request)
    if users is None:
        err = err or (
            "No se pudo cargar la lista de usuarios. Tu sesión pudo caducar "
            "(vuelve a iniciar sesión) o tu cuenta no tiene permisos de "
            "administración."
        )

    groups = await _load_groups(request) or []

    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "request": request,
            "title": "Usuarios",
            "user": user,
            "roles": get_roles(request),
            "users": users or [],
            "assignable_roles": ASSIGNABLE_ROLES,
            "all_groups": groups,
            "msg": msg,
            "err": err,
            "edit_id": edit,
        },
    )


@app.post("/admin/usuarios")
async def admin_users_create(
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    password: str = Form(...),
    temporary: str = Form("on"),
    role: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    body = {
        "username": username.strip(),
        "enabled": True,
        "emailVerified": False,
    }
    if email.strip():
        body["email"] = email.strip()
    if first_name.strip():
        body["firstName"] = first_name.strip()
    if last_name.strip():
        body["lastName"] = last_name.strip()

    resp = await kc_admin(request, "POST", "/users", json=body)
    if resp is None or resp.status_code not in (201, 204):
        return _redir_users(err=_kc_error(resp, "No se pudo crear el usuario"))

    location = resp.headers.get("Location", "")
    user_id = location.rstrip("/").rsplit("/", 1)[-1]

    pw = await kc_admin(
        request, "PUT", f"/users/{user_id}/reset-password",
        json={
            "type": "password",
            "value": password,
            "temporary": temporary == "on",
        },
    )
    if pw is None or pw.status_code not in (204, 200):
        return _redir_users(
            err="Usuario creado, pero no se pudo fijar la contraseña."
        )

    if role in ASSIGNABLE_ROLES:
        await _set_role(request, user_id, role, add=True)

    return _redir_users(msg=f"Usuario '{username}' creado.")


@app.post("/admin/usuarios/{user_id}/estado")
async def admin_users_toggle(
    request: Request, user_id: str,
    enabled: str = Form(...), edit: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    resp = await kc_admin(
        request, "PUT", f"/users/{user_id}",
        json={"enabled": enabled == "true"},
    )
    if resp is None or resp.status_code not in (204, 200):
        return _redir_users(err=_kc_error(resp, "No se pudo cambiar el estado"), edit=edit)
    return _redir_users(msg="Estado actualizado.", edit=edit)


@app.post("/admin/usuarios/{user_id}/perfil")
async def admin_users_profile(
    request: Request, user_id: str,
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    enabled: str = Form("true"),
    edit: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    body = {
        "firstName": first_name.strip() or None,
        "lastName": last_name.strip() or None,
        "email": email.strip() or None,
        "enabled": enabled == "true",
    }
    resp = await kc_admin(request, "PUT", f"/users/{user_id}", json=body)
    if resp is None or resp.status_code not in (204, 200):
        return _redir_users(
            err=_kc_error(resp, "No se pudieron guardar los datos"), edit=edit
        )
    return _redir_users(msg="Datos del usuario actualizados.", edit=edit)


@app.post("/admin/usuarios/{user_id}/password")
async def admin_users_password(
    request: Request, user_id: str,
    password: str = Form(...), temporary: str = Form("on"),
    edit: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    resp = await kc_admin(
        request, "PUT", f"/users/{user_id}/reset-password",
        json={"type": "password", "value": password, "temporary": temporary == "on"},
    )
    if resp is None or resp.status_code not in (204, 200):
        return _redir_users(
            err=_kc_error(resp, "No se pudo cambiar la contraseña"), edit=edit
        )
    return _redir_users(msg="Contraseña actualizada.", edit=edit)


@app.post("/admin/usuarios/{user_id}/roles")
async def admin_users_roles(
    request: Request, user_id: str,
    role: str = Form(...), action: str = Form(...),
    edit: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    if role not in ASSIGNABLE_ROLES:
        return _redir_users(err="Ese rol no está permitido.", edit=edit)

    ok = await _set_role(request, user_id, role, add=(action == "add"))
    if not ok:
        return _redir_users(err="No se pudo actualizar el rol.", edit=edit)
    return _redir_users(msg="Roles actualizados.", edit=edit)


@app.post("/admin/usuarios/{user_id}/eliminar")
async def admin_users_delete(request: Request, user_id: str):
    _, deny = _require_admin(request)
    if deny:
        return deny

    resp = await kc_admin(request, "DELETE", f"/users/{user_id}")
    if resp is None or resp.status_code not in (204, 200):
        return _redir_users(err=_kc_error(resp, "No se pudo eliminar el usuario"))
    return _redir_users(msg="Usuario eliminado.")


async def _set_role(request, user_id, role_name, add):
    # Se resuelve el rol desde los mapeos del propio usuario (permiso
    # manage-users), evitando GET /roles/{name} que exige view-realm.
    src = "/available" if add else ""
    listing = await kc_admin(
        request, "GET", f"/users/{user_id}/role-mappings/realm{src}"
    )
    if listing is None or listing.status_code != 200:
        return False

    role = next(
        (x for x in listing.json() if x.get("name") == role_name), None
    )
    if role is None:
        # add: ya lo tiene · remove: no lo tenía → nada que hacer
        return True

    method = "POST" if add else "DELETE"
    resp = await kc_admin(
        request, method, f"/users/{user_id}/role-mappings/realm", json=[role]
    )
    return resp is not None and resp.status_code in (204, 200)


def _kc_error(resp, fallback):
    """Mensaje en lenguaje llano. Nunca expone el texto crudo del proveedor
    de identidad."""
    if resp is None:
        return (
            fallback
            + ". Es posible que tu sesión haya caducado: vuelve a iniciar sesión."
        )
    if resp.status_code in (401, 403):
        return "No tienes permiso para realizar esta acción."
    if resp.status_code == 409:
        return "Ya existe un registro con esos datos."
    if resp.status_code == 404:
        return "El registro ya no existe."
    return fallback + "."


def _redir_users(msg="", err="", edit=""):
    query = {"msg": msg, "err": err}
    if edit:
        query["edit"] = edit
    return RedirectResponse(
        url="/admin/usuarios?" + urlencode(query),
        status_code=303,
    )


def _redir_groups(msg="", err=""):
    return RedirectResponse(
        url="/admin/grupos?" + urlencode({"msg": msg, "err": err}),
        status_code=303,
    )


# ============================================================
# GESTIÓN DE GRUPOS
# ============================================================
# Listar grupos y la pertenencia de usuarios solo necesita
# query-groups + manage-users. Crear/eliminar grupo exige
# manage-realm (ver README): miembro del grupo platform-admins.

_NEED_MANAGE_REALM = (
    "No tienes permisos para crear o eliminar grupos. "
    "Pide a un administrador que te los habilite."
)


@app.get("/admin/grupos", response_class=HTMLResponse)
async def admin_groups(request: Request, msg: str = "", err: str = ""):
    user, deny = _require_admin(request)
    if deny:
        return deny

    groups = await _load_groups(request)
    if groups is None:
        err = err or (
            "No se pudo cargar la lista de grupos. Tu sesión pudo caducar "
            "o tu cuenta no tiene permisos suficientes."
        )

    return templates.TemplateResponse(
        request=request,
        name="admin_groups.html",
        context={
            "request": request,
            "title": "Grupos",
            "user": user,
            "groups": groups or [],
            "msg": msg,
            "err": err,
        },
    )


@app.post("/admin/grupos")
async def admin_groups_create(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    name = name.strip()
    if not name:
        return _redir_groups(err="El nombre del grupo no puede estar vacío.")

    if parent_id:
        path = f"/groups/{parent_id}/children"
    else:
        path = "/groups"

    resp = await kc_admin(request, "POST", path, json={"name": name})
    if resp is not None and resp.status_code == 403:
        return _redir_groups(err=_NEED_MANAGE_REALM)
    if resp is None or resp.status_code not in (201, 204):
        return _redir_groups(err=_kc_error(resp, "No se pudo crear el grupo"))
    return _redir_groups(
        msg=f"Grupo '{name}' creado" + (" como subgrupo." if parent_id else ".")
    )


@app.post("/admin/grupos/{group_id}/eliminar")
async def admin_groups_delete(request: Request, group_id: str):
    _, deny = _require_admin(request)
    if deny:
        return deny

    resp = await kc_admin(request, "DELETE", f"/groups/{group_id}")
    if resp is not None and resp.status_code == 403:
        return _redir_groups(err=_NEED_MANAGE_REALM)
    if resp is None or resp.status_code not in (204, 200):
        return _redir_groups(err=_kc_error(resp, "No se pudo eliminar el grupo"))
    return _redir_groups(msg="Grupo eliminado.")


@app.post("/admin/usuarios/{user_id}/grupos")
async def admin_users_groups(
    request: Request, user_id: str,
    group_id: str = Form(...), action: str = Form(...),
    edit: str = Form(""),
):
    _, deny = _require_admin(request)
    if deny:
        return deny

    method = "PUT" if action == "add" else "DELETE"
    resp = await kc_admin(
        request, method, f"/users/{user_id}/groups/{group_id}"
    )
    if resp is None or resp.status_code not in (204, 200):
        return _redir_users(
            err=_kc_error(resp, "No se pudo actualizar el grupo"), edit=edit
        )
    return _redir_users(msg="Grupos del usuario actualizados.", edit=edit)


# ============================================================
# LOGOUT
# ============================================================

@app.get("/logout")
async def logout(request: Request):

    # --------------------------------------------------------
    # Recuperar ID Token ANTES de borrar la sesión
    # --------------------------------------------------------

    id_token = request.session.get(
        "id_token"
    )

    # --------------------------------------------------------
    # Cerrar sesión local de DEMACYA
    # --------------------------------------------------------

    request.session.clear()

    # --------------------------------------------------------
    # Página a la que Keycloak devolverá al usuario
    # --------------------------------------------------------

    post_logout_redirect_uri = APP_BASE_URL.rstrip("/") + "/"

    # --------------------------------------------------------
    # Parámetros del logout OIDC
    # --------------------------------------------------------

    params = {
        "client_id": CLIENT_ID,
        "post_logout_redirect_uri": (
            post_logout_redirect_uri
        ),
    }

    # --------------------------------------------------------
    # ID Token Hint
    # --------------------------------------------------------

    if id_token:

        params["id_token_hint"] = id_token

    # --------------------------------------------------------
    # Construir URL de logout de Keycloak
    # --------------------------------------------------------

    logout_url = (
        f"{KEYCLOAK_BROWSER_URL}"
        f"/realms/{KEYCLOAK_REALM}"
        f"/protocol/openid-connect/logout"
        f"?{urlencode(params)}"
    )

    # --------------------------------------------------------
    # Redirigir a Keycloak
    # --------------------------------------------------------

    return RedirectResponse(
        url=logout_url,
        status_code=302,
    )