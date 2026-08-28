# camera-yolo

App demo: captura de la cámara MindVision + detección **YOLO26-X** en GPU (CUDA) + web con métricas
en vivo (`:8090`). Extrapola las cajas por velocidad entre inferencias.

Es una **aplicación consumidora**, no parte de la base reutilizable. En la arquitectura objetivo,
la inferencia sería un consumidor del bus del `camera-service`, no un proceso que abre la cámara
directamente.

## Deuda técnica (rutas hardcodeadas en `main.py`)

- `MVSDK_PATH = r"C:\Users\dmore\OneDrive\Backup\SOFTWARE\VISION CHINA\USB SDK\...\python_demo"`
  → debería usar `services/camera-service/legacy/mvsdk.py` o el paquete del refactor.
- `MODEL_PATH = r"C:\Python\YOLO\yolo26x.pt"` → config por entorno.
- `CAMERA_IP`, puertos, `DEVICE = "cuda:0"` → config.

## Dependencias

`numpy`, `opencv-python`, `torch` (+ CUDA), `ultralytics`, `psutil`, `nvidia-smi` en el PATH,
y el archivo de pesos `yolo26x.pt`.

## Ejecutar

```bash
python main.py   # requiere cámara + GPU NVIDIA + pesos
```
