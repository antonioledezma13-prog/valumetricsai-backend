"""
ValuMetrics AI — Backend Principal (FastAPI)
============================================
Rutas:
  POST /auth/register
  POST /auth/login
  POST /valuation/calculate
  GET  /valuation/pdf/{hash_operacion}
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
import uuid, hashlib, jwt, io
from datetime import datetime, timedelta
from services.valuation_engine import MotorValuacion, InputValuacion, ResultadoValuacion
from services.pdf_service import generar_informe_pdf

# Servicios opcionales — no crashean si el paquete no está instalado
try:
    from services.vision_service import vision_service
    VISION_OK = True
except Exception as _e:
    print(f"[Vision] No disponible: {_e}")
    vision_service = None
    VISION_OK = False

try:
    from services.postgis_service import postgis_service
    POSTGIS_OK = True
except Exception as _e:
    print(f"[PostGIS] No disponible: {_e}")
    postgis_service = None
    POSTGIS_OK = False

# ──────────────────────────────────────────────────────
#  Configuración
# ──────────────────────────────────────────────────────
SECRET_KEY = "valumetrics-secret-2025-change-in-prod"
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 720  # 30 días

app = FastAPI(
    title="ValuMetrics AI API",
    description="Motor de valoración inmobiliaria pericial con IA",
    version="2.0.0",
)

# Orígenes permitidos — ajustar en producción
ALLOWED_ORIGINS = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:5501",
    "http://127.0.0.1:5501",
    "http://localhost:8080",
    "http://localhost:3000",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "null",   # file:// requests
    # Agregar dominio de producción aquí:
    # "https://valumetricsai.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción: cambiar a ALLOWED_ORIGINS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# ──────────────────────────────────────────────────────
#  Almacenamiento en memoria (reemplazar por MongoDB/PostGIS en producción)
# ──────────────────────────────────────────────────────
users_db: dict = {}          # email → user dict
valuations_db: dict = {}     # hash_operacion → ResultadoValuacion

# ──────────────────────────────────────────────────────
#  Schemas Pydantic
# ──────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    nombre: str
    apellido: str
    email: str
    password: str
    telefono: Optional[str] = ""
    empresa: Optional[str] = ""
    rol: str = "tasador"   # tasador | perito | inversor | admin

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres")
        return v

class LoginRequest(BaseModel):
    email: str
    password: str

class ValuationRequest(BaseModel):
    # Identificación
    tipo_inmueble: str
    direccion: str
    ciudad: str
    zona_tipo: str = "residencial"
    # Terreno
    area_terreno_m2: float = 0.0
    valor_tierra_usd_m2: float = 0.0
    # Construcción
    area_construida_m2: float = 0.0
    edad_anios: float = 0.0
    tipo_construccion: str = "concreto_armado"
    acabados: str = "medio"
    estado_conservacion: str = "normal"
    # PH
    piso_nivel: int = 1
    tiene_ascensor: bool = False
    tiene_estacionamiento: bool = False
    puesto_estacionamiento: int = 0
    # Finca
    hectareas: float = 0.0
    mejoras_agricolas: bool = False
    # Galpón
    altura_libre_m: float = 0.0
    tiene_rampa: bool = False
    capacidad_electrica_kva: float = 0.0
    # Mercado
    comparables: int = 0
    renta_mensual_usd: float = 0.0
    # Meta
    tiene_planos: bool = False
    tiene_imagenes: bool = False
    tasa_bcv_ves: float = 36000.0
    # Características físicas
    tipo_techo:     str  = ""
    habitaciones:   int  = 0
    banos:          int  = 0
    medios_banos:   int  = 0
    estacionamientos: int = 0
    amenidades:     list = []
    clima:          str  = "individual"
    notas:          str  = ""

# ──────────────────────────────────────────────────────
#  Utilidades Auth
# ──────────────────────────────────────────────────────

def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def create_token(email: str, nombre: str, rol: str) -> str:
    payload = {
        "sub": email,
        "nombre": nombre,
        "rol": rol,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

# ──────────────────────────────────────────────────────
#  Rutas de autenticación
# ──────────────────────────────────────────────────────

@app.post("/auth/register", status_code=201)
async def register(req: RegisterRequest):
    if req.email in users_db:
        raise HTTPException(status_code=409, detail="El email ya está registrado")
    user_id = str(uuid.uuid4())
    users_db[req.email] = {
        "id": user_id,
        "nombre": req.nombre,
        "apellido": req.apellido,
        "email": req.email,
        "password_hash": hash_password(req.password),
        "telefono": req.telefono,
        "empresa": req.empresa,
        "rol": req.rol,
        "created_at": datetime.utcnow().isoformat(),
        "valuaciones_count": 0,
    }
    token = create_token(req.email, req.nombre, req.rol)
    return {
        "message": "Usuario registrado exitosamente",
        "token": token,
        "user": {
            "id": user_id,
            "nombre": req.nombre,
            "apellido": req.apellido,
            "email": req.email,
            "rol": req.rol,
        }
    }

@app.post("/auth/login")
async def login(req: LoginRequest):
    user = users_db.get(req.email)
    if not user or user["password_hash"] != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = create_token(req.email, user["nombre"], user["rol"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "nombre": user["nombre"],
            "apellido": user["apellido"],
            "email": user["email"],
            "rol": user["rol"],
            "valuaciones_count": user["valuaciones_count"],
        }
    }

@app.get("/auth/me")
async def me(payload: dict = Depends(verify_token)):
    user = users_db.get(payload["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {k: v for k, v in user.items() if k != "password_hash"}

# ──────────────────────────────────────────────────────
#  Rutas de valuación
# ──────────────────────────────────────────────────────

@app.post("/valuation/calculate")
async def calculate(req: ValuationRequest, payload: dict = Depends(verify_token)):
    inp = InputValuacion(
        tipo_inmueble=req.tipo_inmueble,
        direccion=req.direccion,
        ciudad=req.ciudad,
        zona_tipo=req.zona_tipo,
        zona_clase=req.zona_clase,
        zona_acceso=req.zona_acceso,
        zona_vias=req.zona_vias,
        zona_agua=req.zona_agua,
        zona_electricidad=req.zona_electricidad,
        zona_gas=req.zona_gas,
        zona_aseo=req.zona_aseo,
        zona_uso_suelo=req.zona_uso_suelo,
        zona_densidad=req.zona_densidad,
        zona_ambito=req.zona_ambito,
        zona_seguridad=req.zona_seguridad,
        zona_equipamiento=req.zona_equipamiento,
        area_terreno_m2=req.area_terreno_m2,
        valor_tierra_usd_m2=req.valor_tierra_usd_m2,
        area_construida_m2=req.area_construida_m2,
        edad_anios=req.edad_anios,
        tipo_construccion=req.tipo_construccion,
        acabados=req.acabados,
        estado_conservacion=req.estado_conservacion,
        piso_nivel=req.piso_nivel,
        tiene_ascensor=req.tiene_ascensor,
        tiene_estacionamiento=req.tiene_estacionamiento,
        puesto_estacionamiento=req.puesto_estacionamiento,
        hectareas=req.hectareas,
        mejoras_agricolas=req.mejoras_agricolas,
        altura_libre_m=req.altura_libre_m,
        tiene_rampa=req.tiene_rampa,
        capacidad_electrica_kva=req.capacidad_electrica_kva,
        comparables=req.comparables,
        renta_mensual_usd=req.renta_mensual_usd,
        tiene_planos=req.tiene_planos,
        tiene_imagenes=req.tiene_imagenes,
        tasa_bcv_ves=req.tasa_bcv_ves,
        usuario_id=payload["sub"],
        tipo_techo=req.tipo_techo,
        habitaciones=req.habitaciones,
        banos=req.banos,
        medios_banos=req.medios_banos,
        estacionamientos=req.estacionamientos,
        amenidades=req.amenidades,
        clima=req.clima,
        notas=req.notas,
    )
    try:
        motor = MotorValuacion()
        resultado = motor.valorar(inp)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Persistir resultado
    valuations_db[resultado.hash_operacion] = resultado

    # Incrementar contador del usuario
    if payload["sub"] in users_db:
        users_db[payload["sub"]]["valuaciones_count"] += 1

    return resultado.__dict__

@app.get("/valuation/pdf/{hash_operacion}")
async def download_pdf(hash_operacion: str, payload: dict = Depends(verify_token)):
    resultado = valuations_db.get(hash_operacion)
    if not resultado:
        raise HTTPException(status_code=404, detail="Valuación no encontrada")

    user = users_db.get(payload["sub"], {})
    nombre_perito = f"{user.get('nombre', '')} {user.get('apellido', '')}".strip()
    pdf_bytes = generar_informe_pdf(resultado, nombre_perito=nombre_perito)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ValuMetrics_{hash_operacion}.pdf"'},
    )

# ─── Ruta principal para PDF: recibe resultado del frontend y genera PDF ───
class PDFDirectRequest(BaseModel):
    resultado: dict    # El objeto resultado completo del cálculo JS
    nombre_perito: str = ""

@app.post("/valuation/pdf-direct")
async def pdf_direct(req: PDFDirectRequest, payload: dict = Depends(verify_token)):
    """
    Genera PDF directamente desde el resultado calculado en el frontend.
    No requiere que el resultado esté en memoria del servidor.
    """
    from dataclasses import dataclass, field as dc_field
    from typing import Any

    user = users_db.get(payload["sub"], {})
    nombre_perito = req.nombre_perito or f"{user.get('nombre','')} {user.get('apellido','')}".strip()

    # Reconstruir ResultadoValuacion desde el dict del frontend
    r = req.resultado
    resultado = ResultadoValuacion(
        hash_operacion=r.get("hash_operacion", f"VM-AI-DIRECT-{uuid.uuid4().hex[:8].upper()}"),
        timestamp=r.get("timestamp", datetime.utcnow().isoformat()),
        tipo_inmueble=r.get("tipo_inmueble", ""),
        direccion=r.get("direccion", ""),
        valor_tierra_usd=float(r.get("valor_tierra_usd", 0)),
        valor_construccion_nuevo_usd=float(r.get("valor_construccion_nuevo_usd", 0)),
        depreciacion_rate=float(r.get("depreciacion_rate", 0)),
        valor_construccion_depreciado_usd=float(r.get("valor_construccion_depreciado_usd", 0)),
        valor_total_usd=float(r.get("valor_total_usd", 0)),
        valor_total_ves=float(r.get("valor_total_ves", 0)),
        tasa_bcv=float(r.get("tasa_bcv", 36000)),
        confidence_score=float(r.get("confidence_score", 0)),
        metodologia=r.get("metodologia", "Costo de Reposición Deprecado (Ross-Heidecke)"),
        vida_util_aplicada=float(r.get("vida_util_aplicada", 70)),
        factor_zona=float(r.get("factor_zona", 1.0)),
        factor_acabados=float(r.get("factor_acabados", 1.0)),
        estado_conservacion=r.get("estado_conservacion", "normal"),
        valor_por_m2_usd=float(r.get("valor_por_m2_usd", 0)),
        valor_renta_capitalizado_usd=float(r.get("valor_renta_capitalizado_usd", 0)),
        delta_geografico=r.get("delta_geografico", ""),
        score_zona=r.get("score_zona"),
        desglose_zona=r.get("desglose_zona") or {},
        parametros_entrada=(lambda pe: {
            # Merge: use parametros_entrada if available, fallback to top-level r keys
            **{k: v for k, v in r.items() if k not in ('parametros_entrada','notas_pericial')},
            **(pe if isinstance(pe, dict) else {}),
        })(r.get("parametros_entrada")),
        notas_pericial=r.get("notas_pericial") or [],
    )

    # Guardar también en memoria para referencia futura
    valuations_db[resultado.hash_operacion] = resultado

    try:
        pdf_bytes = generar_informe_pdf(resultado, nombre_perito=nombre_perito)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {str(e)}")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ValuMetrics_{resultado.hash_operacion}.pdf"'},
    )

@app.get("/valuation/history")
async def history(payload: dict = Depends(verify_token)):
    user_email = payload["sub"]
    user_vals = [
        {
            "hash": k,
            "timestamp": v.timestamp,
            "tipo": v.tipo_inmueble,
            "direccion": v.direccion,
            "valor_usd": v.valor_total_usd,
            "confidence": v.confidence_score,
        }
        for k, v in valuations_db.items()
        if v.parametros_entrada.get("usuario_id") == user_email
    ]
    return {"valuaciones": user_vals}

# ──────────────────────────────────────────────────────
#  Ruta: Análisis de patologías (YOLOv8)
# ──────────────────────────────────────────────────────

class VisionRequest(BaseModel):
    imagenes_b64: list       # Lista de imágenes en base64
    estado_actual: str = "normal"
    conf_threshold: float = 0.35

@app.post("/vision/analizar")
async def analizar_patologias(req: VisionRequest, payload: dict = Depends(verify_token)):
    if not req.imagenes_b64:
        raise HTTPException(status_code=422, detail="Se requiere al menos una imagen")
    if not VISION_OK or vision_service is None:
        return {"analizado": False, "mensaje": "Módulo Vision no instalado. Instala: pip install ultralytics Pillow"}
    resultado = vision_service.analizar_imagenes(
        req.imagenes_b64,
        estado_actual=req.estado_actual,
        conf_threshold=req.conf_threshold,
    )
    return resultado.__dict__

# ──────────────────────────────────────────────────────
#  Ruta: Mapa de calor PostGIS
# ──────────────────────────────────────────────────────

@app.get("/geo/heatmap")
async def heatmap(
    ciudad: str = "Caracas",
    lon_min: float = -67.05,
    lat_min: float = 10.40,
    lon_max: float = -66.75,
    lat_max: float = 10.55,
):
    """GeoJSON del mapa de calor de precios $/m²."""
    if not POSTGIS_OK or postgis_service is None:
        return {"type": "FeatureCollection", "features": []}
    geojson = postgis_service.obtener_heatmap(
        ciudad=ciudad,
        bbox=(lon_min, lat_min, lon_max, lat_max),
    )
    return geojson

@app.get("/geo/comparables")
async def comparables(
    lon: float,
    lat: float,
    radio_km: float = 1.0,
    tipo: str = "",
    payload: dict = Depends(verify_token),
):
    """Valuaciones cercanas para usar como comparables de mercado."""
    if not POSTGIS_OK or postgis_service is None:
        return {"comparables": []}
    data = postgis_service.obtener_comparables(lon, lat, radio_km, tipo)
    return {"comparables": data}

@app.post("/geo/guardar")
async def guardar_geo(
    hash_operacion: str,
    lon: float,
    lat: float,
    payload: dict = Depends(verify_token),
):
    """Asocia coordenadas a una valuación ya calculada."""
    resultado = valuations_db.get(hash_operacion)
    if not resultado:
        raise HTTPException(status_code=404, detail="Valuación no encontrada")
    if not POSTGIS_OK or postgis_service is None:
        return {"guardado": False, "hash": hash_operacion}
    ok = postgis_service.guardar_valuacion(resultado, lon, lat)
    return {"guardado": ok, "hash": hash_operacion}

@app.get("/health")

async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
