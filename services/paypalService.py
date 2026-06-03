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
        print(f"DEBUG: Iniciando create_order")
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
        
        url = f"{os.getenv('PAYPAL_BASE_URL')}/v2/checkout/orders"
        
        async with httpx.AsyncClient() as client:
            print(f"DEBUG: Enviando POST a {url}")
            response = await client.post(url, headers=headers, json=body)
            
            # --- CAMBIO AQUÍ: Capturamos la respuesta cruda ---
            print(f"DEBUG: Respuesta recibida con código {response.status_code}")
            print(f"DEBUG: Cuerpo de respuesta: {response.text}")
            
            if response.status_code != 201:
                raise Exception(f"PayPal devolvió error: {response.text}")

            # Retorna directamente el JSON
            data = response.json()
            print(f"DEBUG: Retornando datos: {data}")
            return data
            
    except httpx.HTTPError as e:
        print(f"ERROR DE CONEXIÓN HTTP: {str(e)}")
        raise e
    except Exception as e:
        print(f"ERROR INTERNO EN CREATE_ORDER: {str(e)}")
        raise e