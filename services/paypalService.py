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
    
    # Payload simplificado: Sin URLs de redirección porque el SDK de botones
    # maneja la ventana emergente directamente en el frontend.
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": currency,
                "value": f"{amount:.2f}"
            }
        }]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{os.getenv('PAYPAL_BASE_URL')}/v2/checkout/orders",
            headers=headers,
            json=body
        )
        
        # Si hay error, lanzamos un error con el detalle de PayPal
        if response.status_code != 201:
            print(f"DEBUG PAYPAL ERROR: {response.text}") # Esto aparecerá en los logs de Render
            raise HTTPException(status_code=response.status_code, detail=response.text)
            
        return response.json()