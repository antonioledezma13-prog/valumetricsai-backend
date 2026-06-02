import httpx
import os

async def get_access_token():
    async with httpx.AsyncClient() as client:
        auth = (os.getenv('PAYPAL_CLIENT_ID'), os.getenv('PAYPAL_SECRET'))
        response = await client.post(
            f"{os.getenv('PAYPAL_BASE_URL')}/v1/oauth2/token",
            auth=auth,
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        return response.json()['access_token']

async def create_order(amount: float, currency: str = "USD"):
    """Crea una intención de pago en PayPal y devuelve los datos de la orden"""
    token = await get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": currency,
                "value": f"{amount:.2f}"
            }
        }],
        "application_context": {
            "return_url": "http://localhost:3000/payment-success", # Cambiar en prod
            "cancel_url": "http://localhost:3000/planes"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{os.getenv('PAYPAL_BASE_URL')}/v2/checkout/orders",
            headers=headers,
            json=body
        )
        response.raise_for_status()
        return response.json()

async def capture_order(order_id: str):
    """Captura el dinero de una orden previamente aprobada por el usuario"""
    token = await get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{os.getenv('PAYPAL_BASE_URL')}/v2/checkout/orders/{order_id}/capture",
            headers=headers,
            json={}
        )
        response.raise_for_status()
        return response.json()