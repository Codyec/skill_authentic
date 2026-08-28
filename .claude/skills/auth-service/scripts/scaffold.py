#!/usr/bin/env python3
"""
Genera un proyecto nuevo a partir de la skill `auth-service`.

Uso:
    python scaffold.py --slug acme --dir ./acme-platform \
        [--realm ACME] [--brand "ACME S.A."] \
        [--web-port 8000] [--kc-port 8081] [--logo ./logo.png]

Copia templates/ al destino sustituyendo los marcadores, genera .env con
secretos aleatorios y renombra la carpeta del tema de login.
"""
import argparse
import re
import secrets
import shutil
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
BINARY_EXT = {".png", ".ico", ".jpg", ".jpeg", ".gif", ".woff", ".woff2"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")


def rand():
    return secrets.token_urlsafe(24)


def substitute(text, repl):
    for key, value in repl.items():
        text = text.replace(key, value)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True,
                    help="identificador en minúsculas, p. ej. acme")
    ap.add_argument("--dir", required=True, help="carpeta destino (se crea)")
    ap.add_argument("--realm", help="nombre del realm (def. SLUG en mayúsculas)")
    ap.add_argument("--brand", help="nombre visible (def. SLUG capitalizado)")
    ap.add_argument("--web-port", type=int, default=8000)
    ap.add_argument("--kc-port", type=int, default=8081)
    ap.add_argument("--logo", help="PNG de marca (opcional)")
    args = ap.parse_args()

    slug = args.slug.strip().lower()
    if not SLUG_RE.match(slug):
        sys.exit("El slug debe casar con ^[a-z][a-z0-9-]{2,30}$")

    realm = (args.realm or slug.upper()).strip()
    brand = (args.brand or slug.replace("-", " ").title()).strip()
    dest = Path(args.dir).resolve()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} ya existe y no está vacío.")

    repl = {
        "__SLUG__": slug,
        "__REALM__": realm,
        "__BRAND__": brand,
        "__WEB_PORT__": str(args.web_port),
        "__KC_PORT__": str(args.kc_port),
    }

    for src in sorted(TEMPLATES.rglob("*")):
        rel = src.relative_to(TEMPLATES)
        rel_out = Path(substitute(str(rel), repl))
        out = dest / rel_out

        if src.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue

        out.parent.mkdir(parents=True, exist_ok=True)

        if src.name == ".env.example":
            example = substitute(src.read_text(encoding="utf-8"), repl)
            out.write_text(example.replace("__RANDOM__", "cambia-esto"),
                           encoding="utf-8")
            env = re.sub(r"__RANDOM__", lambda _m: rand(), example)
            (dest / ".env").write_text(env, encoding="utf-8")
            continue

        if src.suffix.lower() in BINARY_EXT:
            if args.logo and src.name == "logo.png":
                shutil.copyfile(args.logo, out)
            else:
                shutil.copyfile(src, out)
            continue

        out.write_text(
            substitute(src.read_text(encoding="utf-8"), repl),
            encoding="utf-8",
        )

    env_lines = dict(
        line.split("=", 1)
        for line in (dest / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )

    print(f"""
  Proyecto '{brand}' creado en: {dest}

  Siguiente:
    cd {dest}
    docker compose up -d          # espera ~20 s a que arranque la identidad

  App:               http://localhost:{args.web_port}
  Consola identidad: http://localhost:{args.kc_port}
    usuario:     {env_lines.get('KEYCLOAK_ADMIN_USERNAME', 'admin').strip()}
    contraseña:  {env_lines.get('KEYCLOAK_ADMIN_PASSWORD', '').strip()}
    (guárdala: está en {dest / '.env'})

  Primer acceso: en la consola de identidad, realm {realm} ->
    Users -> Add user (con contraseña) -> Groups -> platform-admins -> añadirlo.
""")


if __name__ == "__main__":
    main()
