from fastapi import FastAPI, Request, Response, HTTPException, Depends
from pydantic import BaseModel

app = FastAPI()

class Token(BaseModel):
access_token: str
token_type: str

async def get_token(request: Request) -> Token:
token = request.headers.get("Authorization")
if not token:
raise HTTPException(status_code=401, detail="Missing Authorization Header")
return Token(access_token=token)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
try:
token_data = await get_token(request)
# Aquí puedes agregar la lógica de validación del token
response = await call_next(request)
return response
except HTTPException as e:
raise e