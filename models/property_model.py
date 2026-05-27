from pydantic import BaseModel, validator
from typing import Optional
from decimal import Decimal

class PropertyModel(BaseModel):
    property_id: str
    address: str
    area_m2: float
    year_built: int
    finishings: str
    country_code: str

    @validator('area_m2')
    def check_positive(cls, v):
        if v <= 0:
            raise ValueError(f"{v} must be a positive number")
        return v

    @validator('year_built', 'country_code')
    def check_not_empty(cls, v):
        if isinstance(v, str) and not v.strip():
            raise ValueError(f"{v} cannot be empty")
        return v