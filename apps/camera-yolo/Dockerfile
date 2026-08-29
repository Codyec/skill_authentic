# camera-yolo — cámara MindVision GigE + YOLO26 + panel de configuración
#
# Contexto de build: la RAÍZ del repo (necesita vendor/mindvision-sdk).
#   docker build -f apps/camera-yolo/Dockerfile -t camera-yolo .
#
# Base: familia oficial de Ultralytics (trae torch, opencv, numpy, ultralytics).
#   GPU NVIDIA : ultralytics/ultralytics:latest           (por defecto)
#   Solo CPU   : ultralytics/ultralytics:latest-cpu
#   Raspberry 5: ultralytics/ultralytics:latest-arm64
#   Jetson     : ultralytics/ultralytics:latest-jetson-jetpack6
#
#   docker build -f apps/camera-yolo/Dockerfile \
#     --build-arg BASE_IMAGE=ultralytics/ultralytics:latest-cpu -t camera-yolo:cpu .
#
# LICENCIA: la imagen incluye libMVSDK.so (SDK propietario MindVision).
# No publicar esta imagen en un registro público. Build local únicamente.

ARG BASE_IMAGE=ultralytics/ultralytics:latest
FROM ${BASE_IMAGE}

# --- SDK MindVision (Linux): .so de la arquitectura de destino + headers ---
COPY vendor/mindvision-sdk/linux/include/ /usr/include/
COPY vendor/mindvision-sdk/linux/lib/ /opt/mvsdk-lib/
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) src=/opt/mvsdk-lib/x64/libMVSDK.so ;; \
      arm64) src=/opt/mvsdk-lib/arm64/libMVSDK.so ;; \
      *) echo "Arquitectura no soportada: $arch" >&2; exit 1 ;; \
    esac; \
    cp "$src" /lib/libMVSDK.so; \
    ldconfig; \
    rm -rf /opt/mvsdk-lib

RUN pip install --no-cache-dir psutil

# Wrapper de referencia del monorepo (más completo que el del SDK)
COPY services/camera-service/legacy/mvsdk.py /opt/camera-yolo/mvsdk.py
COPY apps/camera-yolo/ /opt/camera-yolo/

WORKDIR /opt/camera-yolo

ENV PYTHONPATH=/opt/camera-yolo \
    PYTHONUNBUFFERED=1 \
    MODEL_PATH=/models/yolo26x.pt \
    CAMERA_CONFIG_PATH=/config/camera_config.json \
    YOLO_DEVICE=cpu \
    WEB_SHELL_URL=http://localhost:8000/dashboard \
    MVSDK_PATH=

EXPOSE 8090

CMD ["python", "main.py"]
