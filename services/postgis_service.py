"""
ValuMetrics AI — Servicio PostGIS / Supabase
=============================================
Gestiona datos geoespaciales para el mapa de calor de valores inmobiliarios.

Funciones:
  - Guardar valuaciones geolocalizadas en PostGIS
  - Consultar puntos de calor por bounding box del mapa
  - Calcular precio promedio ponderado por zona ($/m²)
  - Generar GeoJSON para Mapbox heatmap layer

Configurar en .env:
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_KEY=eyJ...
"""

import os
import json
import math
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

# ── Supabase client ──────────────────────────────────────────
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


# ─────────────────────────────────────────────────────────────
#  SQL de inicialización (ejecutar una vez en Supabase)
# ─────────────────────────────────────────────────────────────

INIT_SQL = """
-- Habilitar extensión PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- Tabla de valuaciones geolocalizadas
CREATE TABLE IF NOT EXISTS valuaciones_geo (
    id              SERIAL PRIMARY KEY,
    hash_operacion  TEXT UNIQUE NOT NULL,
    usuario_id      TEXT NOT NULL,
    tipo_inmueble   TEXT NOT NULL,
    direccion       TEXT,
    ciudad          TEXT,
    -- Geometría punto (lon, lat en WGS84)
    ubicacion       GEOGRAPHY(POINT, 4326),
    -- Valores
    valor_usd       NUMERIC(14, 2),
    valor_m2_usd    NUMERIC(10, 2),
    area_construida NUMERIC(10, 2),
    area_terreno    NUMERIC(10, 2),
    edad_anios      NUMERIC(5, 1),
    acabados        TEXT,
    zona_tipo       TEXT,
    confidence      NUMERIC(5, 2),
    -- Metadata
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Índice espacial para consultas de mapa
CREATE INDEX IF NOT EXISTS idx_valuaciones_geo_ubicacion
    ON valuaciones_geo USING GIST (ubicacion);

-- Índice por ciudad para filtros rápidos
CREATE INDEX IF NOT EXISTS idx_valuaciones_geo_ciudad
    ON valuaciones_geo (ciudad);

-- Vista para el mapa de calor (agrupada por zona ~200m)
CREATE OR REPLACE VIEW heatmap_zonas AS
SELECT
    ST_X(ST_Centroid(ST_Collect(ubicacion::geometry))) AS lon,
    ST_Y(ST_Centroid(ST_Collect(ubicacion::geometry))) AS lat,
    AVG(valor_m2_usd) AS precio_m2_promedio,
    COUNT(*) AS n_muestras,
    zona_tipo,
    ciudad
FROM valuaciones_geo
WHERE valor_m2_usd > 0
GROUP BY
    ST_SnapToGrid(ubicacion::geometry, 0.002),  -- ~200m de resolución
    zona_tipo,
    ciudad;
"""


# ─────────────────────────────────────────────────────────────
#  Dataclass de punto geo
# ─────────────────────────────────────────────────────────────

@dataclass
class PuntoGeo:
    lon: float
    lat: float
    valor_m2_usd: float
    tipo_inmueble: str
    zona_tipo: str
    n_muestras: int = 1


# ─────────────────────────────────────────────────────────────
#  Datos semilla de Caracas (precargados para funcionar sin datos reales)
# ─────────────────────────────────────────────────────────────
# Precios referenciales por zona (USD/m² construido) — Caracas 2025

DATOS_SEMILLA_CARACAS = [
    # (lon, lat, precio_m2, zona)
    # Prime — Las Mercedes, Altamira, La Castellana, Chacao
    (-66.8530, 10.4940, 1150, "prime"),   # Las Mercedes centro
    (-66.8490, 10.4960, 1080, "prime"),   # Las Mercedes este
    (-66.8450, 10.4880, 980,  "prime"),   # El Rosal
    (-66.8510, 10.5010, 1200, "prime"),   # Altamira
    (-66.8420, 10.5050, 1100, "prime"),   # La Castellana
    (-66.8380, 10.5030, 950,  "prime"),   # Chacao
    (-66.8340, 10.4990, 900,  "prime"),   # Bello Campo
    # Residencial estándar
    (-66.8700, 10.4900, 680,  "residencial"),  # La Florida
    (-66.8780, 10.5000, 620,  "residencial"),  # Los Palos Grandes
    (-66.8600, 10.5100, 590,  "residencial"),  # La Campiña
    (-66.8900, 10.4800, 520,  "residencial"),  # Sabana Grande
    (-66.8820, 10.4750, 480,  "residencial"),  # Chacaíto
    (-66.8650, 10.4820, 550,  "residencial"),  # Santa Eduvigis
    (-66.8480, 10.5200, 640,  "residencial"),  # Country Club
    (-66.8300, 10.5100, 580,  "residencial"),  # El Pedregal
    # Periurbano
    (-66.9100, 10.4700, 320,  "periurbano"),  # Bello Monte
    (-66.9200, 10.4600, 280,  "periurbano"),  # Colinas BM
    (-66.8200, 10.4800, 350,  "periurbano"),  # El Retiro
    (-66.8100, 10.4700, 300,  "periurbano"),  # Las Mercedes sur
    # Valle — periurbano también
    (-66.8750, 10.4500, 260,  "periurbano"),  # Valle Arriba
    (-66.9000, 10.4400, 240,  "periurbano"),  # La Rinconada
]


# ─────────────────────────────────────────────────────────────
#  Servicio PostGIS
# ─────────────────────────────────────────────────────────────

class PostGISService:

    def __init__(self):
        self._client: Optional[object] = None
        self._connected = False
        self._intentar_conexion()

    def _intentar_conexion(self):
        if not SUPABASE_AVAILABLE:
            print("[PostGIS] supabase-py no instalado — modo datos semilla")
            return
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[PostGIS] Sin credenciales — modo datos semilla")
            return
        try:
            self._client = create_client(SUPABASE_URL, SUPABASE_KEY)
            self._connected = True
            print("[PostGIS] Conectado a Supabase")
        except Exception as e:
            print(f"[PostGIS] Error conectando: {e} — modo datos semilla")

    # ──────────────────────────────────────────
    #  Guardar valuación geolocalizda
    # ──────────────────────────────────────────

    def guardar_valuacion(
        self,
        resultado,          # ResultadoValuacion del engine
        lon: float,
        lat: float,
    ) -> bool:
        """
        Persiste una valuación con su ubicación geográfica.
        Retorna True si se guardó, False si falló.
        """
        if not self._connected:
            return False

        try:
            p = resultado.parametros_entrada
            data = {
                "hash_operacion":  resultado.hash_operacion,
                "usuario_id":      p.get("usuario_id", ""),
                "tipo_inmueble":   resultado.tipo_inmueble,
                "direccion":       resultado.direccion,
                "ciudad":          p.get("ciudad", ""),
                "ubicacion":       f"POINT({lon} {lat})",
                "valor_usd":       resultado.valor_total_usd,
                "valor_m2_usd":    resultado.valor_por_m2_usd,
                "area_construida": p.get("area_construida_m2", 0),
                "area_terreno":    p.get("area_terreno_m2", 0),
                "edad_anios":      p.get("edad_anios", 0),
                "acabados":        p.get("acabados", ""),
                "zona_tipo":       p.get("zona_tipo", ""),
                "confidence":      resultado.confidence_score,
            }
            self._client.table("valuaciones_geo").upsert(data).execute()
            return True
        except Exception as e:
            print(f"[PostGIS] Error guardando: {e}")
            return False

    # ──────────────────────────────────────────
    #  Obtener datos del mapa de calor
    # ──────────────────────────────────────────

    def obtener_heatmap(
        self,
        ciudad: str = "Caracas",
        bbox: Optional[Tuple[float,float,float,float]] = None,
    ) -> Dict:
        """
        Retorna GeoJSON FeatureCollection para Mapbox heatmap layer.
        Si hay conexión Supabase, usa datos reales.
        Si no, usa datos semilla de Caracas.

        Args:
            ciudad: Filtrar por ciudad
            bbox: (lon_min, lat_min, lon_max, lat_max) — área visible del mapa

        Returns:
            GeoJSON FeatureCollection con propiedad 'precio_m2' por punto
        """
        if self._connected:
            puntos = self._heatmap_desde_supabase(ciudad, bbox)
        else:
            puntos = self._heatmap_desde_semilla(bbox)

        return self._a_geojson(puntos)

    def _heatmap_desde_supabase(self, ciudad: str, bbox) -> List[PuntoGeo]:
        try:
            query = self._client.table("heatmap_zonas").select("*")
            if ciudad:
                query = query.eq("ciudad", ciudad)
            if bbox:
                lon_min, lat_min, lon_max, lat_max = bbox
                query = query.gte("lon", lon_min).lte("lon", lon_max)
                query = query.gte("lat", lat_min).lte("lat", lat_max)

            res = query.execute()
            puntos = []
            for row in res.data:
                puntos.append(PuntoGeo(
                    lon=row["lon"],
                    lat=row["lat"],
                    valor_m2_usd=row["precio_m2_promedio"],
                    tipo_inmueble="mixto",
                    zona_tipo=row.get("zona_tipo", "residencial"),
                    n_muestras=row.get("n_muestras", 1),
                ))
            # Complementar con semilla si hay pocos datos reales
            if len(puntos) < 5:
                puntos.extend(self._heatmap_desde_semilla(bbox))
            return puntos
        except Exception as e:
            print(f"[PostGIS] Error heatmap Supabase: {e}")
            return self._heatmap_desde_semilla(bbox)

    def _heatmap_desde_semilla(self, bbox) -> List[PuntoGeo]:
        puntos = []
        for lon, lat, precio, zona in DATOS_SEMILLA_CARACAS:
            if bbox:
                lon_min, lat_min, lon_max, lat_max = bbox
                if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                    continue
            puntos.append(PuntoGeo(
                lon=lon, lat=lat,
                valor_m2_usd=precio,
                tipo_inmueble="mixto",
                zona_tipo=zona,
                n_muestras=1,
            ))
        return puntos

    def _a_geojson(self, puntos: List[PuntoGeo]) -> Dict:
        """Convierte lista de PuntoGeo a GeoJSON para Mapbox."""
        features = []
        if not puntos:
            return {"type": "FeatureCollection", "features": []}

        max_precio = max(p.valor_m2_usd for p in puntos)

        for p in puntos:
            # Normalizar peso 0–1 para el heatmap
            peso = p.valor_m2_usd / max_precio if max_precio > 0 else 0.5
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [p.lon, p.lat],
                },
                "properties": {
                    "precio_m2":  round(p.valor_m2_usd, 2),
                    "peso":       round(peso, 4),
                    "zona":       p.zona_tipo,
                    "muestras":   p.n_muestras,
                },
            })

        return {"type": "FeatureCollection", "features": features}

    # ──────────────────────────────────────────
    #  Comparables cercanos
    # ──────────────────────────────────────────

    def obtener_comparables(
        self,
        lon: float,
        lat: float,
        radio_km: float = 1.0,
        tipo_inmueble: str = "",
        limite: int = 5,
    ) -> List[Dict]:
        """
        Retorna valuaciones cercanas para usar como comparables de mercado.
        Ordenadas por distancia ascendente.
        """
        if not self._connected:
            return self._comparables_semilla(lon, lat, radio_km, tipo_inmueble, limite)

        try:
            radio_m = radio_km * 1000
            rpc_params = {
                "lon": lon,
                "lat": lat,
                "radio": radio_m,
                "tipo": tipo_inmueble or None,
                "lim": limite,
            }
            # RPC function en Supabase (ver función SQL abajo)
            res = self._client.rpc("comparables_cercanos", rpc_params).execute()
            return res.data or []
        except Exception as e:
            print(f"[PostGIS] Error comparables: {e}")
            return self._comparables_semilla(lon, lat, radio_km, tipo_inmueble, limite)

    def _comparables_semilla(self, lon, lat, radio_km, tipo, limite) -> List[Dict]:
        """Comparables desde datos semilla (sin DB)."""
        resultado = []
        for s_lon, s_lat, precio, zona in DATOS_SEMILLA_CARACAS:
            dist = self._distancia_km(lon, lat, s_lon, s_lat)
            if dist <= radio_km:
                resultado.append({
                    "lon": s_lon,
                    "lat": s_lat,
                    "distancia_km": round(dist, 3),
                    "precio_m2_usd": precio,
                    "zona_tipo": zona,
                    "fuente": "referencia_mercado",
                })
        resultado.sort(key=lambda x: x["distancia_km"])
        return resultado[:limite]

    @staticmethod
    def _distancia_km(lon1, lat1, lon2, lat2) -> float:
        """Haversine distance en km."""
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))


# Instancia singleton
postgis_service = PostGISService()


# ─────────────────────────────────────────────────────────────
#  SQL para Supabase — función de comparables cercanos
# ─────────────────────────────────────────────────────────────
COMPARABLES_SQL = """
CREATE OR REPLACE FUNCTION comparables_cercanos(
    lon   FLOAT,
    lat   FLOAT,
    radio FLOAT,       -- metros
    tipo  TEXT,
    lim   INT DEFAULT 5
)
RETURNS TABLE (
    hash_operacion TEXT,
    direccion      TEXT,
    tipo_inmueble  TEXT,
    valor_m2_usd   NUMERIC,
    distancia_m    FLOAT,
    zona_tipo      TEXT
)
LANGUAGE sql STABLE AS $$
    SELECT
        hash_operacion,
        direccion,
        tipo_inmueble,
        valor_m2_usd,
        ST_Distance(ubicacion, ST_MakePoint(lon, lat)::geography) AS distancia_m,
        zona_tipo
    FROM valuaciones_geo
    WHERE
        ST_DWithin(ubicacion, ST_MakePoint(lon, lat)::geography, radio)
        AND (tipo IS NULL OR tipo_inmueble = tipo)
    ORDER BY distancia_m
    LIMIT lim;
$$;
"""
