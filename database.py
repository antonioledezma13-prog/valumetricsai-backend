"""
ValuMetrics AI — Capa de Base de Datos MongoDB Atlas
=====================================================
Usa Motor (async MongoDB driver) para no bloquear FastAPI.
Si MONGODB_URI no está configurado, cae back a dicts en memoria.

Variables de entorno requeridas (.env):
  MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/valumetrics
"""

import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List

MONGODB_URI = os.getenv("MONGODB_URI", "")
DB_NAME     = os.getenv("MONGODB_DB", "valumetrics")

# ── Intento de conexión async con Motor ──────────────────────
_client = None
_db     = None
_mongo_ok = False

async def init_db():
    global _client, _db, _mongo_ok
    if not MONGODB_URI:
        print("[DB] Sin MONGODB_URI — usando almacenamiento en memoria")
        return
    try:
        import motor.motor_asyncio as motor_async
        _client = motor_async.AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        
        # Ahora que _client existe, puedes asignar _db aquí
        _db = _client.get_database(DB_NAME) 
        
        # Test connection (haciendo ping a la base de datos específica)
        await _db.command("ping")
        _mongo_ok = True
        print(f"[DB] MongoDB Atlas conectado: {DB_NAME} ✓")
        
        # Crear índices
        await _db.users.create_index("email", unique=True)
    except Exception as e:
        print(f"[DB] Error al conectar: {e}")
        _mongo_ok = False

async def close_db():
    global _client
    if _client:
        _client.close()

# ──────────────────────────────────────────
#  Fallback en memoria (desarrollo/sin Mongo)
# ──────────────────────────────────────────
_mem_users:       Dict = {}
_mem_valuaciones: Dict = {}
_mem_pagos:       Dict = {}

# ──────────────────────────────────────────
#  USUARIOS
# ──────────────────────────────────────────

async def crear_usuario(user_data: dict) -> bool:
    if _mongo_ok:
        try:
            await _db.users.insert_one(user_data)
            return True
        except Exception as e:
            if "duplicate" in str(e).lower():
                return False
            print(f"[DB] crear_usuario error: {e}")
            return False
    else:
        if user_data["email"] in _mem_users:
            return False
        _mem_users[user_data["email"]] = user_data
        return True

async def obtener_usuario(email: str) -> Optional[dict]:
    if _mongo_ok:
        try:
            doc = await _db.users.find_one({"email": email}, {"_id": 0})
            return doc
        except Exception as e:
            print(f"[DB] obtener_usuario error: {e}")
    return _mem_users.get(email)

async def actualizar_usuario(email: str, update: dict) -> bool:
    if _mongo_ok:
        try:
            await _db.users.update_one({"email": email}, {"$set": update})
            return True
        except Exception as e:
            print(f"[DB] actualizar_usuario error: {e}")
    user = _mem_users.get(email)
    if user:
        user.update(update)
        return True
    return False

async def listar_usuarios() -> List[dict]:
    if _mongo_ok:
        try:
            cursor = _db.users.find({}, {"_id":0,"password_hash":0})
            return await cursor.to_list(length=1000)
        except Exception as e:
            print(f"[DB] listar_usuarios error: {e}")
    return [{k:v for k,v in u.items() if k != "password_hash"}
            for u in _mem_users.values()]

# ──────────────────────────────────────────
#  VALUACIONES
# ──────────────────────────────────────────

async def guardar_valuacion(resultado_dict: dict) -> bool:
    resultado_dict["created_at"] = datetime.utcnow().isoformat()
    if _mongo_ok:
        try:
            await _db.valuaciones.replace_one(
                {"hash_operacion": resultado_dict["hash_operacion"]},
                resultado_dict, upsert=True
            )
            return True
        except Exception as e:
            print(f"[DB] guardar_valuacion error: {e}")
    _mem_valuaciones[resultado_dict["hash_operacion"]] = resultado_dict
    return True

async def obtener_valuacion(hash_op: str) -> Optional[dict]:
    if _mongo_ok:
        try:
            doc = await _db.valuaciones.find_one({"hash_operacion": hash_op}, {"_id": 0})
            return doc
        except Exception as e:
            print(f"[DB] obtener_valuacion error: {e}")
    return _mem_valuaciones.get(hash_op)

async def historial_usuario(usuario_id: str, limite: int = 50) -> List[dict]:
    if _mongo_ok:
        try:
            cursor = _db.valuaciones.find(
                {"parametros_entrada.usuario_id": usuario_id},
                {"_id":0,"hash_operacion":1,"timestamp":1,
                 "tipo_inmueble":1,"direccion":1,
                 "valor_total_usd":1,"confidence_score":1},
            ).sort("created_at", -1).limit(limite)
            return await cursor.to_list(length=limite)
        except Exception as e:
            print(f"[DB] historial error: {e}")
    return [
        {"hash": k, "timestamp": v.get("timestamp",""),
         "tipo": v.get("tipo_inmueble",""), "direccion": v.get("direccion",""),
         "valor_usd": v.get("valor_total_usd",0),
         "confidence": v.get("confidence_score",0)}
        for k, v in _mem_valuaciones.items()
        if v.get("parametros_entrada",{}).get("usuario_id") == usuario_id
    ]

# ──────────────────────────────────────────
#  PAGOS
# ──────────────────────────────────────────

_client = None
_db = None 
_mongo_ok = False

async def guardar_pago(pago: dict) -> bool:
    global _db
    pago["created_at"] = datetime.utcnow().isoformat()
    
    # Usamos paypal_order_id como clave principal, que es la que realmente existe
    order_key = pago.get("paypal_order_id")
    
    if not order_key:
        print(f"[DB Error] ¡El pago no tiene 'paypal_order_id'! Campos recibidos: {list(pago.keys())}")
        return False

    if _mongo_ok and _db is not None:
        try:
            # Usamos order_key en lugar de pago["order_id"]
            await _db.pagos.replace_one(
                {"paypal_order_id": order_key}, 
                pago, 
                upsert=True
            )
            print(f"[DB] Pago guardado exitosamente en MongoDB con ID: {order_key}")
            return True
        except Exception as e:
            print(f"[DB] ¡ERROR CRÍTICO AL GUARDAR EN MONGO!: {e}")
    
    # Fallback a memoria usando la clave correcta
    _mem_pagos[order_key] = pago
    return True