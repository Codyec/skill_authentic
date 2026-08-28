# -*- coding: utf-8 -*-
"""
HT-GE134GM - PRUEBA MINIMA DE LECTURA INICIAL

Esta versión hace SOLO esto:

1. Inicializa la cámara.
2. Lee todas las propiedades UNA SOLA VEZ.
3. Guarda esa lectura en INITIAL_PROPERTIES.
4. Construye una página HTML estática con esos valores.
5. Sirve esa página.

NO:
- video
- FPS en vivo
- JavaScript
- polling
- refresh
- botones
- escritura de propiedades
- API de propiedades
"""

import ctypes
import json
import html
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import mvsdk
import cv2
import numpy as np


HOST = "0.0.0.0"
PORT = 8010

CAMERA_IP = "192.168.0.216"

camera_lock = threading.RLock()
apply_lock = threading.Lock()

hCamera = 0
capability = None

# ================================================================
# VARIABLE AUXILIAR: SE LLENA UNA SOLA VEZ.
# ================================================================
INITIAL_PROPERTIES = {}
running = True
latest_jpeg = None
latest_frame_lock = threading.RLock()
property_write_lock = threading.RLock()

# ----------------------------------------------------------------------
# FrameRate nativo
# ----------------------------------------------------------------------
# CameraSetFrameRate / CameraGetFrameRate no aparecen en mvsdk.py.
# Si la DLL MVCAMSDK_X64 las contiene, se pueden llamar directamente.
# Si no existen, el servicio simplemente las marca como no disponibles.
_native_frame_rate = None


def init_native_frame_rate():
    global _native_frame_rate

    try:
        sdk = mvsdk._sdk
        get_fn = getattr(sdk, "CameraGetFrameRate", None)
        set_fn = getattr(sdk, "CameraSetFrameRate", None)

        if get_fn is None or set_fn is None:
            return

        # CameraApi.h (SDK oficial) declara RateHZ como 'int' / 'int*',
        # no 'double'. En la ABI x64 de Windows, un argumento c_double
        # viaja por un registro de punto flotante (XMM), mientras que
        # la función compilada espera un int en un registro entero
        # (RDX) — con c_double, la DLL nunca recibe el valor real.
        get_fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        get_fn.restype = ctypes.c_int

        set_fn.argtypes = [ctypes.c_int, ctypes.c_int]
        set_fn.restype = ctypes.c_int

        _native_frame_rate = (get_fn, set_fn)
        print("[OK] DLL nativa expone CameraGetFrameRate/CameraSetFrameRate")
    except Exception as exc:
        print("[INFO] FrameRate nativo no disponible:", exc)


def native_get_frame_rate():
    if _native_frame_rate is None:
        return False, "NO_DISPONIBLE"

    get_fn, _ = _native_frame_rate
    value = ctypes.c_int()
    try:
        rc = get_fn(int(hCamera), ctypes.byref(value))
        if rc != 0:
            return False, f"SDK_ERROR_{rc}"
        return True, value.value
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def native_set_frame_rate(value):
    if _native_frame_rate is None:
        return False, "NO_DISPONIBLE"

    _, set_fn = _native_frame_rate
    try:
        rc = set_fn(int(hCamera), int(value))
        if rc != 0:
            return False, f"SDK_ERROR_{rc}"
        return True, rc
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------
# CameraGetStatisticResend nativo (tampoco está en mvsdk.py)
# ----------------------------------------------------------------------

_native_get_resend = None


def init_native_statistic_resend():
    global _native_get_resend

    try:
        sdk = mvsdk._sdk
        fn = getattr(sdk, "CameraGetStatisticResend", None)

        if fn is None:
            return

        fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int

        _native_get_resend = fn
        print("[OK] DLL nativa expone CameraGetStatisticResend")
    except Exception as exc:
        print("[INFO] CameraGetStatisticResend no disponible:", exc)


def native_get_resend_count():
    if _native_get_resend is None:
        return False, "NO_DISPONIBLE"

    value = ctypes.c_uint()
    try:
        rc = _native_get_resend(int(hCamera), ctypes.byref(value))
        if rc != 0:
            return False, f"SDK_ERROR_{rc}"
        return True, value.value
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------
# ESTADÍSTICAS EN VIVO (panel izquierdo, igual que la interfaz oficial)
# ----------------------------------------------------------------------

stats_lock = threading.RLock()

stats = {
    "capture_fps": 0.0,
    "frames_total": 0,
    "frames_captured": 0,
    "frames_lost": 0,
    "resend_count": None,
    "width": 0,
    "height": 0,
    "link_speed_mbps": None,
}


def detect_link_speed_mbps(camera_ip):
    """
    El SDK no reporta la velocidad de enlace — ese dato es del
    adaptador de red de Windows, no de la cámara (lo mismo que
    muestra la interfaz del fabricante en 'Información del
    dispositivo'). Lo pedimos vía PowerShell una sola vez al
    arrancar. Si falla por cualquier motivo, se deja en None y
    el panel muestra 'N/D' — nunca rompe el servicio.
    """
    try:
        import subprocess

        cmd = (
            "Get-NetIPAddress -IPAddress '"
            + camera_ip.rsplit(".", 1)[0]
            + ".*' -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty InterfaceAlias"
        )

        alias = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()

        if not alias:
            return None

        cmd2 = (
            "(Get-NetAdapter -Name '" + alias + "').LinkSpeed"
        )

        speed_text = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd2],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()

        # Viene como "1 Gbps" o "100 Mbps"
        if "Gbps" in speed_text:
            return float(speed_text.split()[0]) * 1000
        if "Mbps" in speed_text:
            return float(speed_text.split()[0])

        return None
    except Exception as exc:
        print("[INFO] No se pudo leer link speed:", exc)
        return None


# ----------------------------------------------------------------------
# Propiedades
# ----------------------------------------------------------------------

def property_definitions():
    return [
        {
            "id": "auto_exposure",
            "label": "Exposición Automática",
            "section": "ExposureControl",
            "type": "bool",
            "get": "CameraGetAeState",
            "set": "CameraSetAeState",
        },
        {
            "id": "exposure_us",
            "label": "Tiempo de Exposición",
            "section": "ExposureControl",
            "unit": "µs",
            "type": "float",
            "get": "CameraGetExposureTime",
            "set": "CameraSetExposureTime",
            "range": "CameraGetExposureTimeRange",
        },
        {
            "id": "analog_gain",
            "label": "Ganancia Analógica",
            "section": "ExposureControl",
            "unit": "",
            "type": "int",
            "get": "CameraGetAnalogGain",
            "set": "CameraSetAnalogGain",
            "range": "CameraGetAeAnalogGainRange",
        },
        {
            "id": "analog_gain_x",
            "label": "Ganancia Analógica X",
            "section": "ExposureControl",
            "unit": "",
            "type": "float",
            "get": "CameraGetAnalogGainX",
            "set": "CameraSetAnalogGainX",
            "range": "CameraGetAnalogGainXRange",
        },
        {
            "id": "gamma",
            "label": "Gamma",
            "section": "ImageFormatControl",
            "type": "int",
            "get": "CameraGetGamma",
            "set": "CameraSetGamma",
        },
        {
            "id": "contrast",
            "label": "Contraste",
            "section": "ImageFormatControl",
            "type": "int",
            "get": "CameraGetContrast",
            "set": "CameraSetContrast",
        },
        {
            "id": "saturation",
            "label": "Saturación",
            "section": "ImageFormatControl",
            "type": "int",
            "get": "CameraGetSaturation",
            "set": "CameraSetSaturation",
        },
        {
            "id": "mirror_h",
            "label": "Espejo Horizontal",
            "section": "ImageFormatControl",
            "type": "bool_index",
            "index": 0,
            "get": "CameraGetMirror",
            "set": "CameraSetMirror",
        },
        {
            "id": "mirror_v",
            "label": "Espejo Vertical",
            "section": "ImageFormatControl",
            "type": "bool_index",
            "index": 1,
            "get": "CameraGetMirror",
            "set": "CameraSetMirror",
        },
        {
            "id": "rotate",
            "label": "Rotación",
            "section": "ImageFormatControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "0°"},
                {"value": 1, "label": "90°"},
                {"value": 2, "label": "180°"},
                {"value": 3, "label": "270°"},
            ],
            "get": "CameraGetRotate",
            "set": "CameraSetRotate",
        },
        {
            "id": "inverse",
            "label": "Invertir Imagen",
            "section": "ImageFormatControl",
            "type": "bool",
            "get": "CameraGetInverse",
            "set": "CameraSetInverse",
        },
        {
            "id": "anti_flick",
            "label": "Anti Parpadeo",
            "section": "ExposureControl",
            "type": "bool",
            "get": "CameraGetAntiFlick",
            "set": "CameraSetAntiFlick",
        },
        {
            "id": "light_frequency",
            "label": "Frecuencia Anti Parpadeo",
            "section": "ExposureControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "50 Hz"},
                {"value": 1, "label": "60 Hz"},
            ],
            "get": "CameraGetLightFrequency",
            "set": "CameraSetLightFrequency",
        },
        {
            "id": "frame_speed",
            "label": "Velocidad de Cuadro",
            "section": "AcquisitionControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "Bajo"},
                {"value": 1, "label": "Normal"},
                {"value": 2, "label": "Alto"},
                {"value": 3, "label": "Súper"},
            ],
            "get": "CameraGetFrameSpeed",
            "set": "CameraSetFrameSpeed",
        },
        {
            "id": "trigger_mode",
            "label": "Modo de Disparo",
            "section": "TriggerControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "Continuo"},
                {"value": 1, "label": "Disparo por Software"},
                {"value": 2, "label": "Disparo por Hardware / Externo"},
            ],
            "get": "CameraGetTriggerMode",
            "set": "CameraSetTriggerMode",
        },
        {
            "id": "trigger_count",
            "label": "Cantidad de Disparos",
            "section": "TriggerControl",
            "type": "int",
            "get": "CameraGetTriggerCount",
            "set": "CameraSetTriggerCount",
        },
        {
            "id": "trigger_delay_us",
            "label": "Retardo de Disparo",
            "section": "TriggerControl",
            "unit": "µs",
            "type": "int",
            "get": "CameraGetTriggerDelayTime",
            "set": "CameraSetTriggerDelayTime",
        },
        {
            "id": "strobe_mode",
            "label": "Modo de Flash (Strobe)",
            "section": "DigitalIOControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "Sync automático con trigger"},
                {"value": 1, "label": "Sync manual (delay + pulso)"},
                {"value": 2, "label": "Siempre alto"},
                {"value": 3, "label": "Siempre bajo"},
            ],
            "get": "CameraGetStrobeMode",
            "set": "CameraSetStrobeMode",
        },
        {
            "id": "strobe_delay_us",
            "label": "Retardo de Flash",
            "section": "DigitalIOControl",
            "unit": "µs",
            "type": "int",
            "get": "CameraGetStrobeDelayTime",
            "set": "CameraSetStrobeDelayTime",
        },
        {
            "id": "parameter_mode",
            "label": "Modo de Carga de Parámetros",
            "section": "DeviceControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "Por modelo"},
                {"value": 1, "label": "Por nombre de dispositivo"},
                {"value": 2, "label": "Por número de serie"},
                {"value": 3, "label": "En el dispositivo (flash)"},
            ],
            "get": "CameraGetParameterMode",
            "set": "CameraSetParameterMode",
        },
        {
            "id": "trans_pack_len",
            "label": "Tamaño de Paquete de Red",
            "section": "GigEVisionControl",
            "type": "int",
            "get": "CameraGetTransPackLen",
            "set": "CameraSetTransPackLen",
        },
        {
            "id": "isp_processor",
            "label": "Procesador ISP",
            "section": "ImageFormatControl",
            "type": "enum",
            "options": [
                {"value": 0, "label": "Software (PC)"},
                {"value": 1, "label": "Hardware (en la cámara)"},
            ],
            "get": "CameraGetIspProcessor",
            "set": "CameraSetIspProcessor",
        },
        {
            "id": "black_level",
            "label": "Nivel de Negro",
            "section": "ImageFormatControl",
            "type": "int",
            "get": "CameraGetBlackLevel",
            "set": "CameraSetBlackLevel",
        },
        {
            "id": "white_level",
            "label": "Nivel de Blanco",
            "section": "ImageFormatControl",
            "type": "int",
            "get": "CameraGetWhiteLevel",
            "set": "CameraSetWhiteLevel",
        },
        {
            "id": "noise_filter",
            "label": "Filtro de Ruido",
            "section": "ImageFormatControl",
            "type": "bool",
            "get": "CameraGetNoiseFilterState",
            "set": "CameraSetNoiseFilter",
        },
        {
            "id": "acquisition_frame_rate",
            "label": "Frecuencia de Adquisición",
            "section": "AcquisitionControl",
            "unit": "FPS",
            "type": "float",
            "native": True,
        },
    ]


def read_property(p):
    if p.get("native"):
        ok, value = native_get_frame_rate()
        return {"ok": ok, "value": value}

    fn_name = p.get("get")
    fn = getattr(mvsdk, fn_name, None)

    if fn is None:
        return {"ok": False, "value": None, "error": "NO_EXISTE_EN_MVSDK"}

    try:
        if p["type"] == "bool_index":
            value = fn(hCamera, p["index"])
        else:
            value = fn(hCamera)

        result = {"ok": True, "value": value}

        if "range" in p:
            rf = getattr(mvsdk, p["range"], None)
            if rf:
                try:
                    result["range"] = rf(hCamera)
                except Exception:
                    pass

        return result
    except Exception as exc:
        return {"ok": False, "value": None, "error": f"{type(exc).__name__}: {exc}"}


def write_property(p, value):
    try:
        if p.get("native"):
            ok, result = native_set_frame_rate(float(value))
            if not ok:
                return {"ok": False, "error": result}
            return {"ok": True, "result": result}

        fn = getattr(mvsdk, p["set"], None)
        if fn is None:
            return {"ok": False, "error": "NO_EXISTE_EN_MVSDK"}

        typ = p["type"]

        if typ == "bool":
            value = 1 if bool(value) else 0
            rc = fn(hCamera, value)

        elif typ == "bool_index":
            value = 1 if bool(value) else 0
            rc = fn(hCamera, p["index"], value)

        elif typ == "int":
            rc = fn(hCamera, int(float(value)))

        elif typ == "float":
            rc = fn(hCamera, float(value))

        elif typ == "enum":
            rc = fn(hCamera, int(value))

        else:
            return {"ok": False, "error": f"Tipo no soportado: {typ}"}

        # El wrapper devuelve normalmente el código del SDK.
        if rc not in (None, 0):
            return {"ok": False, "error": f"SDK_ERROR_{rc}"}

        return {"ok": True, "result": rc}

    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def get_all_properties():
    data = {}
    with camera_lock:
        for p in property_definitions():
            data[p["id"]] = {
                "label": p["label"],
                "section": p["section"],
                "unit": p.get("unit", ""),
                "type": p["type"],
                "options": p.get("options"),
                **read_property(p),
            }
    return data


# ----------------------------------------------------------------------
# Cámara
# ----------------------------------------------------------------------

def init_camera():
    global hCamera, capability, camera_error

    devs = mvsdk.CameraEnumerateDevice()
    if not devs:
        raise RuntimeError("No se encontró ninguna cámara MVSDK")

    selected = devs[0]

    print("Cámara encontrada:")
    print("  Friendly:", selected.GetFriendlyName())
    print("  Port:", selected.GetPortType())

    hCamera = mvsdk.CameraInit(selected, -1, -1)
    capability = mvsdk.CameraGetCapability(hCamera)

    mono = bool(capability.sIspCapacity.bMonoSensor)

    if mono:
        mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_MONO8)
        channels = 1
    else:
        mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
        channels = 3

    # Continuo.
    mvsdk.CameraSetTriggerMode(hCamera, 0)

    # NO cambiamos exposición ni FPS al arrancar.
    # Se conserva la configuración actual de la cámara.

    mvsdk.CameraPlay(hCamera)

    frame_buffer_size = (
        capability.sResolutionRange.iWidthMax *
        capability.sResolutionRange.iHeightMax *
        channels
    )

    p_frame_buffer = mvsdk.CameraAlignMalloc(frame_buffer_size, 16)

    init_native_frame_rate()

    print("Cámara inicializada.")
    print("Resolución máxima:",
          capability.sResolutionRange.iWidthMax,
          "x",
          capability.sResolutionRange.iHeightMax)

    return p_frame_buffer, channels




# ================================================================
# HTML ESTATICO
# ================================================================

def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def build_controls(snapshot):
    """Render INITIAL_PROPERTIES once. No camera reads from the browser."""
    sections = {}

    for prop_id, p in snapshot.items():
        sections.setdefault(p.get("section", "Properties"), []).append(
            (prop_id, p)
        )

    out = []

    for section, props in sections.items():
        out.append(
            '<div class="section">' + esc(section) + '</div>'
        )

        for prop_id, p in props:
            label = esc(p.get("label", prop_id))
            value = p.get("value", "")
            unit = esc(p.get("unit", ""))
            pid = esc(prop_id)
            initial = esc(value)

            rng = p.get("range")
            range_text = ""
            if isinstance(rng, (list, tuple)):
                range_text = (
                    " · Range: " +
                    " / ".join(esc(x) for x in rng)
                )

            if not p.get("ok", False):
                control = (
                    '<input data-prop="' + pid +
                    '" data-initial="' + initial +
                    '" disabled value="' +
                    esc(p.get("error", "No disponible")) + '">'
                )

            elif p.get("type") in ("bool", "bool_index"):
                try:
                    current = int(float(value))
                except Exception:
                    current = 0

                control = (
                    '<select data-prop="' + pid +
                    '" data-initial="' + initial + '">'
                    '<option value="0"' +
                    (' selected' if current == 0 else '') +
                    '>OFF</option>'
                    '<option value="1"' +
                    (' selected' if current != 0 else '') +
                    '>ON</option>'
                    '</select>'
                )

            elif p.get("type") == "enum" and p.get("options"):
                opts = []

                for option in p["options"]:
                    ov = option.get("value")

                    try:
                        selected = (
                            ' selected'
                            if float(ov) == float(value)
                            else ''
                        )
                    except Exception:
                        selected = (
                            ' selected'
                            if str(ov) == str(value)
                            else ''
                        )

                    opts.append(
                        '<option value="' + esc(ov) + '"' +
                        selected + '>' +
                        esc(option.get("label", ov)) +
                        '</option>'
                    )

                control = (
                    '<select data-prop="' + pid +
                    '" data-initial="' + initial + '">' +
                    ''.join(opts) +
                    '</select>'
                )

            else:
                step = "any" if p.get("type") == "float" else "1"

                control = (
                    '<input data-prop="' + pid +
                    '" data-initial="' + initial +
                    '" type="number" step="' + step +
                    '" value="' + esc(value) + '">'
                )

            out.append(
                '<div class="property">'
                '<div class="name">'
                '<b>' + label + '</b>'
                '<small>' + unit + range_text + '</small>'
                '</div>'
                '<div class="control">' + control + '</div>'
                '</div>'
            )

    return ''.join(out)


HTML = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>HT-GE134GM - Video + propiedades</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101216;color:#eee;font-family:Arial}
header{padding:14px 20px;background:#191c21;border-bottom:1px solid #30343b}
header h1{margin:0;font-size:19px}header p{margin:5px 0;color:#9da5af}
.layout{display:flex;height:calc(100vh - 61px)}
.stats{flex:0 0 260px;min-width:200px;max-width:420px;padding:14px;overflow:auto;background:#15171c;font-size:13px}
.stats h2{font-size:13px;color:#9da5af;text-transform:uppercase;margin:16px 0 6px 0;letter-spacing:.04em}
.stats h2:first-child{margin-top:0}
.stat-row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #22252b}
.stat-row span:first-child{color:#9299a4}
.stat-row span:last-child{font-weight:bold;font-variant-numeric:tabular-nums}
.viewer{flex:1 1 auto;min-width:200px;background:#050608;display:flex;align-items:center;justify-content:center;overflow:hidden}
.viewer img{max-width:100%;max-height:100%;object-fit:contain}
.panel{flex:0 0 520px;min-width:340px;max-width:900px;padding:16px;overflow:auto;background:#15171c}
.info{padding:12px;background:#202329;border:1px solid #383d46;border-radius:7px}
.info b{color:#7fa2ff}.section{margin-top:18px;padding:9px 11px;background:#292c34;border-left:3px solid #628cff;font-weight:bold}
.property{display:grid;grid-template-columns:1fr 210px;gap:18px;align-items:center;border-bottom:1px solid #30343b;padding:10px 4px}
.name b{display:block}.name small{display:block;color:#9299a4;font-size:11px;margin-top:4px}
.control select,.control input{width:100%;padding:8px;background:#111318;color:#eee;border:1px solid #555b65;border-radius:5px}
.resizer{flex:0 0 6px;background:#20232a;cursor:col-resize;position:relative}
.resizer:hover,.resizer.active{background:#4a76e8}
</style>
</head>
<body>
<header style="display:flex;align-items:center;gap:14px;padding:10px 20px;background:#191c21;border-bottom:1px solid #30343b">
  <img src="/logo.png" alt="DEMA" style="height:36px" onerror="this.style.display='none'">
  <h1 style="margin:0;font-size:18px">Configuración de Parámetros de la Cámara</h1>
</header>
<div class="layout" id="layout">
<div class="stats" id="statsPanel">
  <h2>Cámara</h2>
  <div class="stat-row"><span>Resolución</span><span id="s_resolution">--</span></div>
  <div class="stat-row"><span>FPS de Adquisición</span><span id="s_fps">--</span></div>
  <div class="stat-row"><span>Cuadros (total)</span><span id="s_total">--</span></div>
  <div class="stat-row"><span>Cuadros (válidos)</span><span id="s_captured">--</span></div>
  <div class="stat-row"><span>Perdidos</span><span id="s_lost">--</span></div>
  <div class="stat-row"><span>Reenvíos</span><span id="s_resend">--</span></div>

  <h2>Red</h2>
  <div class="stat-row"><span>Velocidad de Enlace</span><span id="s_link">--</span></div>
  <div class="stat-row"><span>IP Cámara</span><span id="s_ip">__CAMERA_IP__</span></div>
</div>
<div class="resizer" id="resizer1"></div>
<div class="viewer" id="viewerPanel"><img src="/stream.mjpg" alt="Video en vivo"></div>
<div class="resizer" id="resizer2"></div>
<div class="panel" id="propsPanel">
__PROPERTIES__

<div style="margin-top:22px;padding:14px;background:#202329;border:1px solid #383d46;border-radius:7px">
  <div style="font-weight:bold;margin-bottom:6px">Estado</div>
  <div id="applyResult" style="color:#9ea5af;font-size:12px">Los cambios se aplican al instante — no hace falta ningún botón.</div>
</div>

<div style="margin-top:14px;padding:14px;background:#202329;border:1px solid #383d46;border-radius:7px">
  <button type="button" id="saveConfigBtn" style="width:100%;padding:12px;border:0;border-radius:6px;background:#3ea35b;color:white;font-weight:bold;cursor:pointer">
    GUARDAR CONFIGURACIÓN ACTUAL
  </button>
  <div id="saveResult" style="margin-top:10px;color:#9ea5af;font-size:12px">Lee el valor actual de cada propiedad (no la lectura congelada del arranque) y lo guarda en camera_config.json, en la misma carpeta del script.</div>
</div>
<script>
// ------------------------------------------------------------
// APLICAR AL INSTANTE (sin botón) — igual que la interfaz oficial:
// cada campo se manda a la cámara apenas cambia.
// ------------------------------------------------------------

async function applyOne(id, value, el) {
  const result = document.getElementById('applyResult');
  result.textContent = 'Aplicando ' + id + ' = ' + value + ' ...';

  try {
    const r = await fetch('/control/apply', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      cache: 'no-store',
      body: JSON.stringify({property: id, value: value})
    });

    const data = await r.json();
    const item = Array.isArray(data.results) ? data.results[0] : null;

    if (data.ok && item && item.ok) {
      el.dataset.applied = value;
      result.textContent = id + ' -> ' + item.verify_message;
    } else {
      const msg = item ? item.set_message : (data.error || 'error');
      result.textContent = 'ERROR aplicando ' + id + ': ' + msg;
    }
  } catch (e) {
    result.textContent = 'Error de red aplicando ' + id + ': ' + e;
  }
}

document.addEventListener('DOMContentLoaded', function () {

  document.querySelectorAll('[data-prop]').forEach(el => {
    el.dataset.applied = el.value;

    el.addEventListener('change', () => {
      if (el.disabled) return;
      applyOne(el.dataset.prop, el.value, el);
    });
  });

  // --------------------------------------------------------
  // CAMPOS CONDICIONALES
  // --------------------------------------------------------
  // Igual que en la interfaz oficial: con AutoExposure en
  // Continuous, ExposureTime lo calcula la cámara sola (se
  // deshabilita); con AutoExposure en Off, es al revés. Anti
  // Flick solo tiene sentido cuando el auto-exposure está
  // corriendo, así que sigue la misma regla.

  const autoExposure = document.querySelector('[data-prop="auto_exposure"]');
  const exposureTime = document.querySelector('[data-prop="exposure_us"]');
  const antiFlick = document.querySelector('[data-prop="anti_flick"]');

  function syncExposureFields() {
    if (!autoExposure) return;

    const isAuto = autoExposure.value === '1';

    if (exposureTime) exposureTime.disabled = isAuto;
    if (antiFlick) antiFlick.disabled = !isAuto;
  }

  if (autoExposure) {
    autoExposure.addEventListener('change', syncExposureFields);
    syncExposureFields();
  }
});

// ------------------------------------------------------------
// GUARDAR CONFIGURACIÓN ACTUAL
// ------------------------------------------------------------

document.getElementById('saveConfigBtn').addEventListener('click', async () => {
  const result = document.getElementById('saveResult');
  result.textContent = 'Leyendo cámara y guardando...';
  result.style.color = '#9ea5af';

  try {
    const r = await fetch('/control/save_config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      cache: 'no-store',
      body: JSON.stringify({})
    });

    const data = await r.json();

    if (data.ok) {
      result.style.color = '#5fd67f';
      result.textContent =
        '✔ Configuración guardada y verificada — ' +
        data.properties_total + '/' + data.properties_total +
        ' propiedades leídas OK, archivo confirmado en disco (' +
        data.path + ', ' + data.config.saved_at + ').';
    } else {
      result.style.color = '#e05c5c';
      let msg = '✘ Guardado CON PROBLEMAS. ';

      if (!data.all_reads_ok) {
        msg += 'No se pudieron leer: ' +
          data.properties_failed.join(', ') + '. ';
      }
      if (!data.file_write_verified) {
        msg += 'El archivo no quedó verificado en disco (' +
          data.verify_error + ').';
      }

      result.textContent = msg;
    }
  } catch (e) {
    result.style.color = '#e05c5c';
    result.textContent = '✘ Error de red guardando: ' + e;
  }
});

// ------------------------------------------------------------
// ESTADÍSTICAS EN VIVO (panel izquierdo)
// ------------------------------------------------------------

async function pollStats() {
  try {
    const r = await fetch('/status', {cache: 'no-store'});
    const s = await r.json();

    document.getElementById('s_resolution').textContent =
      (s.width && s.height) ? (s.width + ' x ' + s.height) : '--';

    document.getElementById('s_fps').textContent =
      s.capture_fps.toFixed(2) + ' FPS';

    document.getElementById('s_total').textContent = s.frames_total;
    document.getElementById('s_captured').textContent = s.frames_captured;
    document.getElementById('s_lost').textContent = s.frames_lost;

    document.getElementById('s_resend').textContent =
      (s.resend_count === null) ? 'N/D' : s.resend_count;

    document.getElementById('s_link').textContent =
      (s.link_speed_mbps === null)
        ? 'N/D'
        : (s.link_speed_mbps >= 1000
            ? (s.link_speed_mbps / 1000) + ' Gbps'
            : s.link_speed_mbps + ' Mbps');

  } catch (e) {
    // Silencioso: si el status falla una vez, se reintenta solo.
  }

  setTimeout(pollStats, 1000);
}

pollStats();

// ------------------------------------------------------------
// PANELES REDIMENSIONABLES (arrastrar los separadores)
// ------------------------------------------------------------

function makeResizable(resizerEl, targetEl, edge, storageKey) {
  const saved = localStorage.getItem(storageKey);
  if (saved) targetEl.style.flexBasis = saved + 'px';

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  resizerEl.addEventListener('mousedown', (e) => {
    dragging = true;
    startX = e.clientX;
    startWidth = targetEl.getBoundingClientRect().width;
    resizerEl.classList.add('active');
    document.body.style.userSelect = 'none';
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;

    const delta = e.clientX - startX;
    const newWidth = edge === 'right' ? startWidth + delta : startWidth - delta;

    targetEl.style.flexBasis = newWidth + 'px';
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    resizerEl.classList.remove('active');
    document.body.style.userSelect = '';
    localStorage.setItem(
      storageKey,
      Math.round(targetEl.getBoundingClientRect().width)
    );
  });
}

makeResizable(
  document.getElementById('resizer1'),
  document.getElementById('statsPanel'),
  'right',
  'dema_stats_width'
);

makeResizable(
  document.getElementById('resizer2'),
  document.getElementById('propsPanel'),
  'left',
  'dema_props_width'
);
</script>

</div>
</div>
</body>
</html>
"""




def camera_capture_loop(p_frame_buffer, channels):
    """
    Video thread.

    Every SDK operation involving the camera is protected by camera_lock.
    Therefore /control/apply can stop/reconfigure/play the camera without
    racing against CameraGetImageBuffer/CameraImageProcess.
    """
    global latest_jpeg, running

    print("[VIDEO] Captura iniciada.")

    frame_count = 0
    fps_window_start = time.perf_counter()

    while running:
        raw = None

        try:
            with camera_lock:
                raw, head = mvsdk.CameraGetImageBuffer(hCamera, 2000)

                mvsdk.CameraImageProcess(
                    hCamera,
                    raw,
                    p_frame_buffer,
                    head
                )

                mvsdk.CameraReleaseImageBuffer(
                    hCamera,
                    raw
                )
                raw = None

                mvsdk.CameraFlipFrameBuffer(
                    p_frame_buffer,
                    head,
                    1
                )

                data = (
                    mvsdk.c_ubyte *
                    (head.iWidth * head.iHeight)
                ).from_address(p_frame_buffer)

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

            # JPEG encoding does not touch the camera, so do it outside
            # the camera lock.
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, 85]
            )

            if ok:
                with latest_frame_lock:
                    latest_jpeg = encoded.tobytes()

            # ------------------------------------------------
            # ESTADÍSTICAS (una vez por segundo, no por frame)
            # ------------------------------------------------
            frame_count += 1
            now = time.perf_counter()
            elapsed = now - fps_window_start

            if elapsed >= 1.0:
                fps = frame_count / elapsed

                frame_stat = None
                try:
                    with camera_lock:
                        frame_stat = mvsdk.CameraGetFrameStatistic(
                            hCamera
                        )
                except Exception:
                    pass

                resend_ok, resend_value = native_get_resend_count()

                with stats_lock:
                    stats["capture_fps"] = fps
                    stats["width"] = head.iWidth
                    stats["height"] = head.iHeight

                    if frame_stat is not None:
                        stats["frames_total"] = frame_stat.iTotal
                        stats["frames_captured"] = frame_stat.iCapture
                        stats["frames_lost"] = frame_stat.iLost

                    if resend_ok:
                        stats["resend_count"] = resend_value

                frame_count = 0
                fps_window_start = now

        except Exception as exc:
            msg = repr(exc)
            last = getattr(camera_capture_loop, "_last_video_error", None)

            if msg != last:
                print("[VIDEO] Error:", msg)
                camera_capture_loop._last_video_error = msg

            time.sleep(0.05)

        finally:
            if raw is not None:
                try:
                    with camera_lock:
                        mvsdk.CameraReleaseImageBuffer(
                            hCamera,
                            raw
                        )
                except Exception:
                    pass

def apply_property(prop_id, value):
    """Execute one setter. Does not read the camera."""
    p = str(prop_id).strip().lower()
    v = str(value).strip()

    if p in ("mirror_h", "mirror_horizontal"):
        fn = getattr(mvsdk, "CameraSetMirror", None)
        if fn is None:
            return False, "CameraSetMirror no existe en mvsdk"

        x = 1 if v.lower() in ("1", "on", "true", "yes") else 0
        rc = fn(hCamera, 0, x)

        return (
            int(rc) == 0,
            f"CameraSetMirror(horizontal,{x}) -> {rc}"
        )

    if p in ("mirror_v", "mirror_vertical"):
        fn = getattr(mvsdk, "CameraSetMirror", None)
        if fn is None:
            return False, "CameraSetMirror no existe en mvsdk"

        x = 1 if v.lower() in ("1", "on", "true", "yes") else 0
        rc = fn(hCamera, 1, x)

        return (
            int(rc) == 0,
            f"CameraSetMirror(vertical,{x}) -> {rc}"
        )

    setters = {
        "auto_exposure": ("CameraSetAeState", "bool"),
        "exposure_us": ("CameraSetExposureTime", "float"),
        "exposure_time": ("CameraSetExposureTime", "float"),
        "analog_gain": ("CameraSetAnalogGain", "int"),
        "analog_gain_x": ("CameraSetAnalogGainX", "float"),
        "gamma": ("CameraSetGamma", "int"),
        "contrast": ("CameraSetContrast", "int"),
        "saturation": ("CameraSetSaturation", "int"),
        "anti_flick": ("CameraSetAntiFlick", "bool"),
        "light_frequency": ("CameraSetLightFrequency", "int"),
        "rotate": ("CameraSetRotate", "int"),
        "inverse": ("CameraSetInverse", "bool"),
        "noise_filter": ("CameraSetNoiseFilter", "bool"),
        "frame_speed": ("CameraSetFrameSpeed", "int"),
        "trigger_mode": ("CameraSetTriggerMode", "int"),
        "trigger_count": ("CameraSetTriggerCount", "int"),
        "trigger_delay_us": ("CameraSetTriggerDelayTime", "int"),
        "trigger_delay": ("CameraSetTriggerDelayTime", "int"),
        "strobe_mode": ("CameraSetStrobeMode", "int"),
        "strobe_delay_us": ("CameraSetStrobeDelayTime", "int"),
        "strobe_delay": ("CameraSetStrobeDelayTime", "int"),
        "parameter_mode": ("CameraSetParameterMode", "int"),
        "trans_pack_len": ("CameraSetTransPackLen", "int"),
        "isp_processor": ("CameraSetIspProcessor", "int"),
        "black_level": ("CameraSetBlackLevel", "int"),
        "white_level": ("CameraSetWhiteLevel", "int"),
    }

    if p in ("acquisition_frame_rate", "acquisition frame rate"):
        ok, result = native_set_frame_rate(float(v))
        return ok, f"CameraSetFrameRate({v}) -> {result}"

    item = setters.get(p)

    if item is None:
        return False, f"Setter no configurado para '{prop_id}'"

    fn_name, typ = item
    fn = getattr(mvsdk, fn_name, None)

    if fn is None:
        return False, f"{fn_name} no existe en mvsdk"

    try:
        if typ == "bool":
            x = (
                1
                if v.lower() in ("1", "on", "true", "yes")
                else 0
            )
        elif typ == "int":
            x = int(float(v))
        else:
            x = float(v)

        rc = fn(hCamera, x)

        if rc not in (None, 0):
            return False, f"{fn_name}({x}) -> {rc}"

        return True, f"{fn_name}({x}) -> {rc}"

    except Exception as exc:
        return False, (
            f"{fn_name}: {type(exc).__name__}: {exc}"
        )


CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


def _json_safe(value):
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    try:
        return str(value)
    except Exception:
        return repr(value)


def save_config(filename=None):
    """
    Lee el valor ACTUAL de cada propiedad (no INITIAL_PROPERTIES,
    que es la foto congelada del arranque) y lo guarda en un JSON
    en la misma carpeta del script. Ese archivo es el que después
    se puede leer para reaplicar esta misma configuración.

    Valida DOS cosas por separado, y las reporta por separado:
      1. Que cada propiedad se haya podido LEER de la cámara.
      2. Que el archivo haya quedado escrito en disco correctamente
         (se relee y se compara contra lo que se pretendía guardar).
    """
    if not filename:
        filename = "camera_config.json"

    path = os.path.join(CONFIG_DIR, filename)

    definitions = property_definitions()
    properties = {}
    failed_reads = []

    with camera_lock:
        for p in definitions:
            read = read_property(p)
            ok = bool(read.get("ok"))

            properties[p["id"]] = {
                "label": p.get("label"),
                "section": p.get("section"),
                "type": p.get("type"),
                "value": _json_safe(read.get("value")),
                "ok": ok,
            }

            if not ok:
                failed_reads.append(p["id"])

    config = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "camera_ip": CAMERA_IP,
        "properties": properties,
    }

    # ------------------------------------------------------------
    # ESCRIBIR Y VERIFICAR CONTRA DISCO (no confiar en que
    # "no tiró excepción" signifique "quedó bien guardado")
    # ------------------------------------------------------------

    write_verified = False
    verify_error = None

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        with open(path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)

        same_count = (
            len(reloaded.get("properties", {})) == len(properties)
        )
        same_timestamp = (
            reloaded.get("saved_at") == config["saved_at"]
        )

        write_verified = same_count and same_timestamp

        if not write_verified:
            verify_error = (
                "El archivo releído no coincide con lo que se "
                "intentó guardar (count={}, timestamp={})".format(
                    same_count, same_timestamp
                )
            )

    except Exception as exc:
        verify_error = f"{type(exc).__name__}: {exc}"

    all_reads_ok = len(failed_reads) == 0
    success = all_reads_ok and write_verified

    if success:
        print(f"[CONFIG] Guardado y verificado en {path}")
    else:
        print(
            f"[CONFIG] Guardado CON PROBLEMAS en {path} — "
            f"lecturas fallidas: {failed_reads}, "
            f"verify_error: {verify_error}"
        )

    result = {
        "success": success,
        "path": path,
        "properties_total": len(properties),
        "properties_failed": failed_reads,
        "all_reads_ok": all_reads_ok,
        "file_write_verified": write_verified,
        "verify_error": verify_error,
        "config": config,
    }

    return success, result


def apply_properties(payload):
    """
    Critical behavior:

    1. Receives ONLY changed properties from the browser.
    2. Takes camera_lock, so video cannot call the SDK concurrently.
    3. Stops acquisition.
    4. Writes only requested properties.
    5. Reads back each written property.
    6. Starts acquisition again.
    """
    if isinstance(payload.get("properties"), dict):
        items = list(payload["properties"].items())
    elif "property" in payload:
        items = [(payload["property"], payload.get("value"))]
    else:
        return False, {"error": "Falta property/value o properties"}

    if not items:
        return True, {
            "results": [],
            "message": "No hay cambios"
        }

    with apply_lock:
        with camera_lock:
            stop_rc = None
            play_rc = None
            results = []
            all_ok = True

            # The video thread cannot enter the SDK while this lock is held.
            try:
                stop_rc = mvsdk.CameraStop(hCamera)

                if stop_rc not in (None, 0):
                    return False, {
                        "results": [],
                        "error": f"CameraStop -> {stop_rc}"
                    }

                print(
                    "[APPLY] Adquisición detenida. "
                    f"Aplicando {len(items)} propiedad(es)."
                )

                # Property definitions are static metadata; using them here
                # does NOT perform a camera read.
                definitions = {
                    p["id"]: p
                    for p in property_definitions()
                }

                for prop_id, value in items:
                    ok_set, set_message = apply_property(
                        prop_id,
                        value
                    )

                    verify = None
                    verify_ok = False
                    verify_message = "No verificado"

                    if ok_set:
                        pdef = definitions.get(str(prop_id))

                        if pdef is None:
                            verify_message = (
                                "Propiedad no existe en "
                                "property_definitions"
                            )
                        else:
                            # READ-BACK: this is the first camera read
                            # after startup, and it happens ONLY after Apply.
                            read = read_property(pdef)
                            verify = read.get("value")
                            verify_ok = bool(read.get("ok"))

                            if verify_ok:
                                verify_message = (
                                    f"CameraGet -> {verify}"
                                )
                            else:
                                verify_message = (
                                    "CameraGet ERROR: " +
                                    str(read.get("error"))
                                )

                    final_ok = bool(ok_set and verify_ok)
                    all_ok = all_ok and final_ok

                    results.append({
                        "property": prop_id,
                        "requested": value,
                        "set_ok": bool(ok_set),
                        "set_message": set_message,
                        "verified_value": verify,
                        "verify_ok": bool(verify_ok),
                        "verify_message": verify_message,
                        "ok": final_ok
                    })

            finally:
                # Always restart acquisition after the transaction.
                try:
                    play_rc = mvsdk.CameraPlay(hCamera)
                except Exception as exc:
                    play_rc = (
                        f"{type(exc).__name__}: {exc}"
                    )

            if play_rc not in (None, 0):
                all_ok = False

            print(
                "[APPLY] Adquisición reanudada. "
                f"CameraPlay -> {play_rc}"
            )

            return all_ok, {
                "results": results,
                "camera_stop": stop_rc,
                "camera_play": play_rc
            }

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            page = (
                HTML
                .replace("__PROPERTIES__", build_controls(INITIAL_PROPERTIES))
                .replace("__CAMERA_IP__", CAMERA_IP)
            )

            raw = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/stream.mjpg":
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control","no-cache")
            self.end_headers()

            try:
                while running:
                    with latest_frame_lock:
                        jpg = latest_jpeg

                    if jpg:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                        )
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")

                    time.sleep(0.03)

            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/logo.png":
            logo_path = os.path.join(CONFIG_DIR, "Logo_DEMA.png")

            if os.path.isfile(logo_path):
                try:
                    with open(logo_path, "rb") as f:
                        data = f.read()

                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception:
                    pass

            self.send_response(404)
            self.end_headers()
            return

        if path == "/status":
            with stats_lock:
                body = json.dumps(stats, ensure_ascii=False).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        raw=b"404"
        self.send_response(404)
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/control/save_config":
            try:
                length = int(self.headers.get("Content-Length", "0"))

                filename = None

                if length:
                    request_body = self.rfile.read(length)
                    try:
                        payload = json.loads(request_body.decode("utf-8"))
                        filename = payload.get("filename")
                    except Exception:
                        pass

                success, result = save_config(filename)

                body = json.dumps(
                    {"ok": success, **result},
                    ensure_ascii=False
                ).encode("utf-8")

                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            except Exception as exc:
                body = json.dumps(
                    {"ok": False, "error": repr(exc)},
                    ensure_ascii=False
                ).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            return

        if path != "/control/apply":
            body = json.dumps({"ok": False, "error": "Endpoint not found"}).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(length)
            payload = json.loads(request_body.decode("utf-8"))
            ok, result = apply_properties(payload)

            body = json.dumps(
                {"ok": bool(ok), **result},
                ensure_ascii=False
            ).encode("utf-8")

            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        except Exception as exc:
            body = json.dumps(
                {"ok": False, "error": repr(exc)},
                ensure_ascii=False
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)



def main():
    global hCamera, capability, INITIAL_PROPERTIES, LOAD_ID

    print("=" * 72)
    print("HT-GE134GM - PRUEBA MINIMA: SOLO LECTURA INICIAL")
    print("=" * 72)

    try:
        # ------------------------------------------------------------
        # 1. Enumerar e inicializar. NO arrancamos adquisición.
        # ------------------------------------------------------------
        devs = mvsdk.CameraEnumerateDevice()

        if not devs:
            raise RuntimeError("No se encontró ninguna cámara")

        selected = devs[0]

        print("Cámara encontrada:")
        print("  Friendly:", selected.GetFriendlyName())
        print("  Port:", selected.GetPortType())

        hCamera = mvsdk.CameraInit(selected, -1, -1)
        capability = mvsdk.CameraGetCapability(hCamera)

        print("Cámara inicializada para lectura.")
        print("NO se cambia Trigger.")
        print("NO se cambia Exposure.")
        print("NO se cambia Gain.")
        print("NO se cambia FrameRate.")
        print("NO se inicia adquisición.")

        # La DLL nativa se prepara solamente para poder leer FrameRate
        # si esa función está disponible. No se escribe nada.
        init_native_frame_rate()
        init_native_statistic_resend()

        link_speed = detect_link_speed_mbps(CAMERA_IP)
        with stats_lock:
            stats["link_speed_mbps"] = link_speed
        print("Link speed detectado:", link_speed, "Mbps")

        # ------------------------------------------------------------
        # 2. LECTURA ÚNICA
        # ------------------------------------------------------------
        print()
        print(">>> LEYENDO PROPIEDADES UNA SOLA VEZ <<<")

        with camera_lock:
            INITIAL_PROPERTIES = get_all_properties()

        print(
            f"[OK] {len(INITIAL_PROPERTIES)} propiedades guardadas "
            "en INITIAL_PROPERTIES"
        )

        # ------------------------------------------------------------
        # 3. Snapshot de consola
        # ------------------------------------------------------------
        print()
        print("SNAPSHOT INICIAL")
        print("-" * 72)

        for prop_id, prop in INITIAL_PROPERTIES.items():
            print(
                f"{prop.get('label', prop_id):35s} = "
                f"{prop.get('value')}"
            )

        print("-" * 72)

        LOAD_ID = str(int(time.time() * 1000))

        # ------------------------------------------------------------
        # 4. Servidor completamente estático
        # ------------------------------------------------------------
        # ------------------------------------------------------------
        # VIDEO: se inicia después de congelar INITIAL_PROPERTIES.
        # El hilo de video NO vuelve a leer propiedades.
        # ------------------------------------------------------------
        mono = bool(capability.sIspCapacity.bMonoSensor)

        if mono:
            mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_MONO8)
            channels = 1
        else:
            mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
            channels = 3

        mvsdk.CameraSetTriggerMode(hCamera, 0)
        mvsdk.CameraPlay(hCamera)

        frame_buffer_size = (
            capability.sResolutionRange.iWidthMax *
            capability.sResolutionRange.iHeightMax *
            channels
        )
        p_frame_buffer = mvsdk.CameraAlignMalloc(frame_buffer_size, 16)

        threading.Thread(
            target=camera_capture_loop,
            args=(p_frame_buffer, channels),
            daemon=True
        ).start()

        server = ThreadingHTTPServer((HOST, PORT), Handler)

        print()
        print("SERVICIO:")
        print(f"  http://127.0.0.1:{PORT}")
        print()
        print("ESTA PRUEBA TIENE:")
        print("  OK video en vivo")
        print("  X FPS en vivo")
        print("  X JavaScript")
        print("  X polling")
        print("  X refresh")
        print("  X botones")
        print("  X escritura")
        print("  X lectura de propiedades después del inicio")
        print()
        print("Las propiedades están congeladas en INITIAL_PROPERTIES.")
        print()
        print("Presiona CTRL+C para detener.")

        server.serve_forever()

    except KeyboardInterrupt:
        print("\nDeteniendo...")

    finally:
        running = False
        try:
            if hCamera:
                mvsdk.CameraUnInit(hCamera)
        except Exception:
            pass


if __name__ == "__main__":
    main()