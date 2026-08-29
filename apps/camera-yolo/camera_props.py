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
    """
    Metadatos estáticos de TODAS las propiedades escalares R/W que expone
    el SDK para esta cámara. NO lee la cámara. Base:
    services/camera-service/legacy/properties_report.txt (escaneo de la
    HT-GE134GM) + pares CameraGet/CameraSet escalares de mvsdk.py.
    """
    def P(pid, label, section, typ, base=None, **extra):
        d = {"id": pid, "label": label, "section": section, "type": typ}
        if base:
            d["get"] = "CameraGet" + base
            d["set"] = "CameraSet" + base
        d.update(extra)
        return d

    ROT = [{"value": 0, "label": "0\u00b0"}, {"value": 1, "label": "90\u00b0"},
           {"value": 2, "label": "180\u00b0"}, {"value": 3, "label": "270\u00b0"}]

    return [
        # ---- Exposici\u00f3n ----
        P("auto_exposure", "Exposici\u00f3n autom\u00e1tica", "Exposici\u00f3n", "bool", "AeState"),
        P("ae_target", "Objetivo de auto-exposici\u00f3n", "Exposici\u00f3n", "int", "AeTarget"),
        P("ae_threshold", "Umbral de auto-exposici\u00f3n", "Exposici\u00f3n", "int", "AeThreshold"),
        P("exposure_us", "Tiempo de exposici\u00f3n", "Exposici\u00f3n", "float", "ExposureTime",
          unit="\u00b5s", range="CameraGetExposureTimeRange"),
        P("analog_gain", "Ganancia anal\u00f3gica", "Exposici\u00f3n", "int", "AnalogGain",
          range="CameraGetAeAnalogGainRange"),
        P("analog_gain_x", "Ganancia anal\u00f3gica (x)", "Exposici\u00f3n", "float", "AnalogGainX",
          range="CameraGetAnalogGainXRange"),
        P("anti_flick", "Anti-parpadeo", "Exposici\u00f3n", "bool", "AntiFlick"),
        P("light_frequency", "Frecuencia de red", "Exposici\u00f3n", "enum", "LightFrequency",
          options=[{"value": 0, "label": "50 Hz"}, {"value": 1, "label": "60 Hz"}]),
        P("hdr", "HDR", "Exposici\u00f3n", "bool", "HDR"),
        P("hdr_gain_mode", "Modo de ganancia HDR", "Exposici\u00f3n", "int", "HDRGainMode"),

        # ---- Imagen y color ----
        P("gamma", "Gamma", "Imagen y color", "int", "Gamma"),
        P("contrast", "Contraste", "Imagen y color", "int", "Contrast"),
        P("saturation", "Saturaci\u00f3n", "Imagen y color", "int", "Saturation"),
        P("sharpness", "Nitidez", "Imagen y color", "int", "Sharpness"),
        P("black_level", "Nivel de negro", "Imagen y color", "int", "BlackLevel"),
        P("white_level", "Nivel de blanco", "Imagen y color", "int", "WhiteLevel"),
        P("gain_r", "Ganancia digital R", "Imagen y color", "rgb", component=0,
          get="CameraGetGain", set="CameraSetGain"),
        P("gain_g", "Ganancia digital G", "Imagen y color", "rgb", component=1,
          get="CameraGetGain", set="CameraSetGain"),
        P("gain_b", "Ganancia digital B", "Imagen y color", "rgb", component=2,
          get="CameraGetGain", set="CameraSetGain"),
        P("wb_mode", "Balance de blancos autom\u00e1tico", "Imagen y color", "bool", "WbMode"),
        P("clr_temp_mode", "Modo de temperatura de color", "Imagen y color", "int", "ClrTempMode"),
        P("preset_clr_temp", "Temperatura de color (preset)", "Imagen y color", "int", "PresetClrTemp"),
        P("monochrome", "Forzar monocromo", "Imagen y color", "bool", "Monochrome"),
        P("inverse", "Invertir imagen", "Imagen y color", "bool", "Inverse"),
        P("lut_mode", "Modo LUT", "Imagen y color", "enum", "LutMode",
          options=[{"value": 0, "label": "Param\u00e9trico"}, {"value": 1, "label": "Preset"},
                   {"value": 2, "label": "Usuario"}]),
        P("noise_filter", "Filtro de ruido 2D", "Imagen y color", "bool",
          get="CameraGetNoiseFilterState", set="CameraSetNoiseFilter"),
        P("correct_dead_pixel", "Correcci\u00f3n de p\u00edxeles muertos", "Imagen y color", "bool",
          "CorrectDeadPixel"),

        # ---- Formato ----
        P("mirror_h", "Espejo horizontal", "Formato", "bool_index", index=0,
          get="CameraGetMirror", set="CameraSetMirror"),
        P("mirror_v", "Espejo vertical", "Formato", "bool_index", index=1,
          get="CameraGetMirror", set="CameraSetMirror"),
        P("rotate", "Rotaci\u00f3n", "Formato", "enum", "Rotate", options=ROT),
        P("isp_processor", "Procesador ISP", "Formato", "enum", "IspProcessor",
          options=[{"value": 0, "label": "Software (PC)"}, {"value": 1, "label": "Hardware (c\u00e1mara)"}]),
        P("media_type", "Formato de p\u00edxel (\u00edndice)", "Formato", "int", "MediaType"),
        P("undistort_enable", "Correcci\u00f3n de distorsi\u00f3n", "Formato", "bool", "UndistortEnable"),

        # ---- Adquisici\u00f3n ----
        P("frame_speed", "Velocidad de cuadro", "Adquisici\u00f3n", "enum", "FrameSpeed",
          options=[{"value": 0, "label": "Baja"}, {"value": 1, "label": "Normal"},
                   {"value": 2, "label": "Alta"}, {"value": 3, "label": "S\u00faper"}]),
        P("acquisition_frame_rate", "Frecuencia de adquisici\u00f3n", "Adquisici\u00f3n", "float",
          unit="FPS", native=True),

        # ---- Disparo ----
        P("trigger_mode", "Modo de disparo", "Disparo", "enum", "TriggerMode",
          options=[{"value": 0, "label": "Continuo"}, {"value": 1, "label": "Software"},
                   {"value": 2, "label": "Hardware / externo"}]),
        P("trigger_count", "N\u00ba de disparos", "Disparo", "int", "TriggerCount"),
        P("trigger_delay_us", "Retardo de disparo", "Disparo", "int", "TriggerDelayTime", unit="\u00b5s"),
        P("ext_trig_delay_us", "Retardo disparo externo", "Disparo", "int", "ExtTrigDelayTime", unit="\u00b5s"),
        P("ext_trig_jitter_us", "Jitter disparo externo", "Disparo", "int", "ExtTrigJitterTime", unit="\u00b5s"),
        P("ext_trig_signal_type", "Tipo de se\u00f1al de disparo externo", "Disparo", "enum",
          "ExtTrigSignalType",
          options=[{"value": 0, "label": "Flanco de subida"}, {"value": 1, "label": "Flanco de bajada"},
                   {"value": 2, "label": "Nivel alto"}, {"value": 3, "label": "Nivel bajo"}]),
        P("ext_trig_shutter_type", "Obturador en disparo externo", "Disparo", "enum",
          "ExtTrigShutterType",
          options=[{"value": 0, "label": "Est\u00e1ndar"}, {"value": 1, "label": "Reset global (GRR)"}]),

        # ---- E/S digital ----
        P("strobe_mode", "Modo de flash (strobe)", "E/S digital", "enum", "StrobeMode",
          options=[{"value": 0, "label": "Sync auto con disparo"}, {"value": 1, "label": "Sync manual"},
                   {"value": 2, "label": "Siempre alto"}, {"value": 3, "label": "Siempre bajo"}]),
        P("strobe_delay_us", "Retardo de flash", "E/S digital", "int", "StrobeDelayTime", unit="\u00b5s"),
        P("strobe_pulse_width_us", "Ancho de pulso de flash", "E/S digital", "int", "StrobePulseWidth",
          unit="\u00b5s"),
        P("strobe_polarity", "Polaridad de flash invertida", "E/S digital", "bool", "StrobePolarity"),

        # ---- Dispositivo / red ----
        P("parameter_mode", "Modo de carga de par\u00e1metros", "Dispositivo", "enum", "ParameterMode",
          options=[{"value": 0, "label": "Por modelo"}, {"value": 1, "label": "Por nombre"},
                   {"value": 2, "label": "Por n\u00ba de serie"}, {"value": 3, "label": "En el dispositivo"}]),
        P("trans_pack_len", "Tama\u00f1o de paquete de red", "Red (GigE)", "int", "TransPackLen"),
    ]


CAMERA_ACTIONS = [
    {"id": "once_wb", "label": "Balance de blancos (una vez)",
     "help": "Calcula el balance de blancos con la escena actual."},
    {"id": "once_bb", "label": "Nivel de negro (una vez)",
     "help": "Ajusta el nivel de negro con la escena actual."},
    {"id": "save_to_camera", "label": "Guardar en la c\u00e1mara (flash)",
     "help": "Escribe la configuraci\u00f3n actual en la memoria no vol\u00e1til de la c\u00e1mara."},
]


def run_action(mvsdk, h_camera, name):
    """Ejecuta un m\u00e9todo de acci\u00f3n del SDK. Devuelve (ok, mensaje)."""
    try:
        if name == "once_wb":
            mvsdk.CameraSetOnceWB(h_camera)
            return True, "Balance de blancos aplicado"
        if name == "once_bb":
            mvsdk.CameraSetOnceBB(h_camera)
            return True, "Nivel de negro aplicado"
        if name == "save_to_camera":
            grp = 0
            try:
                grp = int(mvsdk.CameraGetCurrentParameterGroup(h_camera))
            except Exception:
                pass
            mvsdk.CameraSaveParameter(h_camera, grp)
            return True, f"Configuraci\u00f3n guardada en la c\u00e1mara (grupo {grp})"
        return False, f"Acci\u00f3n desconocida: {name}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

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
        elif p["type"] == "rgb":
            value = fn(h_camera)[p["component"]]
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
    if typ in ("int", "enum", "rgb"):
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
        elif typ == "rgb":
            getfn = getattr(mvsdk, p["get"], None)
            cur = list(getfn(h_camera)) if getfn else [v, v, v]
            cur[p["component"]] = v
            rc = fn(h_camera, int(cur[0]), int(cur[1]), int(cur[2]))
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
    "media_type",
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
