from pydantic import BaseModel, validator
from typing import Optional
from datetime import datetime

class UserModel(BaseModel):
user_id: str
username: str
email: str
password_hash: str
created_at: datetime = datetime.now()

@validator('username', 'email')
def check_not_empty(cls, v):
if not v.strip():
raise ValueError(f"{v} cannot be empty")
return v