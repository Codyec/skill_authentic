---
description: Generar un servicio nuevo con autenticación (login + panel de usuarios/grupos)
---

Invoca la skill `auth-service` (herramienta Skill, `skill: "auth-service"`) y sigue
sus pasos para generar un proyecto autónomo con autenticación (Postgres + Keycloak +
web-shell FastAPI, un solo `docker compose`).

Antes de ejecutar el scaffold, pregunta al usuario lo que falte: slug del proyecto
(minúsculas), nombre de marca, carpeta destino, y puertos si 8000/8081 están ocupados.

Si el usuario pasó argumentos, tómalos como pistas (slug y/o marca): $ARGUMENTS
