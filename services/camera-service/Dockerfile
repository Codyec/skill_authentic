# =============================================================================
# PLANTILLA — NO FUNCIONAL TODAVÍA
# =============================================================================
# Contenerizar el servicio de cámara requiere validación contra hardware real.
# Este archivo documenta la forma esperada; no construir hasta la Fase 4
# (ver ../../docs/roadmap-skill.md).
#
# Requisitos que este Dockerfile asume:
#   - host Linux (Docker Desktop en Windows NO pasa la NIC de la cámara)
#   - la cámara es GigE  ->  el contenedor se lanza con network_mode: host
#   - libMVSDK.so de la arquitectura del host (x64 o arm64)
# =============================================================================

FROM python:3.11-slim

# --- SDK nativo del fabricante -----------------------------------------------
# Copiar la .so de la arquitectura destino. Para multi-arch usar
# TARGETARCH y elegir vendor/mindvision-sdk/linux/lib/{x64,arm64}.
ARG SDK_ARCH=x64
COPY vendor/mindvision-sdk/linux/lib/${SDK_ARCH}/libMVSDK.so /lib/libMVSDK.so
COPY vendor/mindvision-sdk/linux/include/*.h /usr/include/

# --- dependencias de sistema para opencv-headless ---------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/camera-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Mientras no exista src/, se ejecuta el legacy.
COPY services/camera-service/legacy/ ./legacy/

EXPOSE 8010
CMD ["python", "legacy/camera_service_FUNCIONAL_FINAL.py"]
