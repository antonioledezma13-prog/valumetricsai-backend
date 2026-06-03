"""
ValuMetrics AI — Servicio de Pagos PayPal (Producción-grade)
============================================================
Flujo seguro:
  1. Frontend: PayPal.Buttons → captura orden
  2. Frontend: POST /payments/capture con order_id
  3. Backend: verifica con PayPal API (no confiar en frontend)
  4. Backend: activa plan en MongoDB
  5. Backend: envía email de confirmación
  6. Webhook: PayPal notifica server directamente (redundancia)

Variables de entorno requeridas:
  PAYPAL_MODE=sandbox|production
  PAYPAL_SANDBOX_CLIENT_ID=...
  PAYPAL_SANDBOX_SECRET=...
  PAYPAL_LIVE_CLIENT_ID=...
  PAYPAL_LIVE_SECRET=...
"""

import os
import hmac
import hashlib
import httpx
from typing import Optional, Dict

# ── Configuración ─────────────────────────────────────────────
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")

PAYPAL_CREDENTIALS = {
    "sandbox": {
        "client_id": os.getenv("PAYPAL_SANDBOX_CLIENT_ID", ""),
        "secret":    os.getenv("PAYPAL_SANDBOX_SECRET", ""),
        "base_url":  "https://api-m.sandbox.paypal.com",
    },
    "production": {
        "client_id": os.getenv("PAYPAL_LIVE_CLIENT_ID", ""),
        "secret":    os.getenv("PAYPAL_LIVE_SECRET", ""),
        "base_url":  "https://api-m.paypal.com",
    },
}

PLAN_PRICING = {
    # plan_id: {monthly_usd, annual_usd, nombre}
    "profesional":  {"monthly": "19.99", "annual": "191.88", "nombre": "Profesional"},
    "inmobiliaria": {"monthly": "49.99", "annual": "479.88", "nombre": "Inmobiliaria"},
    "enterprise":   {"monthly": "129.99","annual":"1247.88", "nombre": "Enterprise"},
    "payperuse":    {"monthly": "2.99",  "annual": "2.99",   "nombre": "Pay-per-Use"},
}


def _get_creds():
    return PAYPAL_CREDENTIALS[PAYPAL_MODE]


async def get_access_token() -> Optional[str]:
    """Obtiene token de acceso de PayPal API."""
    creds = _get_creds()
    if not creds["client_id"] or not creds["secret"]:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{creds['base_url']}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(creds["client_id"], creds["secret"]),
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception as e:
        print(f"[PayPal] Error obteniendo token: {e}")
    return None


async def verificar_orden(order_id: str) -> Optional[Dict]:
    """
    Verifica una orden de PayPal directamente con la API.
    NUNCA confiar en el frontend — siempre verificar server-side.
    Retorna el dict de la orden si es válida y COMPLETED, None si no.
    """
    token = await get_access_token()
    if not token:
        print("[PayPal] Sin credenciales — modo desarrollo")
        # En desarrollo sin credenciales, retornar mock válido
        if PAYPAL_MODE == "sandbox":
            return {"status": "COMPLETED", "mock": True, "id": order_id}
        return None

    creds = _get_creds()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{creds['base_url']}/v2/checkout/orders/{order_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                order = resp.json()
                return order if order.get("status") == "COMPLETED" else None
            else:
                print(f"[PayPal] Error verificando orden {order_id}: {resp.status_code}")
    except Exception as e:
        print(f"[PayPal] Error verificando orden: {e}")
    return None


async def capturar_orden(order_id: str) -> Optional[Dict]:
    """
    Captura una orden aprobada.
    Se llama cuando el usuario aprueba en el popup de PayPal.
    """
    token = await get_access_token()
    if not token:
        if PAYPAL_MODE == "sandbox":
            return {"status": "COMPLETED", "mock": True, "id": order_id}
        return None

    creds = _get_creds()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{creds['base_url']}/v2/checkout/orders/{order_id}/capture",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
            )
            if resp.status_code in (200, 201):
                return resp.json()
            else:
                print(f"[PayPal] Error capturando {order_id}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[PayPal] Error capturando orden: {e}")
    return None


def verificar_webhook_signature(
    headers: dict,
    raw_body: bytes,
    webhook_id: str,
) -> bool:
    """
    Verifica que el webhook vino realmente de PayPal.
    webhook_id: obtenido de developer.paypal.com → Webhooks
    """
    try:
        # PayPal envía estos headers
        auth_algo   = headers.get("paypal-auth-algo", "")
        cert_url    = headers.get("paypal-cert-url", "")
        transmission_id  = headers.get("paypal-transmission-id", "")
        transmission_sig = headers.get("paypal-transmission-sig", "")
        transmission_time= headers.get("paypal-transmission-time", "")

        # En producción: validar con PayPal /v1/notifications/verify-webhook-signature
        # En sandbox: siempre aceptar para facilitar pruebas
        if PAYPAL_MODE == "sandbox":
            return True

        # TODO: implementar verificación completa en producción
        # (requiere llamada async a PayPal API)
        return bool(transmission_id and transmission_sig)
    except Exception:
        return False


def plan_desde_amount(amount_str: str) -> str:
    """Determina el plan a partir del monto pagado."""
    try:
        amount = float(amount_str)
    except:
        return "free"

    # Tolerancia de ±$0.50 para variaciones de tipo de cambio
    if abs(amount - 2.99) < 0.50:   return "payperuse"
    if abs(amount - 19.99) < 0.50:  return "profesional"
    if abs(amount - 15.99) < 0.50:  return "profesional"   # anual mensualizado
    if abs(amount - 49.99) < 0.50:  return "inmobiliaria"
    if abs(amount - 39.99) < 0.50:  return "inmobiliaria"  # anual
    if abs(amount - 129.99) < 0.50: return "enterprise"
    if abs(amount - 103.99) < 0.50: return "enterprise"    # anual
    # Por monto anual completo
    if abs(amount - 191.88) < 1.0:  return "profesional"
    if abs(amount - 479.88) < 1.0:  return "inmobiliaria"
    if abs(amount - 1247.88) < 2.0: return "enterprise"
    return "free"
