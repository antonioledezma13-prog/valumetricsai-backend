from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class ValuationResponse(BaseModel):
    timestamp: str = datetime.now().isoformat()
    property_id: str
    valuation_summary: dict
    analytical_metrics: dict
    geospatial_insights: dict

    model_config = ConfigDict(populate_by_name=True)