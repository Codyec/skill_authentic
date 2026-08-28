#!/usr/bin/env python3
"""
Re-sincroniza los templates de la skill `auth-service` desde el monorepo.
El repo (platform/web-shell + infra/keycloak/themes/demacya) es la copia
maestra; cuando mejora, ejecuta esto para llevar los cambios a la skill.

    python .claude/skills/auth-service/scripts/update-templates.py
"""
import shutil
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parents[2]
WS_SRC = REPO / "platform" / "web-shell"
TH_SRC = REPO / "infra" / "keycloak" / "themes" / "demacya" / "login"

if not WS_SRC.exists():
    sys.exit("No encuentro platform/web-shell — ejecútalo dentro del repo camaron/.")


def sync_tree(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


sync_tree(WS_SRC / "app", SKILL / "templates" / "web-shell" / "app")
for f in ("Dockerfile", "requirements.txt"):
    shutil.copyfile(WS_SRC / f, SKILL / "templates" / "web-shell" / f)

th_dst = SKILL / "templates" / "keycloak" / "themes" / "__SLUG__" / "login"
shutil.copyfile(TH_SRC / "theme.properties", th_dst / "theme.properties")
shutil.copyfile(TH_SRC / "resources" / "css" / "theme.css",
                th_dst / "resources" / "css" / "theme.css")
shutil.copyfile(TH_SRC / "resources" / "img" / "logo.png",
                th_dst / "resources" / "img" / "logo.png")

# Comprobación: en los templates de la app no deben quedar literales de marca.
leaks = []
for p in (SKILL / "templates" / "web-shell" / "app").rglob("*"):
    if p.suffix in (".py", ".html", ".css"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "DEMACYA" in t or "Logo_DEMA" in t:
            leaks.append(p.relative_to(SKILL))

print("Templates de la skill actualizados desde el repo.")
if leaks:
    print("AVISO — literales de marca sin genericizar en:")
    for p in leaks:
        print("  ", p)
