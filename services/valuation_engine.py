"""
ValuMetrics AI - Motor de Valoración Pericial
=============================================
Implementa metodologías de tasación conforme a normas venezolanas e internacionales:
  - Costo de Reposición Deprecado (Röss-Hödecke)
  - Comparación de Mercado
  - Capitalización de Rentas (solo comercial/industrial)

Tipos de inmueble soportados:
  - propiedad_horizontal  (apartamento, penthouse, PH, suite)
  - vivienda_familiar     (casa, quinta, townhouse, chalet)
  - local_comercial       (local, oficina, consultorio, planta baja comercial)
  - galpon_industrial     (galpón, nave industrial, depósito, almacén)
  - terreno               (terreno urbano/rural, parcela, lote)
  - finca                 (finca, hacienda, granja, fundo)
"""

import uuid
import math
from datetime import datetime
from decimal import Decimal, getcontext
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

getcontext().prec = 14


# ─────────────────────────────────────────────
#  Constantes y tablas de referencia
# ─────────────────────────────────────────────

# Vida útil normativa por tipo de construcción (años)
VIDA_UTIL = {
    "aporticado":           90,   # sistema aporticado — máxima durabilidad estructural
    "concreto_armado":      80,
    "mamposteria_confinada":75,   # mampostería confinada con vigas y columnas
    "metalica":             60,
    "mamposteria_bloque":   70,
    "madera_noble":         50,
    "mixta":                65,
    "prefabricada":         40,
    "bahareque":            35,
    "adobe":                40,
}

# Factor de acabados (multiplica el costo de reposición)
FACTOR_ACABADOS = {
    "lujo":       1.45,
    "alto":       1.25,
    "medio_alto": 1.10,
    "medio":      1.00,
    "bajo":       0.80,
    "rustico":    0.65,
    "industrial": 0.90,   # galpones
}

# ─────────────────────────────────────────────────────────────
#  COSTO DE REPOSICIÓN POR SISTEMA CONSTRUCTIVO (USD/m²)
#  Fuente: DataLaing MaPreX 2025, COVENIN, mercado venezolano
#  Referencia: Caracas zona residencial estándar, acabados medios
#  Incluye: materiales + mano de obra + indirectos (sin terreno)
# ─────────────────────────────────────────────────────────────

COSTO_CONSTRUCCION_M2 = {
    # ── Sistemas en Concreto / Mampostería ─────────────────
    "aporticado":            850,   # pórticos + losa + instalaciones completas
                                    # DataLaing edificio formal ~$1,453 con costos indirectos
                                    # vivienda aislada ~$750-950
    "concreto_armado":       720,   # concreto armado convencional, vigas/columnas/losa
    "mamposteria_confinada":  580,   # bloque + vigas/columnas de confinamiento
    "mamposteria_bloque":     420,   # bloque sin confinar, mínima estructura
    # ── Sistemas Metálicos ─────────────────────────────────
    "metalica":              480,   # estructura metálica + cerramiento, buen nivel
    # ── Sistemas Mixtos ────────────────────────────────────
    "mixta":                 540,   # concreto + metal, combinación variable
    # ── Sistemas Livianos / Económicos ─────────────────────
    "prefabricada":          320,   # paneles prefabricados, calidad media-baja
    "bahareque":             180,   # bahareque mejorado (caña/madera + mortero)
                                    # bahareque rural básico puede bajar a $80-120
    "adobe":                 140,   # adobe, tierra compactada, construcción rural
    # ── Sistemas en Madera ─────────────────────────────────
    "madera_noble":          380,   # madera dura (pardillo, cedro), buena carpintería
    # ── Referencia por tipo (sin sistema específico) ────────
    # Estos valores se usan cuando el usuario no especifica sistema
    "_propiedad_horizontal":  650,  # promedio PH Caracas (aporticado/concreto armado)
    "_vivienda_familiar":     520,  # promedio vivienda (concreto/mampostería confinada)
    "_local_comercial":       480,  # local terminado con instalaciones comerciales
    "_galpon_industrial":     280,  # nave industrial básica (metálica/bloque)
    "_finca":                 160,  # mejoras agrícolas promedio
}

# Costos mínimos absolutos por sistema (piso pericial — jamás se puede tasar menos)
COSTO_MINIMO_M2 = {
    "aporticado":            600,
    "concreto_armado":       480,
    "mamposteria_confinada":  380,
    "mamposteria_bloque":     260,
    "metalica":              320,
    "mixta":                 350,
    "prefabricada":          200,
    "bahareque":             100,
    "adobe":                  80,
    "madera_noble":          240,
}

# Costo base por TIPO DE INMUEBLE (fallback si no hay sistema especificado)
COSTO_BASE_M2 = {
    "propiedad_horizontal":  650,
    "vivienda_familiar":     520,
    "local_comercial":       480,
    "galpon_industrial":     280,
    "terreno":                 0,
    "finca":                 160,
}

# ─────────────────────────────────────────────────────────────
#  SISTEMA DE PUNTUACIÓN DE ZONA (reemplaza factor simple)
#  Cada subfactor suma/resta puntos → score 0-100 → factor 0.40-1.80
# ─────────────────────────────────────────────────────────────

# 1. Clase socioeconómica de la zona
PUNTAJE_CLASE_ZONA = {
    "alta":         30,   # sectores premium, residencias de lujo
    "media_alta":   24,
    "media":        18,
    "media_baja":   10,
    "baja":          4,
    "rural":         0,
}

# 2. Tipo de acceso / comunidad
PUNTAJE_ACCESO = {
    "conjunto_privado_vigilado":  12,  # urbanización cerrada con garita
    "conjunto_privado":            9,  # cerrado sin vigilancia 24h
    "urbanizacion_abierta":        6,  # urbanización convencional
    "libre_asfalto":               3,  # calle pública asfaltada
    "libre_tierra":                0,  # acceso en tierra o sin pavimento
}

# 3. Estado de las vías
PUNTAJE_VIAS = {
    "asfalto_senalizado":     8,   # pavimento + señalización + aceras
    "asfalto_bueno":          6,
    "asfalto_deteriorado":    3,
    "adoquin":                4,
    "tierra_transitable":     1,
    "tierra_deficiente":      0,
}

# 4. Servicios públicos (acumulativo — hasta 10 pts)
PUNTAJE_SERVICIOS = {
    "agua_24h":           4,
    "agua_racionada":     2,
    "sin_agua":           0,
    "electricidad_estable": 3,
    "electricidad_cortes":  1,
    "sin_electricidad":     0,
    "gas_domiciliario":   2,
    "aseo_urbano":        1,
}

# 5. Equipamiento urbano cercano (acumulativo — hasta 12 pts)
PUNTAJE_EQUIPAMIENTO = {
    "centro_comercial":    3,
    "supermercado":        2,
    "escuela_liceo":       2,
    "universidad":         2,
    "hospital_clinica":    2,
    "parque_recreacion":   1,
    "farmacia":            1,
    "banco_cajero":        1,
    "transporte_publico":  1,
    "restaurantes":        1,
}

# 6. Uso de suelo / zonificación oficial
PUNTAJE_USO_SUELO = {
    "r1":               10,  # residencial unifamiliar densidad baja
    "r2":               10,  # residencial unifamiliar/bifamiliar
    "r3":                9,  # residencial multifamiliar baja densidad
    "r4":                8,  # residencial multifamiliar media densidad
    "r5":                7,  # residencial multifamiliar alta densidad
    "rc":                9,  # residencial-comercial
    "c1":                8,  # comercial local
    "c2":                7,  # comercial zonal
    "c3":                6,  # comercial metropolitano
    "i1":                5,  # industrial liviana
    "i2":                4,  # industrial mediana
    "i3":                3,  # industrial pesada
    "centro_urbano":     7,
    "mixto":             6,
    "especial":          5,
    "sin_clasificar":    4,
}

# 7. Densidad poblacional
PUNTAJE_DENSIDAD = {
    "baja":    8,   # <50 hab/ha — tranquilidad, privacidad
    "media":   6,   # 50-150 hab/ha
    "alta":    3,   # >150 hab/ha — congestión, ruido
    "muy_alta":1,   # >300 hab/ha
}

# 8. Ámbito territorial
PUNTAJE_AMBITO = {
    "urbano_capital":      8,   # ciudad capital / área metropolitana
    "urbano_interior":     6,   # ciudad interior importante
    "suburbano":           4,   # periferia / zonas dormitorio
    "rural_semi":          2,   # semirural con servicios básicos
    "rural":               0,   # zona rural sin servicios
    "zona_pesquera":       3,   # litoral/puerto con actividad pesquera
    "zona_turistica":      5,   # zona con potencial/actividad turística
}

# 9. Seguridad percibida
PUNTAJE_SEGURIDAD = {
    "muy_segura":   8,
    "segura":       6,
    "moderada":     4,
    "insegura":     1,
    "muy_insegura": 0,
}

# ── Conversión score total → factor multiplicador ──────────────
def score_zona_a_factor(score: float) -> float:
    """
    Score 0-100 → Factor 0.40-1.80
    Curva lineal segmentada con ancla en 50 pts = 1.00 (zona estándar)
    """
    score = max(0.0, min(100.0, score))
    if score <= 50:
        # 0 pts → 0.40  |  50 pts → 1.00
        return round(0.40 + (score / 50) * 0.60, 4)
    else:
        # 50 pts → 1.00  |  100 pts → 1.80
        return round(1.00 + ((score - 50) / 50) * 0.80, 4)

# Mantener compatibilidad con zona_tipo simple
FACTOR_ZONA_SIMPLE = {
    "prime":       1.60,
    "residencial": 1.00,
    "periurbano":  0.72,
    "rural":       0.45,
    "interior_a":  0.85,
    "interior_b":  0.65,
}

# Factor de techo (multiplica el costo de construcción)
FACTOR_TECHO = {
    "losa_maciza":       1.00,   # referencia base
    "losa_nervada":      0.97,   # más liviana, algo menos costosa
    "losa_prefabricada": 0.92,
    "acerolit":          0.85,   # panel sandwich metálico
    "zinc_galvanizado":  0.70,
    "zinc_prepintado":   0.78,   # termoacústico, mejor calidad
    "teja_ceramica":     0.90,
    "machihembrado":     0.88,
    "madera_rustica":    0.75,
    "platabanda":        1.02,   # azotea transitable requiere más impermeabilización
    "mixto_techo":       0.90,
}

# Puntaje de amenidades (suma al valor final como % del valor construido)
PUNTAJE_AMENIDADES = {
    "penthouse":        0.18,   # +18% sobre valor calculado
    "terraza":          0.05,
    "areas_verdes":     0.03,
    "doble_entrada":    0.04,
    "cuarto_servicio":  0.03,
    "maletero":         0.02,
    "estudio":          0.025,
    "balcon":           0.02,
    "piscina":          0.08,
    "gimnasio":         0.03,
    "salon_fiestas":    0.025,
    "porton_electrico": 0.015,
}

# Factor climatización
FACTOR_CLIMA = {
    "centralizado":  1.06,
    "individual":    1.02,
    "ventilacion":   1.00,
    "ninguno":       1.00,
}

# Factor por número de habitaciones (relativo a 3 habitaciones = 1.00)
def factor_habitaciones(n: int) -> float:
    if n == 0:  return 0.80
    if n == 1:  return 0.88
    if n == 2:  return 0.95
    if n == 3:  return 1.00
    if n == 4:  return 1.06
    if n == 5:  return 1.10
    return min(1.10 + (n - 5) * 0.02, 1.25)

# Factor baños (relativo a 2 baños = 1.00)
def factor_banos(banos: int, medios: int = 0) -> float:
    total = banos + medios * 0.5
    if total <= 1:   return 0.93
    if total <= 2:   return 1.00
    if total <= 3:   return 1.04
    if total <= 4:   return 1.07
    return min(1.07 + (total - 4) * 0.015, 1.15)

# Factor de piso para propiedad horizontal
def factor_piso(piso: int) -> float:
    if piso <= 0:   return 0.88   # planta baja / sótano
    if piso <= 3:   return 0.96
    if piso <= 8:   return 1.00
    if piso <= 15:  return 1.05
    if piso <= 25:  return 1.08
    return 1.10

# Rentabilidad anual de referencia para capitalización de rentas
YIELD_REFERENCIA = {
    "local_comercial":  0.07,   # 7 % anual
    "galpon_industrial": 0.08,  # 8 % anual
    "propiedad_horizontal": 0.05,
    "vivienda_familiar": 0.04,
}


# ─────────────────────────────────────────────
#  Fórmula Röss-Hödecke (depreciación)
# ─────────────────────────────────────────────

def depreciation_ross_heidecke(age: float, vida_util: float, estado: str = "normal") -> float:
    """
    Depreciación física no lineal Röss-Hödecke.
    
    D = 0.5 * (x + x²)    donde  x = edad / vida_útil
    
    Ajuste por estado de conservación pericial:
      optimo      → multiplica depreciación × 0.70
      bueno       → × 0.85
      normal      → × 1.00
      regular     → × 1.20
      malo        → × 1.50
      ruinoso     → fuerza D ≥ 0.80
    """
    if vida_util <= 0:
        raise ValueError("vida_util debe ser > 0")
    
    x = min(age / vida_util, 1.0)
    d_base = 0.5 * (x + x ** 2)
    
    factor_estado = {
        "optimo":   0.70,
        "bueno":    0.85,
        "normal":   1.00,
        "regular":  1.20,
        "malo":     1.50,
        "ruinoso":  9999,   # señal especial
    }.get(estado, 1.00)
    
    if factor_estado == 9999:
        return max(d_base, 0.80)
    
    d_ajustada = min(d_base * factor_estado, 0.95)
    return round(d_ajustada, 6)


# ─────────────────────────────────────────────
#  Score de confianza algorítmica
# ─────────────────────────────────────────────

def calcular_confidence_score(
    comparables: int,
    tiene_planos: bool,
    tiene_imagenes: bool,
    zona_conocida: bool,
    tipo: str,
    estado: str,
) -> float:
    """Score 0–100 que expresa la confiabilidad del valor estimado."""
    score = 50.0
    score += min(comparables * 4, 20)   # hasta +20 por comparables
    if tiene_planos:   score += 8
    if tiene_imagenes: score += 7
    if zona_conocida:  score += 10
    # tipos con mejor data de mercado
    if tipo in ("propiedad_horizontal", "local_comercial"): score += 5
    if estado in ("optimo", "bueno"): score += 5
    return round(min(score, 99.5), 1)


# ─────────────────────────────────────────────
#  Dataclasses de entrada / salida
# ─────────────────────────────────────────────

@dataclass
class InputValuacion:
    # Identificación
    tipo_inmueble: str          # ver lista al inicio del módulo
    direccion: str
    ciudad: str
    zona_tipo: str = "residencial"     # compatibilidad simple (fallback)

    # ── Parámetros avanzados de zona (opcionales) ──────────────
    # Si se proveen, reemplazan el zona_tipo simple con score calculado
    zona_clase: str = ""               # alta|media_alta|media|media_baja|baja|rural
    zona_acceso: str = ""              # conjunto_privado_vigilado|conjunto_privado|urbanizacion_abierta|libre_asfalto|libre_tierra
    zona_vias: str = ""                # asfalto_senalizado|asfalto_bueno|asfalto_deteriorado|adoquin|tierra_transitable|tierra_deficiente
    zona_agua: str = ""                # agua_24h|agua_racionada|sin_agua
    zona_electricidad: str = ""        # electricidad_estable|electricidad_cortes|sin_electricidad
    zona_gas: bool = False
    zona_aseo: bool = False
    zona_uso_suelo: str = ""           # r1|r2|r3|r4|r5|rc|c1|c2|c3|i1|i2|i3|centro_urbano|mixto
    zona_densidad: str = ""            # baja|media|alta|muy_alta
    zona_ambito: str = ""              # urbano_capital|urbano_interior|suburbano|rural_semi|rural|zona_pesquera|zona_turistica
    zona_seguridad: str = ""           # muy_segura|segura|moderada|insegura|muy_insegura
    # Equipamiento cercano (checkboxes)
    zona_equipamiento: list = None     # lista de: centro_comercial|supermercado|escuela_liceo|universidad|hospital_clinica|parque_recreacion|farmacia|banco_cajero|transporte_publico|restaurantes

    # Terreno
    area_terreno_m2: float = 0.0
    valor_tierra_usd_m2: float = 0.0   # precio de mercado del m² de tierra

    # Construcción
    area_construida_m2: float = 0.0
    edad_anios: float = 0.0
    tipo_construccion: str = "concreto_armado"
    acabados: str = "medio"
    estado_conservacion: str = "normal"

    # Propiedad horizontal
    piso_nivel: int = 1
    tiene_ascensor: bool = False
    tiene_estacionamiento: bool = False
    puesto_estacionamiento: int = 0   # cantidad

    # Finca / terreno rural
    hectareas: float = 0.0
    mejoras_agricolas: bool = False

    # Galpón / industrial
    altura_libre_m: float = 0.0
    tiene_rampa: bool = False
    capacidad_electrica_kva: float = 0.0

    # Datos de mercado
    comparables: int = 0
    renta_mensual_usd: float = 0.0    # para capitalización

    # Metadatos
    tiene_planos: bool = False
    tiene_imagenes: bool = False
    tasa_bcv_ves: float = 36000.0      # tasa BCV VES/USD vigente
    usuario_id: str = ""
    # Características físicas
    tipo_techo: str = ""               # ver FACTOR_TECHO
    habitaciones: int = 0
    banos: int = 0
    medios_banos: int = 0
    estacionamientos: int = 0
    amenidades: list = None            # lista de PUNTAJE_AMENIDADES
    clima: str = "individual"          # ninguno|individual|centralizado|ventilacion

    notas: str = ""


@dataclass
class ResultadoValuacion:
    hash_operacion: str
    timestamp: str
    tipo_inmueble: str
    direccion: str

    # Valores calculados
    valor_tierra_usd: float
    valor_construccion_nuevo_usd: float
    depreciacion_rate: float
    valor_construccion_depreciado_usd: float
    valor_total_usd: float
    valor_total_ves: float
    tasa_bcv: float

    # Metadata analítica
    confidence_score: float
    metodologia: str
    vida_util_aplicada: float
    factor_zona: float
    factor_acabados: float
    estado_conservacion: str

    # Desglose adicional (opcional según tipo)
    valor_por_m2_usd: float = 0.0
    valor_renta_capitalizado_usd: float = 0.0
    delta_geografico: str = ""

    # Zona avanzada
    score_zona: float = None
    desglose_zona: Dict[str, Any] = field(default_factory=dict)
    # Para PDF
    parametros_entrada: Dict[str, Any] = field(default_factory=dict)
    notas_pericial: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────
#  Motor principal
# ─────────────────────────────────────────────

def calcular_factor_zona(inp) -> tuple:
    """
    Calcula el factor de zona a partir de los parámetros avanzados.
    Retorna (factor: float, score: float, desglose: dict)
    Si no hay parámetros avanzados, usa zona_tipo simple.
    """
    # Verificar si hay parámetros avanzados
    tiene_avanzado = any([
        inp.zona_clase, inp.zona_acceso, inp.zona_vias,
        inp.zona_agua, inp.zona_electricidad, inp.zona_uso_suelo,
        inp.zona_densidad, inp.zona_ambito, inp.zona_seguridad,
    ])

    if not tiene_avanzado:
        factor = FACTOR_ZONA_SIMPLE.get(inp.zona_tipo, 1.00)
        return factor, None, {}

    score = 0.0
    desglose = {}

    # 1. Clase socioeconómica (peso alto)
    if inp.zona_clase:
        pts = PUNTAJE_CLASE_ZONA.get(inp.zona_clase, 0)
        score += pts
        desglose["clase_zona"] = pts

    # 2. Tipo de acceso / comunidad
    if inp.zona_acceso:
        pts = PUNTAJE_ACCESO.get(inp.zona_acceso, 0)
        score += pts
        desglose["acceso"] = pts

    # 3. Estado de vías
    if inp.zona_vias:
        pts = PUNTAJE_VIAS.get(inp.zona_vias, 0)
        score += pts
        desglose["vias"] = pts

    # 4. Servicios públicos (acumulativo)
    pts_serv = 0
    if inp.zona_agua:
        pts_serv += PUNTAJE_SERVICIOS.get(inp.zona_agua, 0)
    if inp.zona_electricidad:
        pts_serv += PUNTAJE_SERVICIOS.get(inp.zona_electricidad, 0)
    if inp.zona_gas:
        pts_serv += PUNTAJE_SERVICIOS["gas_domiciliario"]
    if inp.zona_aseo:
        pts_serv += PUNTAJE_SERVICIOS["aseo_urbano"]
    pts_serv = min(pts_serv, 10)
    score += pts_serv
    desglose["servicios"] = pts_serv

    # 5. Equipamiento urbano (acumulativo)
    pts_equip = 0
    for item in (inp.zona_equipamiento or []):
        pts_equip += PUNTAJE_EQUIPAMIENTO.get(item, 0)
    pts_equip = min(pts_equip, 12)
    score += pts_equip
    desglose["equipamiento"] = pts_equip

    # 6. Uso de suelo / zonificación
    if inp.zona_uso_suelo:
        pts = PUNTAJE_USO_SUELO.get(inp.zona_uso_suelo, 4)
        score += pts
        desglose["uso_suelo"] = pts

    # 7. Densidad poblacional
    if inp.zona_densidad:
        pts = PUNTAJE_DENSIDAD.get(inp.zona_densidad, 6)
        score += pts
        desglose["densidad"] = pts

    # 8. Ámbito territorial
    if inp.zona_ambito:
        pts = PUNTAJE_AMBITO.get(inp.zona_ambito, 4)
        score += pts
        desglose["ambito"] = pts

    # 9. Seguridad
    if inp.zona_seguridad:
        pts = PUNTAJE_SEGURIDAD.get(inp.zona_seguridad, 4)
        score += pts
        desglose["seguridad"] = pts

    factor = score_zona_a_factor(score)
    return factor, round(score, 1), desglose


class MotorValuacion:

    def valorar(self, inp: InputValuacion) -> ResultadoValuacion:
        tipo = inp.tipo_inmueble.lower().strip()
        if tipo not in COSTO_BASE_M2:
            raise ValueError(f"Tipo de inmueble no soportado: {tipo}. "
                             f"Opciones: {list(COSTO_BASE_M2.keys())}")

        # ── 1. Valor de la tierra ──────────────────────────
        v_tierra = self._calcular_tierra(inp, tipo)

        # ── 2. Valor de construcción nuevo ────────────────
        v_construccion_nuevo = self._calcular_construccion_nueva(inp, tipo)

        # ── 3. Depreciación Röss-Hödecke ──────────────────
        vida_util = VIDA_UTIL.get(inp.tipo_construccion, 70)
        dep_rate = 0.0
        if v_construccion_nuevo > 0 and inp.edad_anios > 0:
            dep_rate = depreciation_ross_heidecke(
                inp.edad_anios, vida_util, inp.estado_conservacion
            )
        v_construccion_dep = v_construccion_nuevo * (1 - dep_rate)

        # ── 4. Valor físico total (Ross-Heidecke) ─────────
        v_fisico = v_tierra + v_construccion_dep

        # ── 5. Ajuste por renta (opcional, solo ciertos tipos) ──
        v_renta = 0.0
        if inp.renta_mensual_usd > 0 and tipo in YIELD_REFERENCIA:
            yield_anual = YIELD_REFERENCIA[tipo]
            v_renta = (inp.renta_mensual_usd * 12) / yield_anual

        # Si hay valor por renta, se pondera 60/40 con valor físico
        if v_renta > 0:
            v_total_usd = v_fisico * 0.60 + v_renta * 0.40
            metodologia = "Costo de Reposición Deprecado (Ross-Heidecke) + Capitalización de Rentas"
        else:
            v_total_usd = v_fisico
            metodologia = "Costo de Reposición Deprecado (Ross-Heidecke)"

        # ── 6. Ajustes especiales por tipo ────────────────
        v_total_usd, notas = self._ajustes_especiales(inp, tipo, v_total_usd)

        # ── 7. Conversión VES ─────────────────────────────
        v_total_ves = v_total_usd * inp.tasa_bcv_ves

        # ── 8. Indicadores ────────────────────────────────
        area_ref = inp.area_construida_m2 if inp.area_construida_m2 > 0 else max(inp.area_terreno_m2, 1)
        v_m2 = v_total_usd / area_ref

        confidence = calcular_confidence_score(
            inp.comparables,
            inp.tiene_planos,
            inp.tiene_imagenes,
            inp.zona_tipo in FACTOR_ZONA_SIMPLE,
            tipo,
            inp.estado_conservacion,
        )

        # Delta geográfico estimado
        delta = self._delta_geografico(confidence)

        fz_final, fz_score, fz_desglose = calcular_factor_zona(inp)

        return ResultadoValuacion(
            hash_operacion=f"VM-AI-{datetime.now().strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:8].upper()}",
            timestamp=datetime.now().isoformat(timespec="seconds"),
            tipo_inmueble=tipo,
            direccion=inp.direccion,
            valor_tierra_usd=round(v_tierra, 2),
            valor_construccion_nuevo_usd=round(v_construccion_nuevo, 2),
            depreciacion_rate=dep_rate,
            valor_construccion_depreciado_usd=round(v_construccion_dep, 2),
            valor_total_usd=round(v_total_usd, 2),
            valor_total_ves=round(v_total_ves, 2),
            tasa_bcv=inp.tasa_bcv_ves,
            confidence_score=confidence,
            metodologia=metodologia,
            vida_util_aplicada=vida_util,
            factor_zona=fz_final,
            score_zona=fz_score,
            desglose_zona=fz_desglose,
            factor_acabados=FACTOR_ACABADOS.get(inp.acabados, 1.0),
            estado_conservacion=inp.estado_conservacion,
            valor_por_m2_usd=round(v_m2, 2),
            valor_renta_capitalizado_usd=round(v_renta, 2),
            delta_geografico=delta,
            parametros_entrada=inp.__dict__.copy(),
            notas_pericial=notas,
        )

    # ──────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────

    def _calcular_tierra(self, inp: InputValuacion, tipo: str) -> float:
        fz, _, _ = calcular_factor_zona(inp)

        if tipo == "finca":
            # Finca: precio por hectárea
            ha = inp.hectareas if inp.hectareas > 0 else inp.area_terreno_m2 / 10_000
            precio_ha_usd = (inp.valor_tierra_usd_m2 * 10_000) if inp.valor_tierra_usd_m2 > 0 else 3_000
            return ha * precio_ha_usd * fz

        elif tipo == "terreno":
            area = inp.area_terreno_m2
            precio = inp.valor_tierra_usd_m2 if inp.valor_tierra_usd_m2 > 0 else 80
            return area * precio * fz

        else:
            area = inp.area_terreno_m2
            precio = inp.valor_tierra_usd_m2 if inp.valor_tierra_usd_m2 > 0 else 120
            return area * precio * fz

    def _calcular_construccion_nueva(self, inp: InputValuacion, tipo: str) -> float:
        if tipo == "terreno":
            return 0.0

        # ── 1. Costo base según sistema constructivo ──────────────
        sistema = inp.tipo_construccion or ""
        if sistema and sistema in COSTO_CONSTRUCCION_M2:
            costo_base  = COSTO_CONSTRUCCION_M2[sistema]
            costo_min   = COSTO_MINIMO_M2.get(sistema, int(costo_base * 0.6))
        else:
            costo_base  = COSTO_BASE_M2.get(tipo, 400)
            costo_min   = int(costo_base * 0.55)

        # ── 2. Factores ────────────────────────────────────────────
        fz, _, _ = calcular_factor_zona(inp)
        fa = FACTOR_ACABADOS.get(inp.acabados, 1.0)
        ft = FACTOR_TECHO.get(inp.tipo_techo, 1.00) if inp.tipo_techo else 1.00

        # Ajuste galpón: altura libre
        if tipo == "galpon_industrial" and inp.altura_libre_m > 6:
            fa *= 1.0 + (inp.altura_libre_m - 6) * 0.02

        # ── 3. Valor bruto (fz afecta 60% a construcción, 100% al suelo) ──
        v = inp.area_construida_m2 * costo_base * fa * ft * (0.40 + fz * 0.60)

        # ── 4. Ajuste de piso para PH ──────────────────────────────
        if tipo == "propiedad_horizontal":
            v *= factor_piso(inp.piso_nivel)

        # ── 5. Piso mínimo pericial ────────────────────────────────
        v_minimo = inp.area_construida_m2 * costo_min
        v = max(v, v_minimo)

        return v

    def _ajustes_especiales(self, inp: InputValuacion, tipo: str, v: float):
        notas = []
        ajuste = 1.0

        # Habitaciones y baños (aplica a todos los tipos residenciales)
        if tipo in ("propiedad_horizontal", "vivienda_familiar", "local_comercial"):
            if inp.habitaciones > 0:
                fh = factor_habitaciones(inp.habitaciones)
                if abs(fh - 1.0) > 0.01:
                    ajuste += (fh - 1.0)
                    notas.append(f"{inp.habitaciones} hab: factor ×{fh:.2f}")
            if inp.banos > 0 or inp.medios_banos > 0:
                fb = factor_banos(inp.banos, inp.medios_banos)
                if abs(fb - 1.0) > 0.01:
                    ajuste += (fb - 1.0)
                    notas.append(f"{inp.banos} baños + {inp.medios_banos} medios: factor ×{fb:.2f}")

        # Amenidades
        amenidades = inp.amenidades or []
        for am in amenidades:
            pts = PUNTAJE_AMENIDADES.get(am, 0)
            if pts > 0:
                ajuste += pts
                notas.append(f"{am.replace('_',' ').title()}: +{pts*100:.0f}%")

        # Climatización
        fc = FACTOR_CLIMA.get(inp.clima, 1.00)
        if fc > 1.0:
            ajuste += (fc - 1.0)
            notas.append(f"A/C {inp.clima}: +{(fc-1)*100:.0f}%")

        if tipo == "propiedad_horizontal":
            if inp.tiene_ascensor:
                ajuste += 0.03
                notas.append("Ascensor: +3%")
            if inp.puesto_estacionamiento > 0:
                bonus = inp.puesto_estacionamiento * 0.025
                ajuste += bonus
                notas.append(f"{inp.puesto_estacionamiento} puesto(s) estacionamiento: +{bonus*100:.1f}%")

        elif tipo == "galpon_industrial":
            if inp.tiene_rampa:
                ajuste += 0.04
                notas.append("Rampa de carga: +4%")
            if inp.capacidad_electrica_kva >= 200:
                ajuste += 0.05
                notas.append(f"Alta capacidad eléctrica ({inp.capacidad_electrica_kva} kVA): +5%")

        elif tipo == "finca":
            if inp.mejoras_agricolas:
                ajuste += 0.12
                notas.append("Mejoras agrícolas (riego, galpones, cercas): +12%")

        elif tipo == "terreno":
            notas.append("Terreno: no aplica depreciación por construcción.")

        return round(v * ajuste, 2), notas

    def _delta_geografico(self, confidence: float) -> str:
        if confidence >= 85:  return "Bajo (± 5%)"
        if confidence >= 70:  return "Medio (± 10%)"
        if confidence >= 55:  return "Alto (± 15%)"
        return "Muy Alto (± 25%)"
