from pydantic import BaseModel, validator
from typing import Optional
from decimal import Decimal
from datetime import datetime

class ValuationResultModel(BaseModel):
    timestamp: str = datetime.now().isoformat()
    property_id: str
    valuation_summary: dict
    analytical_metrics: dict
    geospatial_insights: dict

    class Config:
        populate_by_name = True