import os
from urllib.parse import urlencode

from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from authlib.integrations.starlette_client import OAuth


# ============================================================
# VARIABLES DE ENTORNO
# ============================================================

load_dotenv()


# ============================================================
# APP
# ============================================================

app = FastAPI(title="DEMACYA")


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


# ============================================================
# SESIONES
# ============================================================

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="demacya_session",
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
            "title": "DEMACYA",
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
            "title": "Dashboard DEMACYA",
            "user": user,
        },
    )


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

    post_logout_redirect_uri = (
        "http://localhost:8000/"
    )

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