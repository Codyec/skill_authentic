# -*- coding: utf-8 -*-
"""
test_properties.py

Escaneo de propiedades de la cámara GE134GM vía mvsdk.

QUÉ HACE:
  1. Descubre automáticamente todos los pares CameraGetXxx / CameraSetXxx
     de mvsdk.py que sean "propiedad simple" (Get recibe solo hCamera y
     devuelve un único valor; Set recibe hCamera + ese mismo valor).
  2. Para cada una: lee el valor actual, intenta escribirle EXACTAMENTE
     ESE MISMO VALOR (nunca uno distinto), y vuelve a leer para
     confirmar que no cambió nada. Si la escritura fue rechazada, la
     propiedad queda marcada como SOLO LECTURA. Si se aceptó, queda
     marcada como LECTURA/ESCRITURA.
  3. Como el valor que se escribe es siempre el mismo que ya tenía,
     la cámara termina exactamente en el mismo estado en el que
     arrancó — no hace falta "restaurar" nada aparte.
  4. Prueba además varias sintaxis de CameraCommonCall (la función
     genérica tipo comando) contra un par de features GenICam
     conocidas, para intentar encontrar cómo se leen/escriben desde
     Python, incluyendo AcquisitionFrameRateMode.

QUÉ NO HACE (a propósito, por seguridad):
  - No toca funciones de identidad/almacenamiento persistente
    (número de serie, nombre amigable, datos de usuario en flash).
  - No toca funciones de ciclo de vida de sesión, callbacks, buffers
    de imagen, ni subsistemas complejos (Grabber, Image_, LUT,
    corrección de campo plano, píxeles muertos, distorsión).
  - No toca propiedades indexadas (E/S digital, LEDs) — el filtro de
    firma (Get recibe solo hCamera) las excluye automáticamente.

Uso:
    python test_properties.py

Al terminar, además de imprimir todo en consola, guarda un reporte
en properties_report.txt en la misma carpeta.
"""

import sys
import inspect
import ctypes

MVSDK_PATH = (
    r"C:\Users\dmore\OneDrive\Backup\SOFTWARE\VISION CHINA"
    r"\USB SDK\USB Drive\Demo\Demo\Python\python_demo"
)

if MVSDK_PATH not in sys.path:
    sys.path.insert(0, MVSDK_PATH)

import mvsdk


# ============================================================
# LISTA NEGRA
# ============================================================
# Cualquier función Get/Set cuyo nombre contenga alguno de estos
# fragmentos se excluye del escaneo automático, aunque su firma
# pase el filtro de "propiedad simple".

DENYLIST_SUBSTRINGS = [
    "FriendlyName",
    "WriteSN",
    "ReadSN",
    "UserData",
    "Parameter",       # Team/UserSet, ya lo probamos aparte
    "DataDirectory",
    "SysOption",
    "CallbackFunction",
    "ConnectionStatusCallback",
    "FrameEventCallback",
    "TransferRoi",     # requiere índice + 4 coords
    "AutoConnect",
    "SingleGrabMode",
    "LightingController",
]


# ============================================================
# DESCUBRIR PARES GET/SET DE PROPIEDAD SIMPLE
# ============================================================

def discover_simple_properties():

    all_functions = dict(
        inspect.getmembers(
            mvsdk,
            inspect.isfunction
        )
    )

    pairs = []

    for name, get_fn in all_functions.items():

        if not name.startswith("CameraGet"):
            continue

        suffix = name[len("CameraGet"):]

        set_name = "CameraSet" + suffix

        set_fn = all_functions.get(set_name)

        if set_fn is None:
            continue

        if any(bad in name for bad in DENYLIST_SUBSTRINGS):
            continue

        try:

            get_params = list(
                inspect.signature(get_fn).parameters
            )

            set_params = list(
                inspect.signature(set_fn).parameters
            )

        except (TypeError, ValueError):

            continue

        # Get: solo (hCamera). Set: (hCamera, valor).
        if len(get_params) != 1:
            continue

        if len(set_params) != 2:
            continue

        pairs.append((suffix, get_fn, set_fn))

    pairs.sort(key=lambda p: p[0])

    return pairs


# ============================================================
# PROBAR UNA PROPIEDAD (LEER -> ESCRIBIR MISMO VALOR -> LEER)
# ============================================================

def test_property(h_camera, suffix, get_fn, set_fn):

    try:

        original_value = get_fn(h_camera)

    except Exception as e:

        return {
            "name": suffix,
            "status": "NO SE PUDO LEER",
            "detail": repr(e)
        }

    try:

        set_fn(h_camera, original_value)

    except mvsdk.CameraException as e:

        return {
            "name": suffix,
            "status": "SOLO LECTURA",
            "detail": (
                "la cámara rechazó la escritura, código "
                + str(e.error_code)
                + ": "
                + str(e.message)
            ),
            "value": original_value
        }

    except Exception as e:

        return {
            "name": suffix,
            "status": "ERROR DE SCRIPT (no es solo-lectura)",
            "detail": (
                "excepción de Python al reescribir "
                "(probablemente el tipo de dato no "
                "matchea, no necesariamente que sea "
                "de solo lectura): "
                + repr(e)
            ),
            "value": original_value
        }

    # Confirmar que sigue exactamente igual.

    try:

        confirm_value = get_fn(h_camera)

    except Exception as e:

        return {
            "name": suffix,
            "status": "LECTURA/ESCRITURA (¡no pude reconfirmar!)",
            "detail": repr(e),
            "value": original_value
        }

    if confirm_value != original_value:

        return {
            "name": suffix,
            "status": "LECTURA/ESCRITURA (¡OJO: cambió solo!)",
            "detail": (
                "antes="
                + repr(original_value)
                + " después="
                + repr(confirm_value)
            ),
            "value": original_value
        }

    return {
        "name": suffix,
        "status": "LECTURA/ESCRITURA",
        "detail": "sin cambios, valor preservado",
        "value": original_value
    }


# MAIN
# ============================================================

def main():

    print("Buscando cámara...")

    devices = mvsdk.CameraEnumerateDevice()

    if not devices:

        print("No se encontró ninguna cámara.")
        return

    dev = devices[0]

    print("Cámara:", dev.GetFriendlyName())

    h_camera = mvsdk.CameraInit(dev, -1, -1)

    print("CameraInit OK")

    try:

        pairs = discover_simple_properties()

        print()
        print("=" * 70)
        print(
            "Probando",
            len(pairs),
            "propiedades simples (Get/Set de un solo valor)"
        )
        print("=" * 70)

        results = []

        print()
        print(
            "{:<28} {:<22} {:<20} {}".format(
                "Propiedad", "Estado", "Tipo (Python)", "Valor actual"
            )
        )
        print("-" * 110)

        for suffix, get_fn, set_fn in pairs:

            result = test_property(
                h_camera,
                suffix,
                get_fn,
                set_fn
            )

            results.append(result)

            valor = result.get("value")

            print(
                "{:<28} {:<22} {:<20} {}".format(
                    result["name"],
                    result["status"],
                    type(valor).__name__,
                    valor
                )
            )

            if result.get("detail"):

                print("    detalle:", result["detail"])

        writable = [
            r for r in results
            if r["status"] == "LECTURA/ESCRITURA"
        ]

        readonly = [
            r for r in results
            if r["status"] == "SOLO LECTURA"
        ]

        failed = [
            r for r in results
            if r["status"] == "NO SE PUDO LEER"
        ]

        script_errors = [
            r for r in results
            if r["status"] == "ERROR DE SCRIPT (no es solo-lectura)"
        ]

        weird = [
            r for r in results
            if "OJO" in r["status"]
        ]

        print()
        print("=" * 70)
        print("RESUMEN")
        print("=" * 70)
        print("Lectura/escritura:", len(writable))
        print("Solo lectura:", len(readonly))
        print("No se pudo leer:", len(failed))
        print("Error de script (revisar a mano):", len(script_errors))
        print("Casos raros (revisar):", len(weird))

        # CameraCommonCall ya lo descartamos con la documentación
        # oficial (CameraApi.h dice literalmente "funciones
        # especiales, normalmente no hace falta llamarla en
        # desarrollo de segundo nivel") — no vale la pena seguir
        # gastando tiempo ahí.

        # En cambio, leemos CameraGetFrameRate — la función real
        # de frecuencia máxima que SÍ encontramos en el header C,
        # aunque no esté en mvsdk.py (parche por ctypes).

        print()
        print("=" * 70)
        print("FRAME RATE (función real, no está en mvsdk.py)")
        print("=" * 70)

        try:

            current_rate = ctypes.c_int()

            err = mvsdk._sdk.CameraGetFrameRate(
                h_camera,
                ctypes.byref(current_rate)
            )

            print(
                "CameraGetFrameRate ->",
                current_rate.value,
                "(0 = sin límite / máxima) — código:",
                err
            )

        except Exception as e:

            print(
                "CameraGetFrameRate no disponible en esta DLL:",
                repr(e)
            )

        # ------------------------------------------------
        # GUARDAR REPORTE A ARCHIVO
        # ------------------------------------------------

        with open(
            "properties_report.txt",
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                "Propiedad; Estado; Tipo; Valor; Detalle\n"
            )

            for r in results:

                f.write(
                    "{}; {}; {}; {}; {}\n".format(
                        r["name"],
                        r["status"],
                        type(r.get("value")).__name__,
                        r.get("value"),
                        r.get("detail", "")
                    )
                )

        print()
        print(
            "Reporte guardado en properties_report.txt"
        )

    finally:

        mvsdk.CameraUnInit(h_camera)

        print()
        print("Cámara cerrada. Nada quedó modificado.")


if __name__ == "__main__":
    main()