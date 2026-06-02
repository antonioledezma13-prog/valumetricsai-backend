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
    try:
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
            }]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{os.getenv('PAYPAL_BASE_URL')}/v2/checkout/orders",
                headers=headers,
                json=body
            )
            # Imprime el cuerpo de la respuesta para depurar
            if response.status_code != 201:
                print(f"ERROR DETALLADO PAYPAL: {response.status_code} - {response.text}")
            
            response.raise_for_status()
            return response.json()
            
    except Exception as e:
        # Esto atrapará cualquier error de código (ej: variable no definida)
        print(f"ERROR CRÍTICO EN PYTHON: {str(e)}")
        raise e