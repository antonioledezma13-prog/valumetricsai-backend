from pydantic import BaseModel, validator
from typing import Optional, List
from decimal import Decimal

class ValuationRequest(BaseModel):
    property_id: str
    new_value: Decimal
    age_current: float
    lifetime_estimated: float
    currency_code: str
    confidence_score: float
    market_data_density: str
    structural_depreciation_applied: Optional[Decimal] = None
    macro_risk_premium_factor: Optional[float] = 0.05
    # Campos nuevos requeridos por tu lógica de servicio
    property_type: str = "residential"
    images: Optional[List[str]] = []
    metrics: dict = {"building_area": 100}

    @validator('new_value', 'age_current', 'lifetime_estimated')
    def check_positive(cls, v):
        if isinstance(v, (int, float, Decimal)) and v <= 0:
            raise ValueError(f"{v} must be a positive number")
        return v