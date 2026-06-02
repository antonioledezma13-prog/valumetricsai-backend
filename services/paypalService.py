import traceback
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
        # Imprimir para ver si las variables existen
        print(f"DEBUG: Iniciando create_order con monto={amount}")
        
        token = await get_access_token()
        print(f"DEBUG: Token obtenido correctamente")

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
        
        url = f"{os.getenv('PAYPAL_BASE_URL')}/v2/checkout/orders"
        print(f"DEBUG: Intentando POST a {url}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=body)
            
            # Si el error viene de PayPal, lo veremos aquí:
            if response.status_code != 201:
                print(f"ERROR PAYPAL: {response.status_code} - {response.text}")
            
            response.raise_for_status()
            return response.json()
            
    except Exception:
        # ESTO ES LO MÁS IMPORTANTE: Imprimirá la línea exacta del error
        print("ERROR CRÍTICO EN BACKEND:")
        print(traceback.format_exc())
        raise