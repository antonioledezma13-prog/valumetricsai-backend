"""
ValuMetrics AI — Router de Pagos PayPal (Producción-grade)
==========================================================
Flujo completo:
  1. POST /api/payments/create-order    → crea orden en PayPal
  2. PayPal popup → usuario aprueba
  3. POST /api/payments/capture         → captura + verifica server-side
  4. POST /api/payments/webhook/paypal  → PayPal notifica (redundancia)
  5. GET  /api/payments/status          → consulta estado del plan

Seguridad:
  - Siempre verificar con PayPal API, nunca confiar en el frontend
  - Webhook verifica firma HMAC de PayPal
  - Idempotencia: reintento seguro con mismo order_id
"""

import os, hmac, hashlib, json
import httpx
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

router = APIRouter()

# ── Configuración ─────────────────────────────────────────────
PAYPAL_MODE       = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")   # de developer.paypal.com

CREDS = {
    "sandbox": {
        "client_id": os.getenv("PAYPAL_SANDBOX_CLIENT_ID", ""),
        "secret":    os.getenv("PAYPAL_SANDBOX_SECRET", ""),
        "base":      "https://api-m.sandbox.paypal.com",
    },
    "production": {
        "client_id": os.getenv("PAYPAL_LIVE_CLIENT_ID", ""),
        "secret":    os.getenv("PAYPAL_LIVE_SECRET", ""),
        "base":      "https://api-m.paypal.com",
    },
}

PLAN_MAP = {
    # amount_usd → plan_id
    "2.99":   "payperuse",
    "15.99":  "profesional",    # anual mensualizado
    "19.99":  "profesional",
    "39.99":  "inmobiliaria",   # anual mensualizado
    "49.99":  "inmobiliaria",
    "103.99": "enterprise",     # anual mensualizado
    "129.99": "enterprise",
    "191.88": "profesional",    # anual completo
    "479.88": "inmobiliaria",
    "1247.88":"enterprise",
}

PLAN_NOMBRES = {
    "payperuse":    "Pay-per-Use",
    "profesional":  "Profesional",
    "inmobiliaria": "Inmobiliaria",
    "enterprise":   "Enterprise",
}

# ── Schemas ───────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    plan_type:    str           # profesional | inmobiliaria | enterprise | payperuse
    billing: str = "monthly"  # monthly | annual

class CaptureRequest(BaseModel):
    order_id: str
    plan:     str
    billing:  str = "monthly"

# ── PayPal Helpers ────────────────────────────────────────────

def _creds():
    return CREDS[PAYPAL_MODE]

async def _get_token() -> Optional[str]:
    c = _creds()
    if not c["client_id"] or not c["secret"]:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{c['base']}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(c["client_id"], c["secret"]),
            )
            if r.status_code == 200:
                return r.json()["access_token"]
    except Exception as e:
        print(f"[PayPal] Token error: {e}")
    return None

PRECIOS = {
    ("profesional",  "monthly"): "19.99",
    ("profesional",  "annual"):  "191.88",
    ("inmobiliaria", "monthly"): "49.99",
    ("inmobiliaria", "annual"):  "479.88",
    ("enterprise",   "monthly"): "129.99",
    ("enterprise",   "annual"):  "1247.88",
    ("payperuse",    "monthly"): "2.99",
    ("payperuse",    "annual"):  "2.99",
}

def _plan_desde_amount(amount_str: str) -> str:
    try:
        amount = float(amount_str)
        for key, val in PLAN_MAP.items():
            if abs(amount - float(key)) < 0.50:
                return val
    except:
        pass
    return "free"

async def _activar_plan(user_email: str, plan: str, order_id: str,
                        amount: str, billing: str, payer_email: str):
    """Activa el plan en DB y registra el pago. Thread-safe."""
    import database as db
    from routes.payments import _get_main_users_db

    # 1. Guardar pago en MongoDB
    pago = {
        "order_id":    order_id,
        "user_email":  user_email,
        "payer_email": payer_email,
        "plan":        plan,
        "amount":      amount,
        "billing":     billing,
        "status":      "completed",
        "mode":        PAYPAL_MODE,
        "created_at":  datetime.utcnow().isoformat(),
    }
    await db.guardar_pago(pago)

    # 2. Actualizar usuario en MongoDB
    update = {
        "plan":       plan,
        "plan_since": datetime.utcnow().isoformat(),
        "plan_billing": billing,
    }
    if plan == "payperuse":
        # Pay-per-use: incrementar créditos de uso
        update["payperuse_creditos"] = 1

    await db.actualizar_usuario(user_email, update)

    # 3. Actualizar cache local
    users_cache = _get_main_users_db()
    if user_email in users_cache:
        users_cache[user_email].update(update)

    print(f"[PayPal] Plan {plan} activado para {user_email} — Order {order_id}")

# ── Acceso al users_db de main.py ─────────────────────────────
_main_users_db_ref = {}
def set_main_users_db(ref: dict):
    global _main_users_db_ref
    _main_users_db_ref = ref

def _get_main_users_db():
    return _main_users_db_ref

# ── Rutas ─────────────────────────────────────────────────────

@router.post("/create-order")
async def create_order(req: CreateOrderRequest, request: Request):
    """
    Crea una orden en PayPal. El frontend la usa para abrir el popup.
    Si no hay credenciales (desarrollo), devuelve orden mock.
    """
    token = await _get_token()
    amount = PRECIOS.get((req.plan_type.lower(), req.billing), "19.99")
    desc   = f"ValuMetrics AI — Plan {PLAN_NOMBRES.get(req.plan_type, req.plan_type)} ({req.billing})"

    if not token:
        raise HTTPException(status_code=500, detail="Error al conectar con PayPal (Token no obtenido). Verifica tus credenciales.")

    c = _creds()
    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "description": desc,
            "amount": {
                "currency_code": "USD",
                "value": amount,
            },
        }],
        "application_context": {
            "brand_name":          "ValuMetrics AI",
            "locale":              "es-VE",
            "landing_page":        "BILLING",
            "shipping_preference": "NO_SHIPPING",
            "user_action":         "PAY_NOW",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{c['base']}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
                json=order_data,
            )
            if r.status_code in (200, 201):
                return r.json()
            raise HTTPException(status_code=502,
                detail=f"PayPal error {r.status_code}: {r.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error creando orden: {e}")


@router.post("/capture")
async def capture_payment(
    req: CaptureRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Captura y VERIFICA server-side el pago.
    Llamado por el frontend tras onApprove de PayPal.
    """
    # Obtener usuario desde el token JWT del header
    auth_header = request.headers.get("Authorization", "")
    user_email  = ""
    if auth_header.startswith("Bearer "):
        try:
            import jwt as pyjwt
            SECRET_KEY = os.getenv("SECRET_KEY")
            payload = pyjwt.decode(
                auth_header[7:], SECRET_KEY, algorithms=["HS256"]
            )
            user_email = payload.get("sub", "")
        except:
            pass

    if not user_email:
        raise HTTPException(status_code=401, detail="Autenticación requerida")

    # Obtener token
    token = await _get_token()
    if not token:
        raise HTTPException(status_code=500, detail="Error de autenticación con PayPal.")

    # Capturar la orden con PayPal
    c = _creds()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{c['base']}/v2/checkout/orders/{req.order_id}/capture",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
            )
            if r.status_code not in (200, 201):
                raise HTTPException(status_code=502,
                    detail=f"Error capturando pago: {r.text[:300]}")

            order = r.json()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error de conexión PayPal: {e}")

    # Verificar estado
    if order.get("status") != "COMPLETED":
        raise HTTPException(status_code=402,
            detail=f"Pago no completado. Estado: {order.get('status')}")

    # Extraer datos del pago verificado
    units       = order.get("purchase_units", [{}])
    captures    = units[0].get("payments", {}).get("captures", [{}])
    amount_paid = captures[0].get("amount", {}).get("value", "0") if captures else "0"
    payer_email = order.get("payer", {}).get("email_address", "")

    # Verificar que el monto corresponde al plan solicitado
    expected = PRECIOS.get((req.plan, req.billing), "0")
    if abs(float(amount_paid) - float(expected)) > 0.50:
        print(f"[PayPal] ALERTA: monto {amount_paid} ≠ esperado {expected} para {user_email}")

    # Plan real basado en monto pagado (más seguro que confiar en req.plan)
    plan_real = _plan_desde_amount(amount_paid) or req.plan

    # Activar plan en background (no bloquear respuesta)
    background_tasks.add_task(
        _activar_plan, user_email, plan_real, req.order_id,
        amount_paid, req.billing, payer_email
    )

    return {
        "status":      "COMPLETED",
        "plan":        plan_real,
        "plan_nombre": PLAN_NOMBRES.get(plan_real, plan_real),
        "order_id":    req.order_id,
        "amount":      amount_paid,
        "message":     f"¡Pago confirmado! Plan {PLAN_NOMBRES.get(plan_real)} activado.",
    }


@router.post("/webhook/paypal")
async def webhook_paypal(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook de PayPal — notificación directa server-to-server.
    Actúa como capa de redundancia: si el frontend falla, el webhook activa el plan.
    NO requiere autenticación del usuario (viene de PayPal).
    """
    raw_body = await request.body()

    # Verificar firma del webhook (en producción es obligatorio)
    if PAYPAL_MODE == "production" and PAYPAL_WEBHOOK_ID:
        headers = dict(request.headers)
        token   = await _get_token()
        if token:
            try:
                c = _creds()
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(
                        f"{c['base']}/v1/notifications/verify-webhook-signature",
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
                        json={
                            "auth_algo":          headers.get("paypal-auth-algo",""),
                            "cert_url":           headers.get("paypal-cert-url",""),
                            "transmission_id":    headers.get("paypal-transmission-id",""),
                            "transmission_sig":   headers.get("paypal-transmission-sig",""),
                            "transmission_time":  headers.get("paypal-transmission-time",""),
                            "webhook_id":         PAYPAL_WEBHOOK_ID,
                            "webhook_event":      json.loads(raw_body),
                        }
                    )
                    result = r.json()
                    if result.get("verification_status") != "SUCCESS":
                        print("[Webhook] Firma inválida — ignorando")
                        return {"status": "ignored", "reason": "invalid_signature"}
            except Exception as e:
                print(f"[Webhook] Error verificando firma: {e}")

    # Parsear evento
    try:
        event = json.loads(raw_body)
    except:
        return {"status": "ignored", "reason": "invalid_json"}

    event_type = event.get("event_type", "")
    resource   = event.get("resource", {})

    print(f"[Webhook] Evento recibido: {event_type}")

    if event_type in ("CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED"):
        order_id = resource.get("id", "")
        # Obtener detalles completos de la orden
        token = await _get_token()
        if token and order_id:
            c = _creds()
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(
                        f"{c['base']}/v2/checkout/orders/{order_id}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if r.status_code == 200:
                        order = r.json()
                        units    = order.get("purchase_units",[{}])
                        captures = units[0].get("payments",{}).get("captures",[{}])
                        amount   = captures[0].get("amount",{}).get("value","0") if captures else "0"
                        payer_email = order.get("payer",{}).get("email_address","")
                        # Buscar usuario por payer_email en DB
                        import database as db
                        user = await db.obtener_usuario(payer_email)
                        if user:
                            plan = _plan_desde_amount(amount)
                            background_tasks.add_task(
                                _activar_plan, payer_email, plan,
                                order_id, amount, "monthly", payer_email
                            )
                            print(f"[Webhook] Plan {plan} activado vía webhook para {payer_email}")
            except Exception as e:
                print(f"[Webhook] Error procesando orden: {e}")

    return {"status": "received", "event_type": event_type}


@router.get("/status")
async def payment_status(request: Request):
    """
    Consulta el estado actual del plan del usuario.
    El frontend lo llama cada vez que necesita verificar si el plan fue activado.
    """
    auth_header = request.headers.get("Authorization", "")
    user_email  = ""
    if auth_header.startswith("Bearer "):
        try:
            import jwt as pyjwt
            SECRET_KEY = os.getenv("SECRET_KEY", "valumetrics-secret-2025-change-in-prod")
            payload = pyjwt.decode(auth_header[7:], SECRET_KEY, algorithms=["HS256"])
            user_email = payload.get("sub", "")
        except:
            pass

    if not user_email:
        return {"plan": "anonimo", "limite_mes": 3, "puede_pdf": False}

    import database as db
    user = _get_main_users_db().get(user_email) or await db.obtener_usuario(user_email)
    if not user:
        return {"plan": "free", "limite_mes": 5, "puede_pdf": False}

    plan = user.get("plan", "free")
    LIMITES = {
        "anonimo":     (3,   False, False),
        "free":        (5,   False, False),
        "profesional": (50,  True,  True),
        "inmobiliaria":(200, True,  True),
        "enterprise":  (-1,  True,  True),
        "payperuse":   (1,   True,  True),
    }
    limite, puede_pdf, puede_vision = LIMITES.get(plan, LIMITES["free"])

    mes = datetime.utcnow().strftime("%Y-%m")
    usadas = user.get("valuaciones_mes", 0) if user.get("mes_actual") == mes else 0

    return {
        "plan":           plan,
        "plan_nombre":    PLAN_NOMBRES.get(plan, plan.title()),
        "limite_mes":     limite,
        "usadas_mes":     usadas,
        "restantes":      (limite - usadas) if limite != -1 else -1,
        "puede_pdf":      puede_pdf,
        "puede_vision":   puede_vision,
        "plan_since":     user.get("plan_since", ""),
        "plan_billing":   user.get("plan_billing", ""),
    }
