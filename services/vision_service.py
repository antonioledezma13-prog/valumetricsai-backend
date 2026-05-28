"""
ValuMetrics AI — Análisis de Patologías con Claude Vision API
=============================================================
Reemplaza YOLOv8 con Claude claude-sonnet-4-20250514 Vision.
Ventajas:
  - Sin instalación de modelos ni GPU
  - Análisis técnico pericial en lenguaje natural
  - Funciona en Render free tier
  - Más preciso que heurístico, más descriptivo que YOLO
  - ~$0.01-0.03 USD por imagen analizada

Flujo:
  1. Usuario sube foto(s) → base64
  2. Se envían a Claude API con prompt pericial estructurado
  3. Claude responde JSON con patologías detectadas
  4. Se generan bboxes aproximados si Claude los identifica
  5. Resultado se integra al valor y al PDF
"""

import os
import io
import json
import base64
import time
import re
import anthropic
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field
from PIL import Image, ImageDraw

# ── Configuración ─────────────────────────────────────────────
CONF_MINIMA = 0.60    # Solo reportar con ≥60% confianza

ORDEN_ESTADOS = ["optimo", "bueno", "normal", "regular", "malo", "ruinoso"]

PENALIZACION_SCORE = {"leve": -1.5, "moderada": -3.0, "critica": -6.0}
PENALIZACION_VALOR = {"leve": 0.5,  "moderada": 2.0,  "critica": 4.0}

# Prompt pericial para Claude
PROMPT_PERICIAL = """Eres un ingeniero estructural y perito valuador certificado con 20 años de experiencia en Venezuela.

Analiza esta imagen de un inmueble con criterio técnico pericial estricto.

INSTRUCCIONES:
- Solo reporta patologías que CLARAMENTE se ven en la imagen
- NO inventes ni asumas patologías que no sean visibles
- Si la imagen es de buena calidad y no hay daños visibles, reporta lista vacía
- Sé conservador: ante la duda, NO reportes

Para cada patología visible, proporciona:
- tipo: una de estas categorías exactas:
  grieta_capilar | grieta_activa | grieta_pasiva | eflorescencia | 
  humedad | desportillado | corrosion_acero | asentamiento | 
  desprendimiento | carbonatacion | falla_estructural
- severidad: leve | moderada | critica
- confianza: número entre 0.0 y 1.0 (qué tan seguro estás de esta detección)
- ubicacion: descripción de dónde se ve en la imagen (ej: "esquina superior derecha", "columna central")
- descripcion_tecnica: descripción técnica pericial en español (2-3 oraciones)
- dimension_estimada: estimación del tamaño si es posible (ej: "grieta ~2mm ancho, ~30cm longitud")

Responde ÚNICAMENTE con JSON válido, sin texto adicional, sin markdown:
{
  "patologias": [
    {
      "tipo": "...",
      "severidad": "...", 
      "confianza": 0.0,
      "ubicacion": "...",
      "descripcion_tecnica": "...",
      "dimension_estimada": "..."
    }
  ],
  "condicion_general": "buena | regular | deficiente | critica",
  "observaciones_generales": "...",
  "elemento_analizado": "fachada | columna | viga | losa | pared | piso | otro"
}"""


@dataclass
class Deteccion:
    tipo: str
    severidad: str
    confianza: float
    ubicacion: str
    descripcion_tecnica: str
    dimension_estimada: str = ""


@dataclass
class FotoAnalizada:
    imagen_original_b64: str
    imagen_anotada_b64:  str
    detecciones: List[Deteccion]
    condicion_general: str = "—"
    observaciones: str = ""
    elemento: str = "—"
    nombre: str = "Imagen"


@dataclass
class ResultadoVision:
    analizado:            bool  = False
    motor:                str   = "Claude Vision API"
    imagenes_procesadas:  int   = 0
    tiempo_s:             float = 0.0
    fotos: List[FotoAnalizada]  = field(default_factory=list)
    total_patologias:     int   = 0
    patologias_criticas:  int   = 0
    patologias_moderadas: int   = 0
    patologias_leves:     int   = 0
    ajuste_score:         float = 0.0
    penalizacion_valor_pct: float = 0.0
    estado_sugerido:      str   = ""
    recomendaciones: List[str]  = field(default_factory=list)
    nota_general:         str   = ""
    advertencia:          str   = ""


class VisionService:

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY no configurada en variables de entorno")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    # ──────────────────────────────────────────
    #  API pública
    # ──────────────────────────────────────────

    def analizar_imagenes(
        self,
        imagenes_b64: List[str],
        estado_actual: str = "normal",
        conf_threshold: float = 0.60,
    ) -> ResultadoVision:

        t0 = time.time()
        fotos: List[FotoAnalizada] = []
        todas_dets: List[Deteccion] = []

        try:
            client = self._get_client()
        except RuntimeError as e:
            return ResultadoVision(
                analizado=False,
                advertencia=str(e),
                nota_general="Configure ANTHROPIC_API_KEY para habilitar el análisis de patologías.",
            )

        for i, b64_raw in enumerate(imagenes_b64[:4]):  # máx 4 fotos por análisis
            try:
                # Limpiar base64
                b64_limpio = b64_raw.split(",", 1)[-1] if "," in b64_raw else b64_raw
                media_type = "image/jpeg"
                if b64_raw.startswith("data:image/png"): media_type = "image/png"
                elif b64_raw.startswith("data:image/webp"): media_type = "image/webp"

                # Llamada a Claude Vision
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_limpio,
                                },
                            },
                            {"type": "text", "text": PROMPT_PERICIAL},
                        ],
                    }],
                )

                # Parsear respuesta JSON
                texto = response.content[0].text.strip()
                # Limpiar si hay markdown
                texto = re.sub(r"```json|```", "", texto).strip()
                data  = json.loads(texto)

                # Filtrar por confianza mínima
                dets_foto = []
                for p in data.get("patologias", []):
                    conf = float(p.get("confianza", 0))
                    if conf < CONF_MINIMA:
                        continue
                    det = Deteccion(
                        tipo=p.get("tipo", "patologia_general"),
                        severidad=p.get("severidad", "leve"),
                        confianza=round(conf, 2),
                        ubicacion=p.get("ubicacion", ""),
                        descripcion_tecnica=p.get("descripcion_tecnica", ""),
                        dimension_estimada=p.get("dimension_estimada", ""),
                    )
                    dets_foto.append(det)
                    todas_dets.append(det)

                # Anotar imagen si hay detecciones
                img_pil = self._b64_a_pil(b64_limpio)
                img_anotada_b64 = self._anotar(img_pil, dets_foto) if img_pil else b64_limpio

                fotos.append(FotoAnalizada(
                    imagen_original_b64=b64_limpio,
                    imagen_anotada_b64 =img_anotada_b64,
                    detecciones=dets_foto,
                    condicion_general=data.get("condicion_general", "—"),
                    observaciones=data.get("observaciones_generales", ""),
                    elemento=data.get("elemento_analizado", "—"),
                    nombre=f"Imagen {i+1}",
                ))

            except json.JSONDecodeError:
                # Claude no devolvió JSON válido — guardar foto sin análisis
                b64_limpio = b64_raw.split(",",1)[-1] if "," in b64_raw else b64_raw
                fotos.append(FotoAnalizada(
                    imagen_original_b64=b64_limpio,
                    imagen_anotada_b64 =b64_limpio,
                    detecciones=[],
                    observaciones="No se pudo parsear respuesta del análisis.",
                    nombre=f"Imagen {i+1}",
                ))
            except Exception as e:
                print(f"[Vision] Error imagen {i+1}: {e}")
                continue

        elapsed = round(time.time() - t0, 2)
        return self._consolidar(todas_dets, fotos, elapsed, estado_actual)

    # ──────────────────────────────────────────
    #  Anotar imagen con texto descriptivo
    # ──────────────────────────────────────────

    def _anotar(self, img: Image.Image, dets: List[Deteccion]) -> str:
        """
        Como Claude no da coordenadas exactas de bbox,
        anotamos con etiquetas de texto en la parte inferior.
        """
        if not dets:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88)
            return base64.b64encode(buf.getvalue()).decode()

        ann   = img.copy().convert("RGB")
        W, H  = ann.size
        draw  = ImageDraw.Draw(ann)
        COLORES = {"leve":(255,200,0), "moderada":(255,120,0), "critica":(220,20,60)}

        # Franja inferior con hallazgos
        franja_h  = min(30 * len(dets) + 20, H // 3)
        franja_y  = H - franja_h
        draw.rectangle([0, franja_y, W, H], fill=(10,20,40,200))

        # Línea separadora
        draw.rectangle([0, franja_y, W, franja_y+2], fill=(200,16,46))

        y = franja_y + 8
        for det in dets:
            color = COLORES.get(det.severidad, (255,255,255))
            sev_label = {"leve":"⚠ LEVE","moderada":"🔶 MOD","critica":"🔴 CRIT"}.get(det.severidad,"")
            label = f"{sev_label} {det.tipo.replace('_',' ').upper()} ({det.confianza:.0%}) — {det.ubicacion}"
            draw.text((10, y), label, fill=color)
            y += 28
            if y > H - 10:
                break

        buf = io.BytesIO()
        ann.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()

    # ──────────────────────────────────────────
    #  Consolidar resultado final
    # ──────────────────────────────────────────

    def _consolidar(
        self, dets: List[Deteccion], fotos: List[FotoAnalizada],
        elapsed: float, estado_actual: str
    ) -> ResultadoVision:

        criticas  = [d for d in dets if d.severidad == "critica"]
        moderadas = [d for d in dets if d.severidad == "moderada"]
        leves     = [d for d in dets if d.severidad == "leve"]

        # Ajuste score
        if not dets:
            ajuste = +5.0
        else:
            ajuste  = len(criticas)  * PENALIZACION_SCORE["critica"]
            ajuste += len(moderadas) * PENALIZACION_SCORE["moderada"]
            ajuste += len(leves)     * PENALIZACION_SCORE["leve"]
            ajuste  = max(ajuste, -20.0)

        # Penalización valor
        pen = min(
            len(criticas)*PENALIZACION_VALOR["critica"] +
            len(moderadas)*PENALIZACION_VALOR["moderada"] +
            len(leves)*PENALIZACION_VALOR["leve"], 25.0
        )

        # Estado sugerido
        deg = 2 if criticas else (1 if moderadas else 0)
        idx = ORDEN_ESTADOS.index(estado_actual) if estado_actual in ORDEN_ESTADOS else 2
        estado_sug = ORDEN_ESTADOS[min(idx+deg, len(ORDEN_ESTADOS)-1)]

        # Nota general
        if not dets:
            nota = (f"Análisis Claude Vision completado ({elapsed:.1f}s). "
                    "No se detectaron patologías estructurales visibles "
                    "con confianza ≥60%. Condiciones aparentes satisfactorias.")
        elif criticas:
            nota = (f"ALERTA: {len(criticas)} patología(s) crítica(s) detectada(s). "
                    "Inspección presencial urgente por ingeniero estructural. "
                    "No cerrar operación inmobiliaria sin evaluación técnica.")
        elif moderadas:
            nota = (f"{len(moderadas)} patología(s) moderada(s) detectada(s). "
                    "Evaluar costo de reparación antes de fijar precio definitivo.")
        else:
            nota = f"{len(leves)} patología(s) leve(s). Mantenimiento preventivo recomendado."

        # Recomendaciones
        tipos = {d.tipo for d in dets}
        recs  = []
        if {"grieta_activa","asentamiento","falla_estructural"} & tipos:
            recs.append("Contratar ingeniero estructural colegiado — inspección presencial urgente.")
            recs.append("Instalar testigos en grietas activas para monitorear progresión.")
        if "corrosion_acero" in tipos:
            recs.append("Evaluar sección residual del acero expuesto. Aplicar tratamiento anticorrosivo.")
        if {"humedad","eflorescencia"} & tipos:
            recs.append("Revisar sistema de impermeabilización y drenajes pluviales.")
        if {"desportillado","desprendimiento"} & tipos:
            recs.append("Verificar adherencia del revestimiento en área circundante.")
        if {"grieta_capilar","grieta_pasiva"} & tipos:
            recs.append("Sellar fisuras con masilla elástica o inyección de resina epóxica.")
        if "carbonatacion" in tipos:
            recs.append("Realizar prueba de carbonatación con fenolftaleína en núcleos extraídos.")
        if not recs:
            recs.append("Mantenimiento preventivo periódico recomendado.")

        return ResultadoVision(
            analizado=True,
            motor="Claude Vision API (claude-sonnet-4-20250514)",
            imagenes_procesadas=len(fotos),
            tiempo_s=elapsed,
            fotos=fotos,
            total_patologias=len(dets),
            patologias_criticas=len(criticas),
            patologias_moderadas=len(moderadas),
            patologias_leves=len(leves),
            ajuste_score=round(ajuste, 2),
            penalizacion_valor_pct=round(pen, 2),
            estado_sugerido=estado_sug,
            recomendaciones=recs,
            nota_general=nota,
        )

    def _b64_a_pil(self, b64: str) -> Optional[Image.Image]:
        try:
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        except Exception as e:
            print(f"[Vision] Error PIL: {e}")
            return None


vision_service = VisionService()
