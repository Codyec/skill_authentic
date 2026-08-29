# -*- coding: utf-8 -*-
"""
Gestor de propiedades de la cámara MindVision para camera-yolo.

Portado de services/camera-service/legacy/camera_service_FUNCIONAL_FINAL.py,
sin estado global: cada función recibe el módulo `mvsdk` ya cargado y el
handle `h_camera`. La propiedad `acquisition_frame_rate` es "nativa" (no hay
getter/setter en mvsdk.py) y se atiende con los callables native_get/native_set
que pasa main.py.

camera_config.json es la FUENTE DE VERDAD: al arrancar se carga y se aplica a
la cámara; el panel edita sobre él; al aplicar se escribe cámara + archivo.
Esquema del JSON (idéntico al del servicio legacy):

    {
      "saved_at": "YYYY-mm-dd HH:MM:SS",
      "camera_ip": "192.168.0.216",
      "properties": {
        "<id>": {"label": ..., "section": ..., "type": ..., "value": ..., "ok": true}
      }
    }
"""

import json
import os
import time


# ======================================================================
# DEFINICIONES
# ======================================================================

def property_definitions():
    """Metadatos estáticos. NO lee la cámara."""
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
            "type": "int",
            "get": "CameraGetAnalogGain",
            "set": "CameraSetAnalogGain",
            "range": "CameraGetAeAnalogGainRange",
        },
        {
            "id": "analog_gain_x",
            "label": "Ganancia Analógica X",
            "section": "ExposureControl",
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


def definitions_by_id():
    return {p["id"]: p for p in property_definitions()}


# ======================================================================
# LECTURA / ESCRITURA
# ======================================================================

def _json_safe(value):
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    try:
        return str(value)
    except Exception:
        return repr(value)


def read_property(mvsdk, h_camera, p, native_get=None):
    """Lee una propiedad. Devuelve {"ok", "value", ("range"), ("error")}."""
    if p.get("native"):
        if native_get is None:
            return {"ok": False, "value": None, "error": "NATIVO_NO_DISPONIBLE"}
        try:
            ok, value = native_get()
            return {"ok": bool(ok), "value": value if ok else None,
                    "error": None if ok else str(value)}
        except Exception as exc:
            return {"ok": False, "value": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    fn = getattr(mvsdk, p.get("get", ""), None)
    if fn is None:
        return {"ok": False, "value": None, "error": "NO_EXISTE_EN_MVSDK"}

    try:
        if p["type"] == "bool_index":
            value = fn(h_camera, p["index"])
        else:
            value = fn(h_camera)

        result = {"ok": True, "value": value}

        if "range" in p:
            rf = getattr(mvsdk, p["range"], None)
            if rf:
                try:
                    result["range"] = rf(h_camera)
                except Exception:
                    pass

        return result
    except Exception as exc:
        return {"ok": False, "value": None,
                "error": f"{type(exc).__name__}: {exc}"}


def _coerce(value, typ):
    if typ in ("bool", "bool_index"):
        if isinstance(value, str):
            return 1 if value.strip().lower() in ("1", "on", "true", "yes") else 0
        return 1 if bool(value) and value not in (0, "0") else 0
    if typ == "int" or typ == "enum":
        return int(float(value))
    if typ == "float":
        return float(value)
    raise ValueError(f"Tipo no soportado: {typ}")


def write_property(mvsdk, h_camera, p, value, native_set=None):
    """Escribe una propiedad. NO relee la cámara. Devuelve {"ok", ("error")}."""
    try:
        if p.get("native"):
            if native_set is None:
                return {"ok": False, "error": "NATIVO_NO_DISPONIBLE"}
            ok, detail = native_set(float(value))
            return {"ok": bool(ok), "error": None if ok else str(detail)}

        fn = getattr(mvsdk, p.get("set", ""), None)
        if fn is None:
            return {"ok": False, "error": "NO_EXISTE_EN_MVSDK"}

        typ = p["type"]
        v = _coerce(value, typ)

        if typ == "bool_index":
            rc = fn(h_camera, p["index"], v)
        else:
            rc = fn(h_camera, v)

        if rc not in (None, 0):
            return {"ok": False, "error": f"SDK_ERROR_{rc}"}
        return {"ok": True, "error": None}

    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def read_all(mvsdk, h_camera, native_get=None):
    """Lee TODAS las propiedades -> {id: {label, section, unit, type, options, ok, value, ...}}."""
    data = {}
    for p in property_definitions():
        read = read_property(mvsdk, h_camera, p, native_get=native_get)
        data[p["id"]] = {
            "label": p["label"],
            "section": p["section"],
            "unit": p.get("unit", ""),
            "type": p["type"],
            "options": p.get("options"),
            "ok": bool(read.get("ok")),
            "value": _json_safe(read.get("value")),
            "range": read.get("range"),
            "error": read.get("error"),
        }
    return data


# ======================================================================
# APLICAR UN LOTE (con parada/arranque de adquisición)
# ======================================================================

# Propiedades que en esta cámara solo "prenden" con la adquisición
# detenida. El resto (exposición, ganancia, gamma, contraste, espejo…)
# se escriben en vivo — y además, si se hace CameraStop + CameraPlay,
# la cámara recarga su grupo de parámetros y revierte esos cambios.
_NEEDS_STOP = {
    "isp_processor",
    "trans_pack_len",
    "parameter_mode",
}


def apply_batch(mvsdk, h_camera, changes, native_get=None, native_set=None):
    """
    `changes`: dict {id: value}. Escribe cada propiedad, la relee para
    verificar y devuelve (all_ok, {id: {"set_ok", "verified", "message"}}).

    Pensada para llamarse desde el ÚNICO hilo que toca el SDK (el de
    cámara), así que por defecto NO detiene la adquisición. Solo la
    detiene si alguna propiedad del lote lo exige (_NEEDS_STOP).
    """
    defs = definitions_by_id()
    status = {}
    all_ok = True

    needs_stop = any(str(pid) in _NEEDS_STOP for pid in changes)
    stopped = False

    if needs_stop:
        try:
            mvsdk.CameraStop(h_camera)
            stopped = True
        except Exception as exc:
            status.setdefault("_camera", {})["stop_error"] = (
                f"{type(exc).__name__}: {exc}"
            )

    try:
        for prop_id, value in changes.items():
            p = defs.get(str(prop_id))
            if p is None:
                status[prop_id] = {"set_ok": False, "verified": None,
                                   "message": "Propiedad desconocida"}
                all_ok = False
                continue

            w = write_property(mvsdk, h_camera, p, value, native_set=native_set)
            entry = {"set_ok": bool(w.get("ok")), "verified": None,
                     "message": w.get("error") or "OK"}

            if w.get("ok"):
                r = read_property(mvsdk, h_camera, p, native_get=native_get)
                entry["verified"] = _json_safe(r.get("value"))
                if not r.get("ok"):
                    entry["message"] = "Escrito, pero no se pudo verificar"
            else:
                all_ok = False

            status[prop_id] = entry
    finally:
        if stopped:
            try:
                mvsdk.CameraPlay(h_camera)
            except Exception as exc:
                all_ok = False
                status.setdefault("_camera", {})["play_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )

    return all_ok, status


# ======================================================================
# camera_config.json
# ======================================================================

def config_load(path):
    """
    Devuelve {id: value} con lo guardado, o None si el archivo no existe
    o no es legible.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None

    props = data.get("properties", {})
    valid = definitions_by_id()
    out = {}
    for pid, entry in props.items():
        if pid not in valid:
            continue
        if not entry.get("ok", True):
            continue
        out[pid] = entry.get("value")
    return out


def config_save(path, camera_ip, mvsdk, h_camera, native_get=None, overrides=None):
    """
    Persiste todas las propiedades en `path` y relee el archivo para
    confirmar. Cada propiedad se toma de:
      1. `overrides` (lo que el usuario acaba de fijar / el archivo cargado
         al arranque) — fuente de verdad, y
      2. si no está en overrides, la lectura actual de la cámara.
    (En esta cámara algunos GetXxx no reflejan de inmediato el SetXxx
    correspondiente, así que overrides gana.)
    Devuelve (success, result_dict).
    """
    overrides = overrides or {}
    properties = {}
    failed_reads = []

    for p in property_definitions():
        pid = p["id"]
        if pid in overrides:
            value = overrides[pid]
            ok = True
        else:
            read = read_property(mvsdk, h_camera, p, native_get=native_get)
            ok = bool(read.get("ok"))
            value = read.get("value")
            if not ok:
                failed_reads.append(pid)

        properties[pid] = {
            "label": p.get("label"),
            "section": p.get("section"),
            "type": p.get("type"),
            "value": _json_safe(value),
            "ok": ok,
        }

    config = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "camera_ip": camera_ip,
        "properties": properties,
    }

    write_verified = False
    verify_error = None
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        with open(path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        write_verified = (
            len(reloaded.get("properties", {})) == len(properties)
            and reloaded.get("saved_at") == config["saved_at"]
        )
        if not write_verified:
            verify_error = "El archivo releído no coincide con lo guardado"
    except Exception as exc:
        verify_error = f"{type(exc).__name__}: {exc}"

    success = (len(failed_reads) == 0) and write_verified
    return success, {
        "success": success,
        "path": path,
        "properties_total": len(properties),
        "properties_failed": failed_reads,
        "file_write_verified": write_verified,
        "verify_error": verify_error,
        "config": config,
    }
