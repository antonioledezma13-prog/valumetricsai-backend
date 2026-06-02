# routes/payments.py
from fastapi import APIRouter
from controllers.payments_controller import handle_webhook, create_subscription_order

router = APIRouter()

# Quitamos el Depends() problemático. La seguridad se valida dentro del controlador.
router.add_api_route("/subscription/create", create_subscription_order, methods=["POST"])

# El webhook siempre debe ser público para que PayPal pueda enviar eventos
router.add_api_route("/webhook/paypal", handle_webhook, methods=["POST"])