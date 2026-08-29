import os
import sys
import time
import json
import threading
import subprocess

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np
import torch
from ultralytics import YOLO

import camera_props


# ============================================================
# MVSDK
# ============================================================
# El wrapper mvsdk.py carga la biblioteca nativa al importarse
# (MVCAMSDK_X64.dll en Windows / libMVSDK.so en Linux). Se busca:
#   1. donde ya esté en el PYTHONPATH (imagen Docker: /opt/camera-yolo)
#   2. MVSDK_PATH (instalación local en Windows)
#   3. el wrapper de referencia del monorepo (services/camera-service/legacy)

MVSDK_PATH = os.getenv(
    "MVSDK_PATH",
    r"C:\Users\dmore\OneDrive\Backup\SOFTWARE\VISION CHINA"
    r"\USB SDK\USB Drive\Demo\Demo\Python\python_demo",
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_MVSDK = os.path.normpath(
    os.path.join(_THIS_DIR, "..", "..", "services", "camera-service", "legacy")
)

for _p in (MVSDK_PATH, _LEGACY_MVSDK):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

import mvsdk


# ============================================================
# PARCHE: CameraSetFrameRate / CameraGetFrameRate
# ============================================================
# Esta versión de mvsdk.py (Windows) no trae estas dos funciones,
# pero SÍ existen en la DLL nativa — confirmado en CameraApi.h del
# SDK de Linux del mismo fabricante:
#
#   CameraSetFrameRate(hCamera, RateHZ)
#   "Set the frame frequency (area) or line frequency (line scan).
#    (only supported by some gige camera)"
#   RateHZ: frame rate or line rate (<=0 means maximum frequency)
#
# Es distinta de CameraSetFrameSpeed (esa es el preset Low/Normal/
# High/Super, ya la usamos más abajo). Como es una función C
# estándar (extern "C", CameraHandle es un int simple), la llamamos
# directo por ctypes reusando el mismo handle de DLL que mvsdk.py
# ya cargó internamente en mvsdk._sdk — mismo patrón que usa el
# propio mvsdk.py para todo lo demás.

def camera_set_frame_rate(h_camera, rate_hz):

    err_code = mvsdk._sdk.CameraSetFrameRate(
        h_camera,
        rate_hz
    )

    mvsdk.SetLastError(err_code)

    return err_code


def camera_get_frame_rate(h_camera):

    rate_hz = mvsdk.c_int()

    err_code = mvsdk._sdk.CameraGetFrameRate(
        h_camera,
        mvsdk.byref(rate_hz)
    )

    mvsdk.SetLastError(err_code)

    return rate_hz.value


# ============================================================
# CONFIGURACIÓN
# ============================================================

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8090"))

MODEL_PATH = os.getenv("MODEL_PATH", r"C:\Python\YOLO\yolo26x.pt")

DEVICE = os.getenv("YOLO_DEVICE", "cuda:0")

# Solo informativo / para logs: la app abre devices[0]. El servicio
# de configuración lo guarda en camera_config.json.
CAMERA_IP = os.getenv("CAMERA_IP", "192.168.0.216")

# camera_config.json = fuente de verdad de las propiedades de la cámara.
# Nativo: apps/camera-yolo/config/camera_config.json (versionado como semilla).
# Docker: /config/camera_config.json (volumen ./config).
MENU_URL = os.getenv("WEB_SHELL_URL", "http://localhost:8000/dashboard")

CAMERA_CONFIG_PATH = os.getenv(
    "CAMERA_CONFIG_PATH",
    os.path.join(_THIS_DIR, "config", "camera_config.json"),
)

# Cámara
WIDTH = 1280
HEIGHT = 1024

FRAME_SPEED = 2
DEFAULT_EXPOSURE_US = 2000

# YOLO
DEFAULT_IMGSZ = 640
DEFAULT_CONF = 0.25
DEFAULT_IOU = 0.45
DEFAULT_MAX_DET = 100

# Web
WEB_WIDTH = 960
WEB_HEIGHT = 768
WEB_FPS_TARGET = 20
JPEG_QUALITY = 72


# ============================================================
# ESTADO GLOBAL
# ============================================================

running = True

camera_ready = False
model_ready = False

latest_frame = None
latest_capture_time = 0.0

latest_detections = []
latest_detections_time = 0.0

latest_jpeg = None
latest_jpeg_time = 0.0
latest_sequence = 0

frame_lock = threading.Lock()
detections_lock = threading.Lock()
jpeg_lock = threading.Lock()

# Se dispara cada vez que camera_loop() publica un frame nuevo.
# web_loop() espera en este evento, NO en el resultado de YOLO:
# así el video se refresca al ritmo de la cámara, no al de la inferencia.
frame_ready = threading.Event()

config_lock = threading.Lock()


# ============================================================
# CONTROLES DINÁMICOS
# ============================================================

exposure_us = DEFAULT_EXPOSURE_US

yolo_enabled = True

confidence = DEFAULT_CONF
iou_threshold = DEFAULT_IOU
imgsz = DEFAULT_IMGSZ
max_det = DEFAULT_MAX_DET

show_boxes = True
show_labels = False

flip_vertical = False
mirror_horizontal = False

# Estimar la posición del cuadro en los frames donde no hubo
# inferencia nueva, en vez de dejarlo clavado en la última posición.
predict_motion = True


# ============================================================
# PROPIEDADES DE CÁMARA (panel de configuración)
# ============================================================
# camera_config.json es la fuente de verdad. camera_loop() aplica
# `desired_properties` a la cámara (único hilo que toca el SDK) y
# vuelve a persistir el archivo con los valores efectivos.

camera_handle = 0

# {id: valor objetivo}. Lo puebla config_load() al arrancar y
# /control/apply cuando el usuario edita en el panel.
desired_properties = {}

# camera_loop() atiende el lote pendiente cuando esto es True.
properties_dirty = False

# POST /control/save_config: camera_loop() relee la cámara y guarda.
save_requested = False

# True cuando se aplicaron cambios a la cámara que aún NO están en
# camera_config.json (el archivo que es la fuente de verdad).
unsaved_changes = False

# El próximo lote a aplicar viene del archivo (reload) -> no marca "sin guardar".
_apply_from_file = False

# {id: {"set_ok", "verified", "message"}} — último resultado por propiedad.
properties_status = {}

# Texto del último guardado a disco (para mostrar en el panel).
properties_last_save = ""

props_lock = threading.Lock()


# ============================================================
# MÉTRICAS
# ============================================================

camera_fps = 0.0
capture_ms = 0.0

# Frames/seg que camera_loop() descarta porque el driver
# ya tenía más de uno esperando (backlog). Si esto es
# consistentemente > 0, confirma que hay una cola interna
# atrasándose respecto de la cámara real.
drain_fps = 0.0

yolo_fps = 0.0
yolo_ms = 0.0

web_fps = 0.0

total_frames = 0
total_inferences = 0

detections_count = 0

frame_age_ms = 0.0
detection_age_ms = 0.0

gpu_memory_used_mb = 0.0
gpu_memory_total_mb = 0.0
gpu_memory_percent = 0.0

gpu_utilization = 0.0

cpu_percent = 0.0

ram_used_mb = 0.0
ram_total_mb = 0.0
ram_percent = 0.0

current_exposure_us = DEFAULT_EXPOSURE_US

camera_resolution = "N/A"

model_name = "YOLO26-X"

last_error = ""


# ============================================================
# GPU / SISTEMA
# ============================================================

def update_system_metrics():

    global gpu_memory_used_mb
    global gpu_memory_total_mb
    global gpu_memory_percent
    global gpu_utilization
    global cpu_percent
    global ram_used_mb
    global ram_total_mb
    global ram_percent

    # --------------------------------------------------------
    # CPU / RAM
    # --------------------------------------------------------

    try:

        import psutil

        cpu_percent = psutil.cpu_percent(
            interval=None
        )

        memory = psutil.virtual_memory()

        ram_used_mb = (
            memory.used / 1024 / 1024
        )

        ram_total_mb = (
            memory.total / 1024 / 1024
        )

        ram_percent = memory.percent

    except Exception:

        pass

    # --------------------------------------------------------
    # GPU MEMORIA
    # --------------------------------------------------------

    if torch.cuda.is_available():

        try:

            device_index = 0

            used = torch.cuda.memory_allocated(
                device_index
            )

            reserved = torch.cuda.memory_reserved(
                device_index
            )

            total = torch.cuda.get_device_properties(
                device_index
            ).total_memory

            # CUDA memory del proceso
            gpu_memory_used_mb = (
                max(used, reserved)
                / 1024
                / 1024
            )

            gpu_memory_total_mb = (
                total
                / 1024
                / 1024
            )

            gpu_memory_percent = (
                gpu_memory_used_mb
                / gpu_memory_total_mb
                * 100.0
                if gpu_memory_total_mb > 0
                else 0.0
            )

        except Exception:

            pass

    # --------------------------------------------------------
    # GPU UTILIZACIÓN
    # --------------------------------------------------------

    try:

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits"
            ],
            capture_output=True,
            text=True,
            timeout=1
        )

        if result.returncode == 0:

            value = result.stdout.strip()

            if value:

                gpu_utilization = float(
                    value.splitlines()[0]
                )

    except Exception:

        pass


# ============================================================
# EXPOSICIÓN
# ============================================================

def get_exposure():

    with config_lock:

        return exposure_us


def set_exposure(value):

    global exposure_us

    value = int(value)

    value = max(
        1,
        min(
            131071,
            value
        )
    )

    with config_lock:

        exposure_us = value

    return value


# ============================================================
# PROPIEDADES DE CÁMARA — helpers
# ============================================================

def _native_fr_get():
    """Adaptador para camera_props: frecuencia de adquisición nativa."""
    try:
        return True, camera_get_frame_rate(camera_handle)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _native_fr_set(value):
    try:
        rc = camera_set_frame_rate(camera_handle, int(float(value)))
        return rc in (None, 0), f"CameraSetFrameRate -> {rc}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def queue_properties(changes):
    """Mezcla cambios pedidos por el panel en `desired_properties`."""
    global properties_dirty

    valid = camera_props.definitions_by_id()
    accepted = []

    with props_lock:
        for pid, value in changes.items():
            if pid not in valid:
                continue
            desired_properties[pid] = value
            accepted.append(pid)

        if accepted:
            properties_dirty = True

    # La exposición tiene su propio control (slider) y su propia
    # ruta de aplicación en camera_loop(): mantenemos una sola fuente.
    if "exposure_us" in changes:
        try:
            set_exposure(int(float(changes["exposure_us"])))
        except (TypeError, ValueError):
            pass

    return accepted


def props_snapshot():
    """Estado para GET /api/props (no toca la cámara)."""
    with props_lock:
        return {
            "definitions": camera_props.property_definitions(),
            "values": dict(desired_properties),
            "status": dict(properties_status),
            "camera_ip": CAMERA_IP,
            "config_path": CAMERA_CONFIG_PATH,
            "last_save": properties_last_save,
            "dirty": properties_dirty,
            "unsaved": unsaved_changes,
        }


def _save_config_now(h_camera):
    """camera_loop() -> persiste camera_config.json desde la intención actual."""
    global properties_last_save, unsaved_changes

    with props_lock:
        overrides = dict(desired_properties)

    save_ok, save_result = camera_props.config_save(
        CAMERA_CONFIG_PATH, CAMERA_IP, mvsdk, h_camera,
        native_get=_native_fr_get, overrides=overrides,
    )
    with props_lock:
        properties_last_save = (
            f"{time.strftime('%H:%M:%S')} — "
            + ("guardado OK" if save_ok else "guardado con avisos")
        )
        if save_ok:
            unsaved_changes = False
    print(f"[PROPS] Guardado manual ok={save_ok} en {save_result.get('path')}")


def _apply_pending_properties(h_camera):
    """
    Lo llama camera_loop() (único hilo con acceso al SDK) cuando hay un
    lote pendiente. Escribe a la cámara y re-persiste camera_config.json.
    """
    global properties_dirty, properties_status, save_requested
    global unsaved_changes, _apply_from_file

    with props_lock:
        if save_requested and not properties_dirty:
            save_requested = False
            do_save_only = True
        else:
            do_save_only = False

    if do_save_only:
        _save_config_now(h_camera)
        return

    with props_lock:
        if not properties_dirty:
            return
        changes = dict(desired_properties)
        properties_dirty = False
        save_requested = False
        from_file = _apply_from_file
        _apply_from_file = False

    all_ok, status = camera_props.apply_batch(
        mvsdk,
        h_camera,
        changes,
        native_get=_native_fr_get,
        native_set=_native_fr_set,
    )

    with props_lock:
        properties_status = status
        # Aplicar cambia la cámara EN VIVO (previsualización). El archivo
        # camera_config.json solo se toca al pulsar "Guardar" — así el
        # usuario confirma explícitamente la config que servirá para todo.
        if not from_file:
            unsaved_changes = True

    if from_file:
        # Recarga desde archivo: la cámara ya coincide con el archivo.
        _save_config_now(h_camera)

    print(
        f"[PROPS] Lote aplicado ({len(changes)} props, ok={all_ok}, "
        f"desde_archivo={from_file})"
    )


def _bootstrap_properties(h_camera):
    """
    Al arrancar: si existe camera_config.json lo aplica a la cámara
    (fuente de verdad). Si no existe, crea la semilla desde la cámara.
    """
    global properties_dirty, properties_status, properties_last_save, unsaved_changes

    saved = camera_props.config_load(CAMERA_CONFIG_PATH)

    if saved:
        with props_lock:
            desired_properties.clear()
            desired_properties.update(saved)

        if "exposure_us" in saved:
            try:
                set_exposure(int(float(saved["exposure_us"])))
            except (TypeError, ValueError):
                pass

        all_ok, status = camera_props.apply_batch(
            mvsdk,
            h_camera,
            saved,
            native_get=_native_fr_get,
            native_set=_native_fr_set,
        )
        with props_lock:
            properties_status = status
        print(
            f"[PROPS] camera_config.json aplicado al arranque "
            f"({len(saved)} props, ok={all_ok})"
        )
    else:
        with props_lock:
            snap = camera_props.read_all(
                mvsdk, h_camera, native_get=_native_fr_get
            )
            desired_properties.clear()
            for pid, entry in snap.items():
                if entry.get("ok"):
                    desired_properties[pid] = entry.get("value")

    with props_lock:
        overrides = dict(desired_properties)

    save_ok, save_result = camera_props.config_save(
        CAMERA_CONFIG_PATH,
        CAMERA_IP,
        mvsdk,
        h_camera,
        native_get=_native_fr_get,
        overrides=overrides,
    )
    with props_lock:
        properties_last_save = (
            f"{time.strftime('%H:%M:%S')} — "
            + ("guardado OK" if save_ok else "guardado con avisos")
        )
        unsaved_changes = False
    print(
        f"[PROPS] Semilla/estado guardado en {save_result.get('path')} "
        f"(ok={save_ok})"
    )


def reload_properties_from_file():
    """POST /control/reload_config — vuelve a leer el archivo y lo encola."""
    global properties_dirty, _apply_from_file

    saved = camera_props.config_load(CAMERA_CONFIG_PATH)
    if saved is None:
        return False, "No hay archivo de configuración que recargar"

    with props_lock:
        desired_properties.clear()
        desired_properties.update(saved)
        properties_dirty = True
        _apply_from_file = True

    if "exposure_us" in saved:
        try:
            set_exposure(int(float(saved["exposure_us"])))
        except (TypeError, ValueError):
            pass

    return True, f"{len(saved)} propiedades recargadas del archivo"


# ============================================================
# ORIENTACIÓN (VOLTEAR / ESPEJO)
# ============================================================

def get_orientation():

    with config_lock:

        return (
            flip_vertical,
            mirror_horizontal
        )


def apply_orientation(frame):

    vertical, horizontal = get_orientation()

    if vertical and horizontal:

        return cv2.flip(
            frame,
            -1
        )

    if vertical:

        return cv2.flip(
            frame,
            0
        )

    if horizontal:

        return cv2.flip(
            frame,
            1
        )

    return frame


# ============================================================
# OBTENER CONFIG YOLO
# ============================================================

def get_yolo_config():

    with config_lock:

        return (
            yolo_enabled,
            confidence,
            iou_threshold,
            imgsz,
            max_det,
            show_boxes,
            show_labels
        )


# ============================================================
# EXTRAER DETECCIONES (formato plano, sin depender de ultralytics)
# ============================================================

def extract_plain_detections(result):

    if (
        result is None
        or result.boxes is None
    ):
        return []

    boxes = result.boxes

    if len(boxes) == 0:
        return []

    xyxy = (
        boxes.xyxy
        .detach()
        .cpu()
        .numpy()
    )

    confs = (
        boxes.conf
        .detach()
        .cpu()
        .numpy()
    )

    classes = (
        boxes.cls
        .detach()
        .cpu()
        .numpy()
    )

    names = result.names

    detections = []

    for box, conf, cls in zip(
        xyxy,
        confs,
        classes
    ):

        class_id = int(cls)

        if isinstance(names, dict):

            class_name = names.get(
                class_id,
                str(class_id)
            )

        else:

            class_name = names[
                class_id
            ]

        detections.append({
            "xyxy": (
                float(box[0]),
                float(box[1]),
                float(box[2]),
                float(box[3])
            ),
            "conf": float(conf),
            "cls": class_id,
            "name": class_name
        })

    return detections


# ============================================================
# ESTIMACIÓN DE MOVIMIENTO (VELOCIDAD ENTRE DETECCIONES)
# ============================================================

# Solo los usa yolo_loop(), un único hilo escritor: no hace
# falta lock para este estado.
_previous_detections = []
_previous_detections_time = 0.0

# Si el emparejamiento con la detección anterior salta esta
# distancia (relativa al tamaño de la caja), se considera un
# objeto distinto y no se le asigna velocidad.
MATCH_DISTANCE_FACTOR = 1.5

# No proyectar más allá de esto. Si YOLO se atrasa más que esto,
# preferimos clavar la caja en su última posición conocida antes
# que dejarla "volar" por la pantalla.
MAX_EXTRAPOLATION_S = 0.35


def box_center(xyxy):

    x1, y1, x2, y2 = xyxy

    return (
        (x1 + x2) / 2.0,
        (y1 + y2) / 2.0
    )


def estimate_velocities(
    current_detections,
    current_time
):

    global _previous_detections
    global _previous_detections_time

    dt = (
        current_time
        - _previous_detections_time
    )

    no_history = (
        dt <= 0.0
        or dt > 1.0
        or not _previous_detections
    )

    if no_history:

        for det in current_detections:

            det["velocity"] = (0.0, 0.0)

    else:

        used = set()

        for det in current_detections:

            cx, cy = box_center(
                det["xyxy"]
            )

            best_index = None
            best_dist = None

            for i, prev in enumerate(
                _previous_detections
            ):

                if i in used:
                    continue

                if prev["cls"] != det["cls"]:
                    continue

                pcx, pcy = box_center(
                    prev["xyxy"]
                )

                dist = (
                    (cx - pcx) ** 2
                    + (cy - pcy) ** 2
                ) ** 0.5

                if (
                    best_dist is None
                    or dist < best_dist
                ):
                    best_dist = dist
                    best_index = i

            width = (
                det["xyxy"][2]
                - det["xyxy"][0]
            )

            height = (
                det["xyxy"][3]
                - det["xyxy"][1]
            )

            max_match_dist = (
                max(width, height)
                * MATCH_DISTANCE_FACTOR
            )

            matched = (
                best_index is not None
                and best_dist is not None
                and best_dist <= max_match_dist
            )

            if matched:

                used.add(best_index)

                pcx, pcy = box_center(
                    _previous_detections[
                        best_index
                    ]["xyxy"]
                )

                det["velocity"] = (
                    (cx - pcx) / dt,
                    (cy - pcy) / dt
                )

            else:

                det["velocity"] = (0.0, 0.0)

    _previous_detections = current_detections
    _previous_detections_time = current_time


def project_detections(
    detections,
    detections_time,
    target_time
):

    if not detections:
        return []

    dt = target_time - detections_time

    if dt <= 0.0:
        return detections

    dt = min(
        dt,
        MAX_EXTRAPOLATION_S
    )

    projected = []

    for det in detections:

        vx, vy = det.get(
            "velocity",
            (0.0, 0.0)
        )

        x1, y1, x2, y2 = det["xyxy"]

        dx = vx * dt
        dy = vy * dt

        new_det = dict(det)

        new_det["xyxy"] = (
            x1 + dx,
            y1 + dy,
            x2 + dx,
            y2 + dy
        )

        projected.append(new_det)

    return projected


# ============================================================
# DIBUJAR RESULTADOS
# ============================================================

def draw_detections(
    frame,
    detections
):

    output = frame.copy()

    if not detections:
        return output

    with config_lock:

        local_show_boxes = show_boxes
        local_show_labels = show_labels

    # --------------------------------------------------------
    # DIBUJAR CADA DETECCIÓN
    # --------------------------------------------------------

    for det in detections:

        x1, y1, x2, y2 = [
            int(v)
            for v in det["xyxy"]
        ]

        # ====================================================
        # BOUNDING BOX
        # ====================================================

        if local_show_boxes:

            cv2.rectangle(
                output,
                (x1, y1),
                (x2, y2),
                255,
                2
            )

        # ====================================================
        # ETIQUETA
        # ====================================================

        if local_show_labels:

            label = (
                f"{det['name']} "
                f"{det['conf']:.2f}"
            )

            # ------------------------------------------------
            # TAMAÑO DEL TEXTO
            # ------------------------------------------------

            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.55
            thickness = 1

            (
                text_width,
                text_height
            ), baseline = (
                cv2.getTextSize(
                    label,
                    font,
                    font_scale,
                    thickness
                )
            )

            padding_x = 7
            padding_y = 5

            box_width = (
                text_width
                + padding_x * 2
            )

            box_height = (
                text_height
                + baseline
                + padding_y * 2
            )

            # ------------------------------------------------
            # POSICIÓN
            # ------------------------------------------------

            label_x = x1

            label_y = (
                y1 - box_height
            )

            # Si no cabe arriba,
            # ponerlo dentro del bounding box.
            if label_y < 0:

                label_y = y1

            # ------------------------------------------------
            # FONDO NEGRO
            # ------------------------------------------------

            cv2.rectangle(
                output,
                (
                    label_x,
                    label_y
                ),
                (
                    label_x + box_width,
                    label_y + box_height
                ),
                (0, 0, 0),
                -1
            )

            # ------------------------------------------------
            # BORDE NEGRO / NO AZUL
            # ------------------------------------------------

            cv2.rectangle(
                output,
                (
                    label_x,
                    label_y
                ),
                (
                    label_x + box_width,
                    label_y + box_height
                ),
                (0, 0, 0),
                1
            )

            # ------------------------------------------------
            # TEXTO BLANCO
            # ------------------------------------------------

            text_x = (
                label_x
                + padding_x
            )

            text_y = (
                label_y
                + padding_y
                + text_height
            )

            cv2.putText(
                output,
                label,
                (
                    text_x,
                    text_y
                ),
                font,
                font_scale,
                (255, 255, 255),
                thickness,
                cv2.LINE_AA
            )

    return output


# ============================================================
# HILO DE CÁMARA
# ============================================================

def camera_loop():

    global running
    global camera_ready
    global camera_handle

    global latest_frame
    global latest_capture_time

    global camera_fps
    global capture_ms
    global total_frames

    global drain_fps

    global current_exposure_us
    global camera_resolution

    global last_error

    h_camera = 0
    frame_buffer = 0

    applied_exposure = None

    frame_count = 0
    drained_window = 0
    fps_start = time.perf_counter()

    try:

        print("=" * 70)
        print("DEMA - GE134GM + YOLO26-X")
        print("=" * 70)

        print("Buscando cámara...")

        devices = (
            mvsdk.CameraEnumerateDevice()
        )

        if not devices:

            raise RuntimeError(
                "No se encontró ninguna cámara."
            )

        for i, dev in enumerate(devices):

            print(
                f"{i}: "
                f"{dev.GetFriendlyName()} | "
                f"{dev.GetPortType()}"
            )

        dev = devices[0]

        print(
            "Cámara:",
            dev.GetFriendlyName()
        )

        # ----------------------------------------------------
        # INIT
        # ----------------------------------------------------

        try:

            h_camera = mvsdk.CameraInit(
                dev,
                0,
                0
            )

            print(
                "CameraInit OK "
                "(emParamLoadMode=0, emTeam=0)"
            )

        except mvsdk.CameraException as e:

            print(
                "CameraInit con (0, 0) falló "
                "("
                + str(e.error_code)
                + "): "
                + str(e.message)
                + " — probando con (-1, -1)..."
            )

            h_camera = mvsdk.CameraInit(
                dev,
                -1,
                -1
            )

            print(
                "CameraInit OK "
                "(emParamLoadMode=-1, emTeam=-1)"
            )

        # ----------------------------------------------------
        # DIAGNÓSTICO: GRUPO DE PARÁMETROS ACTIVO
        # ----------------------------------------------------
        # Descartamos la hipótesis del "Team" — la cámara ya
        # estaba en el grupo 0 antes de tocar nada, así que
        # ese sistema no es el mismo que "UserSet"/mode1 de
        # la interfaz gráfica. Dejamos solo el print para
        # confirmar en qué grupo queda con el nuevo init.

        try:

            print(
                "Grupo de parámetros activo:",
                mvsdk.CameraGetCurrentParameterGroup(
                    h_camera
                )
            )

        except Exception as e:

            print(
                "No se pudo leer el grupo de "
                "parámetros:",
                repr(e)
            )

        print("ANTES CameraGetCapability")

        # ----------------------------------------------------
        # DIAGNÓSTICO: CameraCommonCall (AcquisitionFrameRateMode)
        # ----------------------------------------------------
        # No hay función dedicada en mvsdk.py para esta feature
        # GenICam. Un ejemplo en LabVIEW (NI-IMAQdx) contra esta
        # misma cámara mostró que sus atributos se direccionan
        # como "CameraAttributes::<Categoría>::<Feature>" — acá
        # probamos varias formas plausibles de pasarle eso a
        # CameraCommonCall. Es inofensivo: esta función no lanza
        # excepción por un comando inválido, solo devuelve un
        # string/código que logueamos para ver cuál (si alguna)
        # funciona.

        acquisition_frame_rate_mode_attempts = [
            "CameraAttributes::AcquisitionControl::"
            "AcquisitionFrameRateMode=mode1",

            "AcquisitionControl::AcquisitionFrameRateMode=mode1",

            "AcquisitionFrameRateMode=mode1",

            "set AcquisitionFrameRateMode mode1",
        ]

        for attempt in acquisition_frame_rate_mode_attempts:

            try:

                result = mvsdk.CameraCommonCall(
                    h_camera,
                    attempt,
                    256
                )

                print(
                    "CameraCommonCall(",
                    repr(attempt),
                    ") ->",
                    repr(result)
                )

            except Exception as e:

                print(
                    "CameraCommonCall(",
                    repr(attempt),
                    ") excepción:",
                    repr(e)
                )

        cap = mvsdk.CameraGetCapability(h_camera)

        print("DESPUÉS CameraGetCapability")

        # ----------------------------------------------------
        # 1280x1024
        # ----------------------------------------------------

        target = None
        target_desc = None

        print(
            "Buscando preset 1280x1024...",
            flush=True
        )

        for i in range(cap.iImageSizeDesc):
            print(
                f"Preset {i}...",
                flush=True
            )

            desc = cap.pImageSizeDesc[i]

            print(
                f"  {desc.iWidth}x{desc.iHeight}",
                flush=True
            )

            if (
                desc.iWidth == WIDTH
                and desc.iHeight == HEIGHT
            ):
                target = i
                target_desc = desc

                print(
                    f"Usando preset {i}: "
                    f"{desc.iWidth}x{desc.iHeight}",
                    flush=True
                )

                break

        if target is None:
            raise RuntimeError(
                "No existe preset 1280x1024."
            )

        result = (
            mvsdk.CameraSetImageResolution(
                h_camera,
                target_desc
            )
        )

        print(
            "Resolución:",
            result
        )

        if result != 0:
            raise RuntimeError(
                f"CameraSetImageResolution falló: {result}"
            )

        camera_resolution = (
            f"{WIDTH}x{HEIGHT}"
        )

        print(
            "Resolución activa:",
            camera_resolution
        )

        # ----------------------------------------------------
        # MONO8
        # ----------------------------------------------------

        result = (
            mvsdk.CameraSetIspOutFormat(
                h_camera,
                mvsdk.CAMERA_MEDIA_TYPE_MONO8
            )
        )

        print(
            "MONO8:",
            result
        )

        # ----------------------------------------------------
        # CONTINUO
        # ----------------------------------------------------

        result = (
            mvsdk.CameraSetTriggerMode(
                h_camera,
                0
            )
        )

        print(
            "Trigger:",
            result
        )

        # ----------------------------------------------------
        # FRAME SPEED
        # ----------------------------------------------------

        result = (
            mvsdk.CameraSetFrameSpeed(
                h_camera,
                FRAME_SPEED
            )
        )

        print(
            "FrameSpeed:",
            mvsdk.CameraGetFrameSpeed(
                h_camera
            ),
            "resultado:",
            result
        )

        # ----------------------------------------------------
        # FRAME RATE (la función real — 0 = máxima frecuencia)
        # ----------------------------------------------------

        try:

            frame_rate_result = camera_set_frame_rate(
                h_camera,
                0
            )

            print(
                "CameraSetFrameRate(0/máx) resultado:",
                frame_rate_result
            )

            print(
                "CameraGetFrameRate ahora:",
                camera_get_frame_rate(h_camera)
            )

        except Exception as e:

            print(
                "CameraSetFrameRate no disponible en esta "
                "DLL:",
                repr(e)
            )

        # ----------------------------------------------------
        # EXPOSICIÓN MANUAL
        # ----------------------------------------------------

        result = (
            mvsdk.CameraSetAeState(
                h_camera,
                0
            )
        )

        print(
            "AE manual:",
            result
        )

        requested = get_exposure()

        result = (
            mvsdk.CameraSetExposureTime(
                h_camera,
                requested
            )
        )

        current_exposure_us = (
            mvsdk.CameraGetExposureTime(
                h_camera
            )
        )

        applied_exposure = (
            current_exposure_us
        )

        print(
            "Exposición:",
            current_exposure_us,
            "us"
        )

        # ----------------------------------------------------
        # PLAY
        # ----------------------------------------------------

        result = (
            mvsdk.CameraPlay(
                h_camera
            )
        )

        print(
            "CameraPlay:",
            result
        )

        # ----------------------------------------------------
        # BUFFER
        # ----------------------------------------------------

        frame_buffer = mvsdk.CameraAlignMalloc(
            WIDTH * HEIGHT * 4,
            16
        )

        if not frame_buffer:

            raise RuntimeError(
                "No se pudo reservar "
                "el buffer."
            )

        # ----------------------------------------------------
        # PROPIEDADES: camera_config.json es la fuente de verdad
        # ----------------------------------------------------

        camera_handle = h_camera

        try:

            _bootstrap_properties(
                h_camera
            )

        except Exception as e:

            print(
                "No se pudo aplicar camera_config.json:",
                repr(e)
            )

        camera_ready = True

        print()
        print(
            "Cámara lista."
        )

        # ====================================================
        # LOOP
        # ====================================================

        while running:

            try:

                # --------------------------------------------
                # PROPIEDADES PENDIENTES DEL PANEL
                # --------------------------------------------

                _apply_pending_properties(
                    h_camera
                )

                # --------------------------------------------
                # CAMBIO EXPOSICIÓN
                # --------------------------------------------

                requested = get_exposure()

                if (
                    requested
                    != applied_exposure
                ):

                    result = (
                        mvsdk.CameraSetExposureTime(
                            h_camera,
                            requested
                        )
                    )

                    if result == 0:

                        applied_exposure = (
                            requested
                        )

                        current_exposure_us = (
                            requested
                        )

                # --------------------------------------------
                # CAPTURA
                # --------------------------------------------

                start = (
                    time.perf_counter()
                )

                raw, head = mvsdk.CameraGetImageBuffer(
                    h_camera,
                    2000
                )

                # ----------------------------------------------
                # VACIAR BACKLOG DEL DRIVER
                # ----------------------------------------------
                # Si el driver ya tenía más de un frame en cola
                # (porque en algún momento no los consumimos tan
                # rápido como la cámara los generó), acá lo
                # detectamos: pedimos el siguiente frame con un
                # timeout casi nulo (1 ms). Si YA hay uno
                # esperando, soltamos el que acabamos de agarrar
                # sin usarlo y nos quedamos con el nuevo.
                # Repetimos hasta que no quede nada en cola —
                # recién ahí procesamos, y es siempre el frame
                # más reciente posible, nunca uno atrasado.

                drained_this_frame = 0

                while True:

                    try:

                        next_raw, next_head = (
                            mvsdk.CameraGetImageBuffer(
                                h_camera,
                                1
                            )
                        )

                    except mvsdk.CameraException:

                        break

                    mvsdk.CameraReleaseImageBuffer(
                        h_camera,
                        raw
                    )

                    raw, head = (
                        next_raw,
                        next_head
                    )

                    drained_this_frame += 1

                    # Salvavidas: no drenar sin límite si algo
                    # anda raro (ej. la cámara entregando frames
                    # más rápido de lo que ni siquiera podemos
                    # descartar).
                    if drained_this_frame >= 20:
                        break

                drained_window += drained_this_frame

                try:
                    mvsdk.CameraImageProcess(
                        h_camera,
                        raw,
                        frame_buffer,
                        head
                    )

                finally:
                    mvsdk.CameraReleaseImageBuffer(
                        h_camera,
                        raw
                    )

                capture_ms = (
                    time.perf_counter()
                    - start
                ) * 1000.0

                # --------------------------------------------
                # NUMPY
                # --------------------------------------------

                data = (
                    mvsdk.c_ubyte
                    * (head.iWidth * head.iHeight)
                ).from_address(
                    frame_buffer
                )

                frame = (
                    np.frombuffer(
                        data,
                        dtype=np.uint8
                    )
                    .reshape(
                        head.iHeight,
                        head.iWidth
                    )
                    .copy()
                )

                # --------------------------------------------
                # ORIENTACIÓN (VOLTEAR / ESPEJO)
                # --------------------------------------------

                frame = apply_orientation(
                    frame
                )

                timestamp = time.time()

                # --------------------------------------------
                # ÚLTIMO FRAME
                # --------------------------------------------

                with frame_lock:

                    latest_frame = (
                        frame.copy()
                    )

                    latest_capture_time = (
                        timestamp
                    )

                # Avisar inmediatamente al servidor web:
                # hay un frame nuevo para mostrar, sin
                # esperar a que YOLO termine de inferir.
                frame_ready.set()

                total_frames += 1
                frame_count += 1

                globals()[
                    "capture_ms"
                ] = capture_ms

                # --------------------------------------------
                # FPS CÁMARA
                # --------------------------------------------

                now = time.perf_counter()

                elapsed = (
                    now - fps_start
                )

                if elapsed >= 1.0:

                    camera_fps = (
                        frame_count
                        / elapsed
                    )

                    drain_fps = (
                        drained_window
                        / elapsed
                    )

                    frame_count = 0
                    drained_window = 0

                    fps_start = now

            except (
                mvsdk.CameraException
            ) as e:

                print(
                    "CameraException:",
                    getattr(
                        e,
                        "error_code",
                        None
                    ),
                    getattr(
                        e,
                        "message",
                        None
                    )
                )

                time.sleep(
                    0.002
                )

    except Exception as e:

        last_error = str(e)

        print(
            "ERROR CÁMARA:",
            repr(e)
        )

        running = False

    finally:

        camera_ready = False
        camera_handle = 0

        if h_camera:

            try:

                mvsdk.CameraUnInit(
                    h_camera
                )

            except Exception:
                pass

        if frame_buffer:

            try:

                mvsdk.CameraAlignFree(
                    frame_buffer
                )

            except Exception:
                pass

        print(
            "Cámara cerrada."
        )


# ============================================================
# HILO YOLO
# ============================================================

def yolo_loop():

    global running
    global latest_detections
    global latest_detections_time

    global yolo_fps
    global yolo_ms
    global detections_count
    global total_inferences

    global model_ready
    global last_error

    print()
    print("Cargando YOLO26-X...")

    try:

        model = YOLO(
            MODEL_PATH
        )

        model.to(DEVICE)

        print("Modelo:", model.task)
        print("Device:", DEVICE)
        if torch.cuda.is_available():
            print(
                "GPU:",
                torch.cuda.get_device_name(0)
            )

        model_ready = True

        # ====================================================
        # WARM-UP
        # ====================================================

        print("Warm-up YOLO...")

        warmup_frame = None

        while running:

            with frame_lock:

                if latest_frame is not None:

                    warmup_frame = latest_frame.copy()
                    break

            time.sleep(0.01)

        if warmup_frame is None:
            print(
                "No se pudo obtener frame para "
                "warm-up. La cámara no inició.",
                flush=True
            )
            model_ready = False
            return

        warmup_bgr = cv2.cvtColor(
            warmup_frame,
            cv2.COLOR_GRAY2BGR
        )

        (
            _enabled,
            _conf,
            _iou,
            _imgsz,
            _max_det,
            _boxes,
            _labels
        ) = get_yolo_config()

        for _ in range(2):

            model.predict(
                source=warmup_bgr,
                imgsz=_imgsz,
                conf=_conf,
                iou=_iou,
                max_det=_max_det,
                device=DEVICE,
                verbose=False
            )

            if torch.cuda.is_available():
                torch.cuda.synchronize()

        print("YOLO listo.")

    except Exception as e:

        last_error = str(e)

        print(
            "ERROR YOLO:",
            repr(e)
        )

        model_ready = False

        return

    # ========================================================
    # ÚLTIMO FRAME INFERIDO
    # ========================================================

    last_capture_timestamp = -1.0

    # ========================================================
    # FPS YOLO
    # ========================================================

    fps_counter = 0
    fps_timer = time.perf_counter()

    while running:

        (
            enabled,
            conf,
            iou,
            input_size,
            max_detections,
            _boxes,
            _labels
        ) = get_yolo_config()

        # ====================================================
        # TOMAR SIEMPRE EL FRAME MÁS RECIENTE
        # ====================================================

        with frame_lock:

            if latest_frame is None:

                frame = None
                capture_timestamp = 0.0

            else:

                frame = latest_frame.copy()
                capture_timestamp = latest_capture_time

        if frame is None:

            time.sleep(0.001)
            continue

        # ====================================================
        # DESCARTAR FRAME REPETIDO
        # ====================================================

        if capture_timestamp == last_capture_timestamp:

            time.sleep(0.001)
            continue

        last_capture_timestamp = capture_timestamp

        # ====================================================
        # SI YOLO ESTÁ DESACTIVADO
        # ====================================================
        # No renderizamos nada acá: web_loop() ya no depende
        # de este hilo para mostrar video. Solo dejamos sin
        # detecciones vigentes.

        if not enabled:

            detections_count = 0

            with detections_lock:

                latest_detections = []

                latest_detections_time = (
                    capture_timestamp
                )

            continue

        # ====================================================
        # PREPARAR IMAGEN
        # ====================================================

        frame_bgr = cv2.cvtColor(
            frame,
            cv2.COLOR_GRAY2BGR
        )

        # ====================================================
        # INFERENCIA
        # ====================================================

        try:

            if torch.cuda.is_available():
                torch.cuda.synchronize()

            start = time.perf_counter()

            results = model.predict(
                source=frame_bgr,
                imgsz=input_size,
                conf=conf,
                iou=iou,
                max_det=max_detections,
                device=DEVICE,
                verbose=False
            )

            if torch.cuda.is_available():
                torch.cuda.synchronize()

            elapsed_ms = (
                time.perf_counter()
                - start
            ) * 1000.0

            yolo_ms = elapsed_ms

            total_inferences += 1
            fps_counter += 1

            # =================================================
            # FPS REAL DE YOLO
            # =================================================

            now = time.perf_counter()

            fps_elapsed = (
                now - fps_timer
            )

            if fps_elapsed >= 1.0:

                yolo_fps = (
                    fps_counter
                    / fps_elapsed
                )

                fps_counter = 0
                fps_timer = now

            # =================================================
            # RESULTADO EN FORMATO PLANO + VELOCIDAD
            # =================================================

            result = (
                results[0]
                if results
                else None
            )

            detections = extract_plain_detections(
                result
            )

            estimate_velocities(
                detections,
                capture_timestamp
            )

            detections_count = len(
                detections
            )

            # =================================================
            # PUBLICAR (DATOS, NO PÍXELES)
            # =================================================

            with detections_lock:

                latest_detections = detections

                # El timestamp corresponde al frame
                # que realmente procesó YOLO — web_loop()
                # lo usa para saber qué tan viejas están
                # estas detecciones respecto de lo que
                # está mostrando.
                latest_detections_time = (
                    capture_timestamp
                )

        except Exception as e:

            last_error = str(e)

            print(
                "ERROR YOLO:",
                repr(e)
            )

            time.sleep(0.01)

# ============================================================
# HILO WEB
# ============================================================

def web_loop():

    global latest_jpeg
    global latest_jpeg_time
    global latest_sequence
    global web_fps
    global frame_age_ms
    global detection_age_ms

    count = 0
    fps_start = time.perf_counter()

    while running:

        # ----------------------------------------------------
        # ESPERAR A QUE LA CÁMARA PUBLIQUE UN FRAME NUEVO
        # (ya NO esperamos a YOLO — por eso el video deja de
        # ir al ritmo de la inferencia)
        # ----------------------------------------------------

        frame_ready.wait(
            timeout=0.5
        )

        if not running:
            break

        # Consumimos el evento.
        frame_ready.clear()

        # ----------------------------------------------------
        # SIEMPRE EL FRAME DE CÁMARA MÁS RECIENTE
        # ----------------------------------------------------

        with frame_lock:

            if latest_frame is None:
                continue

            raw_frame = (
                latest_frame.copy()
            )

            capture_timestamp = (
                latest_capture_time
            )

        # ----------------------------------------------------
        # ÚLTIMAS DETECCIONES DISPONIBLES
        # (casi siempre van a ser un poco más viejas que el
        # frame que se está por mostrar — es el precio de
        # desacoplar el video de la inferencia)
        # ----------------------------------------------------

        with detections_lock:

            detections = latest_detections
            detections_time = latest_detections_time

        with config_lock:

            local_predict = predict_motion

        if detections and local_predict:

            detections_to_draw = project_detections(
                detections,
                detections_time,
                capture_timestamp
            )

        else:

            detections_to_draw = detections

        # ----------------------------------------------------
        # RENDER: frame crudo + cajas encima
        # ----------------------------------------------------

        frame_bgr = cv2.cvtColor(
            raw_frame,
            cv2.COLOR_GRAY2BGR
        )

        frame = draw_detections(
            frame_bgr,
            detections_to_draw
        )

        # ----------------------------------------------------
        # RESIZE SOLO PARA WEB
        # ----------------------------------------------------

        display = cv2.resize(
            frame,
            (
                WEB_WIDTH,
                WEB_HEIGHT
            ),
            interpolation=cv2.INTER_AREA
        )

        # ----------------------------------------------------
        # JPEG INMEDIATO
        # ----------------------------------------------------

        ok, encoded = cv2.imencode(
            ".jpg",
            display,
            [
                cv2.IMWRITE_JPEG_QUALITY,
                JPEG_QUALITY
            ]
        )

        if not ok:
            continue

        # ----------------------------------------------------
        # PUBLICAR
        # ----------------------------------------------------

        with jpeg_lock:

            latest_jpeg = (
                encoded.tobytes()
            )

            latest_jpeg_time = (
                capture_timestamp
            )

            latest_sequence += 1

        # ----------------------------------------------------
        # EDAD DE LA IMAGEN (cámara -> pantalla)
        # ----------------------------------------------------

        frame_age_ms = max(
            0.0,
            (
                time.time()
                - capture_timestamp
            ) * 1000.0
        )

        # ----------------------------------------------------
        # EDAD DE LA DETECCIÓN (qué tan atrasada va la caja
        # respecto de la imagen que se está mostrando ahora)
        # ----------------------------------------------------

        detection_age_ms = max(
            0.0,
            (
                capture_timestamp
                - detections_time
            ) * 1000.0
        )

        # ----------------------------------------------------
        # FPS DE PUBLICACIÓN
        # ----------------------------------------------------

        count += 1

        now = time.perf_counter()

        elapsed = (
            now - fps_start
        )

        if elapsed >= 1.0:

            web_fps = (
                count / elapsed
            )

            count = 0
            fps_start = now


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!DOCTYPE html>

<html>

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
Visión · DEMACYA
</title>

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">

<style>

:root {
    --bg: #070b13;
    --bg-elev: #0d1320;
    --panel-a: rgba(16, 26, 43, 0.72);
    --panel-b: rgba(10, 16, 27, 0.55);
    --line: #1b2740;
    --line-soft: rgba(120, 150, 200, 0.06);
    --text: #e9eef7;
    --text-dim: #8896ac;
    --text-faint: #5b6880;
    --brand: #1f83d6;
    --brand-bright: #38a0ee;
    --ok: #4ad991;
    --err: #e65c5c;
    --mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Consolas, monospace;
}

* { box-sizing: border-box; }

html, body { margin: 0; padding: 0; min-height: 100%; }

body {
    min-height: 100vh;
    background: var(--bg);
    color: var(--text);
    font-family: "Inter", "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
    text-align: left;
}

body::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: -1;
    background:
        radial-gradient(920px 520px at 50% -12%, rgba(31, 131, 214, 0.16), transparent 70%),
        linear-gradient(var(--line-soft) 1px, transparent 1px) 0 0 / 44px 44px,
        linear-gradient(90deg, var(--line-soft) 1px, transparent 1px) 0 0 / 44px 44px,
        var(--bg);
}

a { color: inherit; }


/* ---------- TOPBAR ---------- */

.topbar {
    position: sticky;
    top: 0;
    z-index: 10;
    background: rgba(7, 11, 19, 0.8);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--line);
}

.topbar-inner {
    max-width: 1250px;
    margin: 0 auto;
    height: 66px;
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
}

.brand {
    display: flex;
    align-items: center;
    gap: 11px;
    text-decoration: none;
    color: var(--text);
}

.brand-mark {
    width: 30px;
    height: 30px;
    display: grid;
    place-items: center;
    border-radius: 8px;
    background: var(--brand);
    color: #fff;
    font-weight: 700;
    font-size: 15px;
}

.brand span {
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.14em;
}

.topbar-right { display: flex; align-items: center; gap: 14px; }

.live {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    color: var(--text-dim);
    font-family: var(--mono);
    letter-spacing: 0.08em;
}

.live .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-faint);
    box-shadow: none;
    transition: background 0.2s ease, box-shadow 0.2s ease;
}

.live.on .dot { background: var(--ok); box-shadow: 0 0 8px rgba(74, 217, 145, 0.6); }


/* ---------- PÁGINA ---------- */

.page {
    max-width: 1250px;
    margin: 0 auto;
    padding: 34px 24px 80px;
}

.page-head { margin-bottom: 24px; }

.eyebrow {
    margin: 0;
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--brand-bright);
}

.page-title {
    margin: 10px 0 0;
    font-size: 26px;
    font-weight: 700;
    line-height: 1.15;
}

.lead {
    margin: 8px 0 0;
    color: var(--text-dim);
    font-size: 13.5px;
    line-height: 1.6;
}


/* ---------- BOTONES ---------- */

button, .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    height: 38px;
    padding: 0 16px;
    border: 1px solid var(--line);
    border-radius: 9px;
    background: transparent;
    color: var(--text-dim);
    font-family: inherit;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    cursor: pointer;
    transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

button:hover, .btn:hover { color: var(--text); border-color: var(--brand); }

.btn-primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.btn-primary:hover { background: var(--brand-bright); border-color: var(--brand-bright); color: #fff; }

.btn-sm { height: 32px; padding: 0 12px; font-size: 12px; }

.btn-chip { height: 30px; padding: 0 10px; font-size: 11px; font-family: var(--mono); letter-spacing: 0.04em; }


/* ---------- PANELES ---------- */

.panel {
    max-width: 1250px;
    margin: 18px auto 0;
    padding: 20px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: linear-gradient(160deg, var(--panel-a), var(--panel-b));
}

.panel > h2 {
    margin: 0 0 14px;
    font-size: 14px;
    font-weight: 600;
}


/* ---------- REJILLA DE CONTROLES ---------- */

.control-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 12px;
}

.control {
    background: rgba(7, 13, 24, 0.6);
    padding: 12px;
    border-radius: 10px;
    border: 1px solid var(--line);
}

.control-title {
    color: var(--text-faint);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-family: var(--mono);
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    gap: 8px;
}

.control-value { color: var(--brand-bright); font-weight: 600; }

input[type=range] {
    width: 100%;
    accent-color: var(--brand);
    height: auto;
    padding: 0;
    background: transparent;
    border: 0;
}

input[type=number], input[type=text], select {
    width: 100%;
    height: 36px;
    padding: 0 10px;
    background: #0a1120;
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 8px;
    font-family: inherit;
    font-size: 13px;
}

input:focus, select:focus { outline: none; border-color: var(--brand); }

.control .buttons { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.control .buttons button { height: 30px; padding: 0 10px; font-size: 11px; }

.buttons { display: flex; flex-wrap: wrap; gap: 8px; }

.toggle { width: 100%; }
.toggle.on { border-color: rgba(74, 217, 145, 0.5); color: #8be9b6; }


/* ---------- VISOR ---------- */

.viewer {
    max-width: 1250px;
    margin: 18px auto 0;
    display: flex;
    gap: 16px;
    align-items: flex-start;
}

.image-panel { flex: 1; min-width: 0; }

#video {
    display: block;
    width: 100%;
    height: auto;
    background: #000;
    border: 1px solid var(--line);
    border-radius: 14px;
}

.metrics {
    width: 300px;
    flex-shrink: 0;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: linear-gradient(160deg, var(--panel-a), var(--panel-b));
    padding: 18px;
}

.metrics-title {
    font-size: 13px;
    font-weight: 600;
    border-bottom: 1px solid var(--line);
    padding-bottom: 12px;
    margin-bottom: 6px;
}

.section {
    color: var(--text-faint);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.14em;
    margin-top: 14px;
    margin-bottom: 4px;
    text-transform: uppercase;
}

.metric {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    padding: 7px 0;
    border-bottom: 1px solid var(--line-soft);
    font-size: 13px;
}

.metric-label { color: var(--text-dim); }
.metric-value { color: var(--text); font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }

.status {
    margin-top: 14px;
    padding: 10px 12px;
    background: rgba(7, 13, 24, 0.6);
    border: 1px solid var(--line);
    border-radius: 9px;
    font-size: 12px;
    color: var(--text-dim);
}

.status.ok { border-color: rgba(74, 217, 145, 0.4); color: #9ff0c4; background: rgba(74, 217, 145, 0.08); }
.status.err { border-color: rgba(230, 92, 92, 0.4); color: #f2b0b0; background: rgba(230, 92, 92, 0.08); }


/* ---------- PANEL DE CONFIGURACIÓN DE CÁMARA ---------- */

.cam-config {
    max-width: 1250px;
    margin: 18px auto 0;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: linear-gradient(160deg, var(--panel-a), var(--panel-b));
    padding: 0 20px;
}

.cam-config > summary {
    cursor: pointer;
    padding: 18px 0;
    font-size: 14px;
    font-weight: 600;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 10px;
}

.cam-config > summary::-webkit-details-marker { display: none; }

.cam-config > summary::before {
    content: "\25B8";
    color: var(--text-faint);
    font-size: 11px;
}

.cam-config[open] > summary::before { content: "\25BE"; }

.cam-config-body { padding-bottom: 20px; }

.cam-config .lead { margin: 0 0 6px; }

.cam-config .section { color: var(--brand-bright); margin: 18px 0 6px; }

.cam-prop {
    display: grid;
    grid-template-columns: 1fr 160px auto;
    gap: 12px;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid var(--line-soft);
    font-size: 13px;
}

.cam-prop > div:first-child { color: var(--text); }
.cam-prop small { color: var(--text-faint); margin-left: 6px; font-family: var(--mono); font-size: 11px; }

.cam-prop .cam-msg {
    grid-column: 1 / -1;
    font-size: 11px;
    color: var(--text-faint);
    font-family: var(--mono);
}

.cam-prop .cam-msg.ok { color: #8be9b6; }
.cam-prop .cam-msg.err { color: #efa3a3; }

.cam-config .buttons { margin-top: 18px; }


/* ---------- RESPONSIVE ---------- */

@media (max-width: 960px) {
    .viewer { flex-direction: column; }
    .metrics { width: 100%; }
}

@media (max-width: 560px) {
    .live { display: none; }
    .cam-prop { grid-template-columns: 1fr 1fr; }
    .page { padding: 24px 16px 60px; }
}

/* ---------- LAYOUT 3 COLUMNAS ---------- */

.layout {
    max-width: 1500px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr) 400px;
    gap: 18px;
    align-items: start;
}

.col { min-width: 0; }

.col-left, .col-center { position: sticky; top: 84px; }

.col-center .image-panel { margin: 0; }

.col-right {
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-height: calc(100vh - 100px);
    overflow-y: auto;
    padding-right: 4px;
}

.col-right .panel,
.col-right .controls,
.col-right .cam-config { margin: 0; max-width: none; }

.col-right::-webkit-scrollbar { width: 8px; }
.col-right::-webkit-scrollbar-thumb { background: var(--line); border-radius: 8px; }

.metrics { width: 100%; }


/* ---------- BLOQUE GUARDAR ---------- */

.save-block {
    border-color: rgba(31, 131, 214, 0.45);
    box-shadow: 0 0 0 1px rgba(31, 131, 214, 0.12) inset;
}

.save-block h2 { margin: 0 0 6px; font-size: 15px; }

.save-block .lead { margin: 0 0 14px; font-size: 12.5px; }

.save-block .lead .mono {
    font-family: var(--mono);
    color: var(--brand-bright);
    font-size: 0.92em;
}

.save-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.btn-lg { height: 44px; padding: 0 22px; font-size: 14px; }

.save-badge {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.06em;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid var(--line);
    color: var(--text-faint);
}

.save-badge.saved { border-color: rgba(74, 217, 145, 0.5); color: #8be9b6; }

.save-badge.unsaved {
    border-color: rgba(240, 180, 90, 0.6);
    color: #f2c88a;
    background: rgba(240, 180, 90, 0.1);
}

.save-meta { margin: 12px 0 0; font-size: 11.5px; color: var(--text-faint); font-family: var(--mono); }

.save-actions { margin-top: 12px; }


/* ---------- CONFIG SIEMPRE VISIBLE (ya no es <details>) ---------- */

.cam-config { padding: 20px; }
.cam-config > h2 { margin: 0 0 4px; font-size: 14px; font-weight: 600; }

#camProps { margin-top: 4px; }


@media (max-width: 1240px) {
    .layout { grid-template-columns: minmax(0, 1fr) 380px; }
    .col-left { grid-column: 1 / -1; position: static; }
    .col-left .metrics { max-width: 640px; }
}

@media (max-width: 900px) {
    .layout { grid-template-columns: 1fr; }
    .col-left, .col-center, .col-right { position: static; grid-column: auto; }
    .col-right { max-height: none; overflow: visible; }
}

</style>

</head>

<body>

<header class="topbar">
    <div class="topbar-inner">
        <a class="brand" href="__MENU_URL__">
            <span class="brand-mark">D</span>
            <span>VISIÓN</span>
        </a>
        <div class="topbar-right">
            <span class="live" id="liveDot"><span class="dot"></span><span id="liveText">conectando</span></span>
            <a class="btn btn-sm" href="__MENU_URL__">Volver al menú</a>
        </div>
    </div>
</header>

<main class="page">

<div class="page-head">
    <p class="eyebrow">Cámara · GE134GM + YOLO26-X</p>
    <h1 class="page-title">Visión</h1>
    <p class="lead">Vídeo en vivo, parámetros de inferencia y configuración de la cámara.</p>
</div>

<div class="layout">

  <aside class="col col-left">
    <div class="metrics">



        <div class="metrics-title">
            Estado de cámara
        </div>


        <div class="section">
            CÁMARA
        </div>


        <div class="metric">

            <span class="metric-label">
                FPS
            </span>

            <span
                class="metric-value"
                id="cameraFps"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Captura
            </span>

            <span
                class="metric-value"
                id="captureMs"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Backlog cámara
            </span>

            <span
                class="metric-value"
                id="drainFps"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Resolución
            </span>

            <span
                class="metric-value"
                id="resolution"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Exposición
            </span>

            <span
                class="metric-value"
                id="exposure"
            >
                --
            </span>

        </div>


        <div class="section">
            YOLO
        </div>


        <div class="metric">

            <span class="metric-label">
                Modelo
            </span>

            <span
                class="metric-value"
            >
                YOLO26-X
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                FPS
            </span>

            <span
                class="metric-value"
                id="yoloFps"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Latencia
            </span>

            <span
                class="metric-value"
                id="yoloMs"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Detecciones
            </span>

            <span
                class="metric-value"
                id="detections"
            >
                0
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Input
            </span>

            <span
                class="metric-value"
                id="inputSize"
            >
                640
            </span>

        </div>


        <div class="section">
            GPU NVIDIA
        </div>


        <div class="metric">

            <span class="metric-label">
                GPU
            </span>

            <span
                class="metric-value"
                id="gpuName"
            >
                RTX
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                GPU uso
            </span>

            <span
                class="metric-value"
                id="gpuUtil"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                VRAM
            </span>

            <span
                class="metric-value"
                id="gpuMemory"
            >
                --
            </span>

        </div>


        <div class="section">
            SISTEMA
        </div>


        <div class="metric">

            <span class="metric-label">
                CPU
            </span>

            <span
                class="metric-value"
                id="cpu"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                RAM
            </span>

            <span
                class="metric-value"
                id="ram"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Frames
            </span>

            <span
                class="metric-value"
                id="frames"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Edad frame
            </span>

            <span
                class="metric-value"
                id="frameAge"
            >
                --
            </span>

        </div>


        <div class="metric">

            <span class="metric-label">
                Edad detección
            </span>

            <span
                class="metric-value"
                id="detectionAge"
            >
                --
            </span>

        </div>
    </div>
  </aside>

  <section class="col col-center">
<div class="image-panel">

        <img
            id="video"
            alt="GE134GM"
        >

    </div>
    <div class="status" id="status">
      Iniciando...
    </div>
  </section>

  <aside class="col col-right">

    <section class="panel save-block" id="saveBlock">
      <h2>Configuración de la cámara</h2>
      <p class="lead">Se guarda en <span class="mono">camera_config.json</span> — el archivo que el
      servicio aplica a la cámara en cada arranque y que usarán todos los procesos de visión.
      Al "Aplicar" una propiedad cambia la cámara en vivo (previsualización); pulsa
      <b>Guardar configuración</b> para dejarla fija en el archivo.</p>
      <div class="save-row">
        <button class="btn btn-primary btn-lg" onclick="saveCameraConfig()">Guardar configuración</button>
        <span class="save-badge" id="saveBadge">—</span>
      </div>
      <p class="save-meta" id="saveMeta">—</p>
      <div class="save-actions">
        <button class="btn btn-sm" onclick="reloadCameraConfig()">Descartar cambios y recargar del archivo</button>
      </div>
      <div class="status" id="camConfigStatus">—</div>
    </section>

    <section class="panel cam-config" id="camConfig">
      <h2>Propiedades de la cámara</h2>
      <div id="camProps">Cargando…</div>
    </section>

<div class="controls">


    <div class="control-grid">


        <!-- EXPOSICIÓN -->

        <div class="control">

            <div class="control-title">
                EXPOSICIÓN
                <span
                    class="control-value"
                    id="exposureValue"
                >
                    2.000 ms
                </span>
            </div>

            <input
                id="exposureSlider"
                type="range"
                min="1"
                max="30000"
                step="1"
                value="2000"
            >

            <input
                id="exposureNumber"
                type="number"
                min="1"
                max="131071"
                value="2000"
                step="1"
            >

            <div class="buttons">

                <button onclick="setExposure(500)">
                    0.5 ms
                </button>

                <button onclick="setExposure(1000)">
                    1 ms
                </button>

                <button onclick="setExposure(2000)">
                    2 ms
                </button>

                <button onclick="setExposure(5000)">
                    5 ms
                </button>

                <button onclick="setExposure(10000)">
                    10 ms
                </button>

            </div>

        </div>


        <!-- CONFIDENCE -->

        <div class="control">

            <div class="control-title">
                CONFIDENCE
                <span
                    class="control-value"
                    id="confidenceValue"
                >
                    0.25
                </span>
            </div>

            <input
                id="confidenceSlider"
                type="range"
                min="0.01"
                max="0.99"
                step="0.01"
                value="0.25"
            >

        </div>


        <!-- IOU -->

        <div class="control">

            <div class="control-title">
                IoU NMS
                <span
                    class="control-value"
                    id="iouValue"
                >
                    0.45
                </span>
            </div>

            <input
                id="iouSlider"
                type="range"
                min="0.05"
                max="0.95"
                step="0.01"
                value="0.45"
            >

        </div>


        <!-- IMAGE SIZE -->

        <div class="control">

            <div class="control-title">
                YOLO IMAGE SIZE
            </div>

            <select id="imgsz">

                <option value="320">
                    320
                </option>

                <option value="416">
                    416
                </option>

                <option value="512">
                    512
                </option>

                <option value="640" selected>
                    640
                </option>

                <option value="768">
                    768
                </option>

                <option value="960">
                    960
                </option>

                <option value="1024">
                    1024
                </option>

            </select>

        </div>


        <!-- MAX DET -->

        <div class="control">

            <div class="control-title">
                MÁX. DETECCIONES
            </div>

            <input
                id="maxDet"
                type="number"
                min="1"
                max="300"
                value="100"
            >

        </div>


        <!-- YOLO ON/OFF -->

        <div class="control">

            <div class="control-title">
                DETECCIÓN
            </div>

            <button
                class="toggle"
                onclick="toggleYolo()"
                id="yoloButton"
            >
                YOLO: ON
            </button>

        </div>


        <!-- BOXES -->

        <div class="control">

            <div class="control-title">
                VISUALIZACIÓN
            </div>

            <button
                class="toggle"
                onclick="toggleBoxes()"
                id="boxesButton"
            >
                Cajas: ON
            </button>

        </div>


        <!-- LABELS -->

        <div class="control">

            <div class="control-title">
                VISUALIZACIÓN
            </div>

            <button
                class="toggle"
                onclick="toggleLabels()"
                id="labelsButton"
            >
                Etiquetas: OFF
            </button>

        </div>


        <!-- VOLTEAR VERTICAL -->

        <div class="control">

            <div class="control-title">
                ORIENTACIÓN
            </div>

            <button
                class="toggle"
                onclick="toggleFlip()"
                id="flipButton"
            >
                Voltear: OFF
            </button>

        </div>


        <!-- MIRROR HORIZONTAL -->

        <div class="control">

            <div class="control-title">
                ORIENTACIÓN
            </div>

            <button
                class="toggle"
                onclick="toggleMirror()"
                id="mirrorButton"
            >
                Espejo: OFF
            </button>

        </div>


        <!-- PREDICCIÓN DE MOVIMIENTO -->

        <div class="control">

            <div class="control-title">
                DETECCIÓN
            </div>

            <button
                class="toggle"
                onclick="togglePredict()"
                id="predictButton"
            >
                Predicción: ON
            </button>

        </div>


    </div>


    <div class="buttons">

        <button onclick="applyYolo()">
            Aplicar parámetros YOLO
        </button>

    </div>


</div>


<!-- ========================================================
     CONFIGURACIÓN DE CÁMARA (camera_config.json)
     ======================================================== -->


  </aside>

</div>

</main>

<script>


// ==========================================================
// ELEMENTOS
// ==========================================================

const video =
    document.getElementById(
        "video"
    );

const exposureSlider =
    document.getElementById(
        "exposureSlider"
    );

const exposureNumber =
    document.getElementById(
        "exposureNumber"
    );

const exposureValue =
    document.getElementById(
        "exposureValue"
    );

const confidenceSlider =
    document.getElementById(
        "confidenceSlider"
    );

const confidenceValue =
    document.getElementById(
        "confidenceValue"
    );

const iouSlider =
    document.getElementById(
        "iouSlider"
    );

const iouValue =
    document.getElementById(
        "iouValue"
    );

const imgsz =
    document.getElementById(
        "imgsz"
    );

const maxDet =
    document.getElementById(
        "maxDet"
    );

const yoloButton =
    document.getElementById(
        "yoloButton"
    );

const boxesButton =
    document.getElementById(
        "boxesButton"
    );

const labelsButton =
    document.getElementById(
        "labelsButton"
    );

const flipButton =
    document.getElementById(
        "flipButton"
    );

const mirrorButton =
    document.getElementById(
        "mirrorButton"
    );

const predictButton =
    document.getElementById(
        "predictButton"
    );


// ==========================================================
// INDICADORES
// ==========================================================

const cameraFps =
    document.getElementById(
        "cameraFps"
    );

const captureMs =
    document.getElementById(
        "captureMs"
    );

const drainFps =
    document.getElementById(
        "drainFps"
    );

const resolution =
    document.getElementById(
        "resolution"
    );

const exposure =
    document.getElementById(
        "exposure"
    );

const yoloFps =
    document.getElementById(
        "yoloFps"
    );

const yoloMs =
    document.getElementById(
        "yoloMs"
    );

const detections =
    document.getElementById(
        "detections"
    );

const inputSize =
    document.getElementById(
        "inputSize"
    );

const gpuName =
    document.getElementById(
        "gpuName"
    );

const gpuUtil =
    document.getElementById(
        "gpuUtil"
    );

const gpuMemory =
    document.getElementById(
        "gpuMemory"
    );

const cpu =
    document.getElementById(
        "cpu"
    );

const ram =
    document.getElementById(
        "ram"
    );

const frames =
    document.getElementById(
        "frames"
    );

const frameAge =
    document.getElementById(
        "frameAge"
    );

const detectionAge =
    document.getElementById(
        "detectionAge"
    );

const status =
    document.getElementById(
        "status"
    );


let objectUrl = null;

let loading = false;

let yoloEnabled = true;

let showBoxes = true;

let showLabels = false;

let flipVertical = false;

let mirrorHorizontal = false;

let predictMotion = true;


// ==========================================================
// EXPOSICIÓN
// ==========================================================

function updateExposureLabel(
    value
) {

    exposureValue.innerText =
        (
            Number(value) / 1000
        ).toFixed(3)
        + " ms";
}


function setExposure(
    value
) {

    value = Math.round(
        Number(value)
    );

    value = Math.max(
        1,
        Math.min(
            131071,
            value
        )
    );

    exposureSlider.value =
        Math.min(
            30000,
            value
        );

    exposureNumber.value =
        value;

    updateExposureLabel(
        value
    );

    fetch(
        "/control/exposure?us="
        + value
        + "&t="
        + Date.now(),
        {
            cache: "no-store"
        }
    );

}


exposureSlider.addEventListener(
    "input",
    () => {

        updateExposureLabel(
            exposureSlider.value
        );

    }
);


exposureSlider.addEventListener(
    "change",
    () => {

        setExposure(
            exposureSlider.value
        );

    }
);


exposureNumber.addEventListener(
    "change",
    () => {

        setExposure(
            exposureNumber.value
        );

    }
);


// ==========================================================
// CONFIDENCE
// ==========================================================

confidenceSlider.addEventListener(
    "input",
    () => {

        confidenceValue.innerText =
            Number(
                confidenceSlider.value
            ).toFixed(2);

    }
);


// ==========================================================
// IOU
// ==========================================================

iouSlider.addEventListener(
    "input",
    () => {

        iouValue.innerText =
            Number(
                iouSlider.value
            ).toFixed(2);

    }
);


// ==========================================================
// YOLO
// ==========================================================

function toggleYolo() {

    yoloEnabled =
        !yoloEnabled;

    yoloButton.innerText =
        yoloEnabled
        ? "YOLO: ON"
        : "YOLO: OFF";

    fetch(
        "/control/yolo?enabled="
        + yoloEnabled
    );

}


function toggleBoxes() {

    showBoxes =
        !showBoxes;

    boxesButton.innerText =
        showBoxes
        ? "Cajas: ON"
        : "Cajas: OFF";

    fetch(
        "/control/boxes?enabled="
        + showBoxes
    );

}


function toggleLabels() {

    showLabels =
        !showLabels;

    labelsButton.innerText =
        showLabels
        ? "Etiquetas: ON"
        : "Etiquetas: OFF";

    fetch(
        "/control/labels?enabled="
        + showLabels
    );

}


function toggleFlip() {

    flipVertical =
        !flipVertical;

    flipButton.innerText =
        flipVertical
        ? "Voltear: ON"
        : "Voltear: OFF";

    fetch(
        "/control/flip?enabled="
        + flipVertical
    );

}


function toggleMirror() {

    mirrorHorizontal =
        !mirrorHorizontal;

    mirrorButton.innerText =
        mirrorHorizontal
        ? "Espejo: ON"
        : "Espejo: OFF";

    fetch(
        "/control/mirror?enabled="
        + mirrorHorizontal
    );

}


function togglePredict() {

    predictMotion =
        !predictMotion;

    predictButton.innerText =
        predictMotion
        ? "Predicción: ON"
        : "Predicción: OFF";

    fetch(
        "/control/predict?enabled="
        + predictMotion
    );

}


// ==========================================================
// APLICAR YOLO
// ==========================================================

function applyYolo() {

    const params = new URLSearchParams({

        conf:
            confidenceSlider.value,

        iou:
            iouSlider.value,

        imgsz:
            imgsz.value,

        max_det:
            maxDet.value

    });

    fetch(
        "/control/yolo_params?"
        + params.toString(),
        {
            cache: "no-store"
        }
    )

    .then(
        r => r.json()
    )

    .then(
        data => {

            if (data.ok) {

                status.innerText =
                    "Parámetros YOLO aplicados";

            }
            else {

                status.innerText =
                    "Error: "
                    + data.error;
            }

        }
    )

    .catch(
        () => {

            status.innerText =
                "Error de comunicación";

        }
    );

}


// ==========================================================
// STATUS
// ==========================================================

async function updateStatus() {

    try {

        const response =
            await fetch(
                "/status?t="
                + Date.now(),
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            throw new Error();
        }

        const data =
            await response.json();


        const live = document.getElementById("liveDot");
        if (live) {
            const ok = data.camera_ready && data.model_ready;
            live.classList.toggle("on", ok);
            document.getElementById("liveText").innerText =
                ok ? "en vivo"
                   : (data.camera_ready ? "cargando modelo" : "sin cámara");
        }


        cameraFps.innerText =
            data.camera_fps.toFixed(2)
            + " FPS";


        captureMs.innerText =
            data.capture_ms.toFixed(2)
            + " ms";


        drainFps.innerText =
            data.drain_fps.toFixed(1)
            + " fps";


        resolution.innerText =
            data.resolution;


        exposure.innerText =
            (
                data.exposure_us
                / 1000
            ).toFixed(3)
            + " ms";


        yoloFps.innerText =
            data.yolo_fps.toFixed(2)
            + " FPS";


        yoloMs.innerText =
            data.yolo_ms.toFixed(2)
            + " ms";


        detections.innerText =
            data.detections;


        inputSize.innerText =
            data.imgsz;


        gpuName.innerText =
            data.gpu_name;


        gpuUtil.innerText =
            data.gpu_util.toFixed(0)
            + " %";


        gpuMemory.innerText =
            data.gpu_memory_used.toFixed(0)
            + " / "
            + data.gpu_memory_total.toFixed(0)
            + " MB";


        cpu.innerText =
            data.cpu.toFixed(1)
            + " %";


        ram.innerText =
            data.ram_used.toFixed(0)
            + " / "
            + data.ram_total.toFixed(0)
            + " MB";


        frames.innerText =
            data.frames;


        frameAge.innerText =
            data.frame_age.toFixed(0)
            + " ms";


        detectionAge.innerText =
            data.detection_age.toFixed(0)
            + " ms";


        if (data.last_error) {

            status.innerText =
                data.last_error;

        }
        else if (
            data.camera_ready
            && data.model_ready
        ) {

            status.innerText =
                "Sistema operativo";

        }

    }
    catch (e) {

        status.innerText =
            "Sin respuesta del servicio";

        const live = document.getElementById("liveDot");
        if (live) {
            live.classList.remove("on");
            document.getElementById("liveText").innerText = "sin conexión";
        }

    }

    setTimeout(
        updateStatus,
        500
    );

}


// ==========================================================
// VIDEO
// ==========================================================

async function nextFrame() {

    if (loading) {

        return;

    }

    loading = true;

    try {

        const response =
            await fetch(
                "/frame.jpg?t="
                + Date.now(),
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {

            throw new Error();

        }

        const blob =
            await response.blob();

        const url =
            URL.createObjectURL(
                blob
            );

        video.onload = () => {

            if (objectUrl) {

                URL.revokeObjectURL(
                    objectUrl
                );

            }

            objectUrl = url;

            loading = false;

            requestAnimationFrame(
                nextFrame
            );

        };

        video.onerror = () => {

            URL.revokeObjectURL(
                url
            );

            loading = false;

            setTimeout(
                nextFrame,
                50
            );

        };

        video.src = url;

    }
    catch (e) {

        loading = false;

        setTimeout(
            nextFrame,
            100
        );

    }

}


// ==========================================================
// CONFIGURACIÓN DE CÁMARA
// ==========================================================

const camProps = document.getElementById("camProps");
const camConfigStatus = document.getElementById("camConfigStatus");

let camPropsLoaded = false;


function refreshSaveBadge(data) {

    const badge = document.getElementById("saveBadge");
    const meta = document.getElementById("saveMeta");
    if (!badge) { return; }

    if (data.unsaved) {
        badge.className = "save-badge unsaved";
        badge.innerText = "● cambios sin guardar";
    } else {
        badge.className = "save-badge saved";
        badge.innerText = "guardado";
    }
    meta.innerText = data.last_save
        ? ("Último guardado: " + data.last_save)
        : "Aún no se ha guardado el archivo.";
}


function camSetStatus(text, cls) {

    camConfigStatus.className = "status " + (cls || "");
    camConfigStatus.innerText = text;
}


function camControlHtml(def, value) {

    const pid = def.id;
    const v = value === null || value === undefined ? "" : value;

    if (def.type === "bool" || def.type === "bool_index") {

        const on = String(v) === "1" || v === 1 || v === true;

        return '<select data-prop="' + pid + '">'
            + '<option value="0"' + (on ? "" : " selected") + '>OFF</option>'
            + '<option value="1"' + (on ? " selected" : "") + '>ON</option>'
            + '</select>';
    }

    if (def.type === "enum" && def.options) {

        let opts = "";

        def.options.forEach(function (o) {

            const sel = String(o.value) === String(v) ? " selected" : "";
            opts += '<option value="' + o.value + '"' + sel + '>'
                + o.label + '</option>';
        });

        return '<select data-prop="' + pid + '">' + opts + '</select>';
    }

    const step = def.type === "float" ? "any" : "1";

    return '<input data-prop="' + pid + '" type="number" step="' + step
        + '" value="' + v + '">';
}


function renderCamProps(data) {

    const defs = data.definitions || [];
    const values = data.values || {};
    const status = data.status || {};

    const bySection = {};

    defs.forEach(function (def) {

        (bySection[def.section] = bySection[def.section] || []).push(def);
    });

    let html = "";

    Object.keys(bySection).forEach(function (section) {

        html += '<div class="section">' + section + '</div>';

        bySection[section].forEach(function (def) {

            const st = status[def.id];
            let msg = "";
            let msgCls = "";

            if (st) {

                if (st.set_ok) {
                    msg = "Aplicado (cámara: " + st.verified + ")";
                    msgCls = "ok";
                } else {
                    msg = st.message || "No se pudo aplicar";
                    msgCls = "err";
                }
            }

            html += '<div class="cam-prop" data-row="' + def.id + '">'
                + '<div><b>' + def.label + '</b><small>'
                + (def.unit || "") + '</small></div>'
                + '<div>' + camControlHtml(def, values[def.id]) + '</div>'
                + '<button class="cam-apply btn btn-sm" '
                + 'onclick="applyCamProp(\'' + def.id + '\')">Aplicar</button>'
                + '<div class="cam-msg ' + msgCls + '">' + msg + '</div>'
                + '</div>';
        });
    });

    camProps.innerHTML = html;
}


function loadCamProps() {

    return fetch("/api/props", { cache: "no-store" })

        .then(function (r) { return r.json(); })

        .then(function (data) {

            renderCamProps(data);
            camPropsLoaded = true;
            refreshSaveBadge(data);
        })

        .catch(function () {

            camProps.innerText = "No se pudo cargar la configuración.";
        });
}


function applyCamProp(pid) {

    const row = camProps.querySelector('[data-row="' + pid + '"]');

    if (!row) {
        return;
    }

    const field = row.querySelector("[data-prop]");
    const body = {};
    body[pid] = field.value;

    camSetStatus("Aplicando " + pid + "…");

    fetch("/control/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ properties: body })
    })

        .then(function (r) { return r.json(); })

        .then(function (data) {

            if (!data.ok) {
                camSetStatus("Error: " + (data.error || data.message), "err");
                return;
            }

            camSetStatus(data.message);
            // Dar tiempo a camera_loop() a aplicar y guardar.
            setTimeout(loadCamProps, 900);
        })

        .catch(function () {

            camSetStatus("Error de comunicación", "err");
        });
}


function saveCameraConfig() {

    camSetStatus("Guardando…");

    fetch("/control/save_config", { cache: "no-store" })

        .then(function (r) { return r.json(); })

        .then(function (data) {

            camSetStatus(
                data.ok ? "Guardado solicitado" : ("Error: " + data.error),
                data.ok ? "ok" : "err"
            );

            setTimeout(loadCamProps, 900);
        })

        .catch(function () {

            camSetStatus("Error de comunicación", "err");
        });
}


function reloadCameraConfig() {

    camSetStatus("Recargando desde archivo…");

    fetch("/control/reload_config", { cache: "no-store" })

        .then(function (r) { return r.json(); })

        .then(function (data) {

            camSetStatus(
                data.message,
                data.ok ? "ok" : "err"
            );

            setTimeout(loadCamProps, 900);
        })

        .catch(function () {

            camSetStatus("Error de comunicación", "err");
        });
}




// ==========================================================
// START
// ==========================================================

updateExposureLabel(
    exposureNumber.value
);

confidenceValue.innerText =
    Number(
        confidenceSlider.value
    ).toFixed(2);

iouValue.innerText =
    Number(
        iouSlider.value
    ).toFixed(2);

nextFrame();

updateStatus();

loadCamProps();


</script>

</body>

</html>
"""


# ============================================================
# HTTP HANDLER
# ============================================================

class CameraHandler(
    BaseHTTPRequestHandler
):

    def log_message(
        self,
        format,
        *args
    ):

        pass


    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    def send_json(
        self,
        data,
        status=200
    ):

        payload = (
            json.dumps(
                data,
                separators=(",", ":")
            )
            .encode(
                "utf-8"
            )
        )

        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(payload))
        )

        self.send_header(
            "Cache-Control",
            "no-cache, no-store"
        )

        self.end_headers()

        self.wfile.write(
            payload
        )


    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self):

        path = urlparse(
            self.path
        ).path

        # ====================================================
        # HOME
        # ====================================================

        if path == "/":

            payload = (
                HTML
                .replace("__MENU_URL__", MENU_URL)
                .encode("utf-8")
            )

            self.send_response(
                200
            )

            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(payload))
            )

            self.send_header(
                "Cache-Control",
                "no-cache, no-store"
            )

            self.end_headers()

            self.wfile.write(
                payload
            )

            return


        # ====================================================
        # EXPOSURE
        # ====================================================

        if path == "/control/exposure":

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = int(
                    params["us"][0]
                )

                value = (
                    set_exposure(
                        value
                    )
                )

                self.send_json({
                    "ok": True,
                    "exposure_us":
                        value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # YOLO ON/OFF
        # ====================================================

        if path == "/control/yolo":

            global yolo_enabled

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = (
                    params
                    .get(
                        "enabled",
                        ["true"]
                    )[0]
                    .lower()
                    == "true"
                )

                with config_lock:

                    yolo_enabled = (
                        value
                    )

                self.send_json({
                    "ok": True,
                    "enabled": value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # BOXES
        # ====================================================

        if path == "/control/boxes":

            global show_boxes

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = (
                    params
                    .get(
                        "enabled",
                        ["true"]
                    )[0]
                    .lower()
                    == "true"
                )

                with config_lock:

                    show_boxes = (
                        value
                    )

                self.send_json({
                    "ok": True,
                    "enabled": value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # LABELS
        # ====================================================

        if path == "/control/labels":

            global show_labels

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = (
                    params
                    .get(
                        "enabled",
                        ["false"]
                    )[0]
                    .lower()
                    == "true"
                )

                with config_lock:

                    show_labels = (
                        value
                    )

                self.send_json({
                    "ok": True,
                    "enabled": value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # VOLTEAR VERTICAL
        # ====================================================

        if path == "/control/flip":

            global flip_vertical

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = (
                    params
                    .get(
                        "enabled",
                        ["false"]
                    )[0]
                    .lower()
                    == "true"
                )

                with config_lock:

                    flip_vertical = (
                        value
                    )

                self.send_json({
                    "ok": True,
                    "enabled": value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # ESPEJO HORIZONTAL
        # ====================================================

        if path == "/control/mirror":

            global mirror_horizontal

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = (
                    params
                    .get(
                        "enabled",
                        ["false"]
                    )[0]
                    .lower()
                    == "true"
                )

                with config_lock:

                    mirror_horizontal = (
                        value
                    )

                self.send_json({
                    "ok": True,
                    "enabled": value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # PREDICCIÓN DE MOVIMIENTO
        # ====================================================

        if path == "/control/predict":

            global predict_motion

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                value = (
                    params
                    .get(
                        "enabled",
                        ["true"]
                    )[0]
                    .lower()
                    == "true"
                )

                with config_lock:

                    predict_motion = (
                        value
                    )

                self.send_json({
                    "ok": True,
                    "enabled": value
                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # YOLO PARAMETERS
        # ====================================================

        if path == "/control/yolo_params":

            global confidence
            global iou_threshold
            global imgsz
            global max_det

            try:

                params = parse_qs(
                    urlparse(
                        self.path
                    ).query
                )

                new_conf = float(
                    params["conf"][0]
                )

                new_iou = float(
                    params["iou"][0]
                )

                new_imgsz = int(
                    params["imgsz"][0]
                )

                new_max_det = int(
                    params["max_det"][0]
                )

                new_conf = max(
                    0.01,
                    min(
                        0.99,
                        new_conf
                    )
                )

                new_iou = max(
                    0.01,
                    min(
                        0.99,
                        new_iou
                    )
                )

                new_max_det = max(
                    1,
                    min(
                        300,
                        new_max_det
                    )
                )

                valid_sizes = {
                    320,
                    416,
                    512,
                    640,
                    768,
                    960,
                    1024
                }

                if (
                    new_imgsz
                    not in valid_sizes
                ):

                    raise ValueError(
                        "imgsz no permitido"
                    )

                with config_lock:

                    confidence = (
                        new_conf
                    )

                    iou_threshold = (
                        new_iou
                    )

                    imgsz = (
                        new_imgsz
                    )

                    max_det = (
                        new_max_det
                    )

                self.send_json({

                    "ok": True,

                    "conf":
                        new_conf,

                    "iou":
                        new_iou,

                    "imgsz":
                        new_imgsz,

                    "max_det":
                        new_max_det

                })

            except Exception as e:

                self.send_json(
                    {
                        "ok": False,
                        "error": str(e)
                    },
                    400
                )

            return


        # ====================================================
        # FRAME
        # ====================================================

        if path == "/frame.jpg":

            with jpeg_lock:

                jpeg = (
                    latest_jpeg
                )

                capture_timestamp = (
                    latest_jpeg_time
                )

                sequence = (
                    latest_sequence
                )

            if jpeg is None:

                self.send_response(
                    503
                )

                self.end_headers()

                return

            age = max(
                0.0,
                (
                    time.time()
                    - capture_timestamp
                ) * 1000.0
            )

            self.send_response(
                200
            )

            self.send_header(
                "Content-Type",
                "image/jpeg"
            )

            self.send_header(
                "Content-Length",
                str(len(jpeg))
            )

            self.send_header(
                "Cache-Control",
                "no-cache, no-store"
            )

            self.send_header(
                "X-Sequence",
                str(sequence)
            )

            self.send_header(
                "X-Capture-Age",
                f"{age:.2f}"
            )

            self.end_headers()

            self.wfile.write(
                jpeg
            )

            return


        # ====================================================
        # STATUS
        # ====================================================

        if path == "/status":

            gpu_name = "N/A"

            if torch.cuda.is_available():

                try:

                    gpu_name = (
                        torch.cuda
                        .get_device_name(
                            0
                        )
                    )

                except Exception:

                    pass

            self.send_json({

                "camera_ready":
                    camera_ready,

                "model_ready":
                    model_ready,

                "camera_fps":
                    camera_fps,

                "drain_fps":
                    drain_fps,

                "capture_ms":
                    capture_ms,

                "yolo_fps":
                    yolo_fps,

                "yolo_ms":
                    yolo_ms,

                "web_fps":
                    web_fps,

                "detections":
                    detections_count,

                "frames":
                    total_frames,

                "exposure_us":
                    current_exposure_us,

                "resolution":
                    camera_resolution,

                "imgsz":
                    imgsz,

                "gpu_name":
                    gpu_name,

                "gpu_util":
                    gpu_utilization,

                "gpu_memory_used":
                    gpu_memory_used_mb,

                "gpu_memory_total":
                    gpu_memory_total_mb,

                "gpu_memory_percent":
                    gpu_memory_percent,

                "cpu":
                    cpu_percent,

                "ram_used":
                    ram_used_mb,

                "ram_total":
                    ram_total_mb,

                "ram_percent":
                    ram_percent,

                "frame_age":
                    frame_age_ms,

                "detection_age":
                    detection_age_ms,

                "last_error":
                    last_error

            })

            return


        # ====================================================
        # PROPIEDADES DE CÁMARA — estado del panel
        # ====================================================

        if path == "/api/props":

            self.send_json(
                props_snapshot()
            )

            return


        # ====================================================
        # PROPIEDADES — recargar desde camera_config.json
        # ====================================================

        if path == "/control/reload_config":

            ok, message = reload_properties_from_file()

            self.send_json(
                {
                    "ok": ok,
                    "message": message
                },
                200 if ok else 400
            )

            return


        # ====================================================
        # PROPIEDADES — guardar camera_config.json ahora
        # ====================================================

        if path == "/control/save_config":

            global save_requested

            if not camera_ready:

                self.send_json(
                    {
                        "ok": False,
                        "error": "La cámara no está lista"
                    },
                    409
                )

                return

            with props_lock:
                save_requested = True

            self.send_json({
                "ok": True,
                "message": "Guardado solicitado"
            })

            return


        # ====================================================
        # 404
        # ====================================================

        self.send_response(
            404
        )

        self.end_headers()


    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    def do_POST(self):

        path = urlparse(
            self.path
        ).path

        # ====================================================
        # PROPIEDADES — aplicar cambios del panel
        # ====================================================

        if path == "/control/apply":

            try:

                length = int(
                    self.headers.get(
                        "Content-Length",
                        "0"
                    )
                )

                body = (
                    self.rfile.read(length)
                    if length else b"{}"
                )

                payload = json.loads(
                    body.decode("utf-8")
                )

            except Exception as exc:

                self.send_json(
                    {
                        "ok": False,
                        "error": f"JSON inválido: {exc}"
                    },
                    400
                )

                return

            changes = payload.get(
                "properties",
                payload
            )

            if not isinstance(changes, dict) or not changes:

                self.send_json(
                    {
                        "ok": False,
                        "error": "Faltan propiedades que aplicar"
                    },
                    400
                )

                return

            if not camera_ready:

                self.send_json(
                    {
                        "ok": False,
                        "error": "La cámara no está lista"
                    },
                    409
                )

                return

            accepted = queue_properties(changes)

            self.send_json({
                "ok": bool(accepted),
                "accepted": accepted,
                "message": (
                    f"{len(accepted)} propiedad(es) en cola"
                    if accepted else "Ninguna propiedad válida"
                )
            })

            return


        self.send_response(
            404
        )

        self.end_headers()


# ============================================================
# SISTEMA MONITOR
# ============================================================

def system_monitor_loop():

    global running

    while running:

        update_system_metrics()

        time.sleep(
            0.5
        )


# ============================================================
# MAIN
# ============================================================

def main():

    global running

    running = True

    print()
    print(
        "=" * 70
    )

    print(
        "DEMA GE134GM + YOLO26-X"
    )

    print(
        "PyTorch + CUDA"
    )

    print(
        f"Model: {MODEL_PATH}"
    )

    print(
        f"Web: http://localhost:{PORT}"
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # CÁMARA
    # --------------------------------------------------------

    camera_thread = (
        threading.Thread(
            target=camera_loop,
            daemon=True
        )
    )

    camera_thread.start()

    # --------------------------------------------------------
    # YOLO
    # --------------------------------------------------------

    yolo_thread = (
        threading.Thread(
            target=yolo_loop,
            daemon=True
        )
    )

    yolo_thread.start()

    # --------------------------------------------------------
    # WEB
    # --------------------------------------------------------

    web_thread = (
        threading.Thread(
            target=web_loop,
            daemon=True
        )
    )

    web_thread.start()

    # --------------------------------------------------------
    # MONITOR
    # --------------------------------------------------------

    monitor_thread = (
        threading.Thread(
            target=system_monitor_loop,
            daemon=True
        )
    )

    monitor_thread.start()

    # --------------------------------------------------------
    # SERVER
    # --------------------------------------------------------

    server = ThreadingHTTPServer(
        (
            HOST,
            PORT
        ),
        CameraHandler
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print(
            "Deteniendo..."
        )

    finally:

        running = False

        server.server_close()

        print(
            "Servidor detenido."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()