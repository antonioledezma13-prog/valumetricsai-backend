# controllers/payments_controller.py
from fastapi import Request, HTTPException
from datetime import datetime
import database as db  
import services.paypalService as paypal
import jwt  # Importamos jwt para leer tu token directamente

SECRET_KEY = "valumetrics-secret-2025-change-in-prod"
ALGORITHM = "HS256"

async def create_subscription_order(request: Request):
    """
    Endpoint protegido que inicializa la intención de compra.
    Espera un JSON ej: {"plan_type": "Professional", "amount": 29.00}
    """
    body = await request.json()
    plan_type = body.get("plan_type")
    amount = body.get("amount")
    
    if not plan_type or not amount:
        raise HTTPException(status_code=400, detail="Faltan parámetros requeridos (plan_type, amount)")
    
    # ─── VALIDACIÓN DIRECTA DEL TOKEN ──────────────────────────────
    auth_header = request.headers.get("Authorization")
    user_id = "anon_user"
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            # Usamos la misma clave secreta de tu main.py
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub", "anon_user") # Extraemos el email/ID del usuario
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Token inválido o expirado")
    else:
        raise HTTPException(status_code=401, detail="No se proporcionó token de autenticación")
    # ───────────────────────────────────────────────────────────────

    try:
        # 1. Crear orden en los servidores de PayPal
        paypal_order = await paypal.create_order(amount=float(amount))
        order_id = paypal_order["id"]
        
        # 2. Registrar el pago como 'pending' en MongoDB Atlas
        new_payment = {
            "order_id": order_id,  # CAMBIA ESTO: usa la variable order_id que ya tienes
            "user_id": user_id,
            "type": "subscription" if plan_type != "Pay-per-Use" else "report",
            "product_id": plan_type,
            "status": "pending",
            "paypal_order_id": order_id,
            "amount": float(amount),
        }
        
        # OJO: Asegúrate de que en tu archivo database.py la variable que exporta la 
        # conexión a Motor se llame 'db' para que esta línea funcione.
        await db.guardar_pago(new_payment) 
        
        # Devuelve la orden para que el frontend abra la ventana de PayPal
        return {"status": "success", "order_id": order_id, "paypal_raw": paypal_order}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creando orden de PayPal: {str(e)}")


async def handle_webhook(request: Request):
    """
    Manejador del Webhook de PayPal / Captura manual del Frontend.
    Actualiza el estado en MongoDB a 'completed'.
    """
    event = await request.json()
    
    # Maneja tanto la simulación manual del front como el evento real de PayPal
    event_type = event.get('event_type')
    
    if event_type == 'CHECKOUT.ORDER.APPROVED' or event.get('status') == 'COMPLETED':
        # Extraer ID dependiendo de la estructura del JSON enviado
        resource = event.get('resource', {})
        order_id = resource.get('id') or event.get('paypal_order_id') or event.get('id')
        
        if not order_id:
            raise HTTPException(status_code=400, detail="No se encontró un ID de orden válido.")
        
        # Opcional: Ejecutar captura directa en los servidores de PayPal si vino del Front
        try:
            await paypal.capture_order(order_id)
        except Exception:
            # Si ya fue capturado por el webhook automático de PayPal, fallará silenciosamente aquí
            pass

        # 3. Habilitar la persistencia real actualizando tu base de datos
        await db.actualizar_pago_status(order_id, "completed")
            
        return {"status": "success", "message": "Pago procesado y actualizado en DB"}