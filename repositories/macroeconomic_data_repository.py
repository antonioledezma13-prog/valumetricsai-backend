from pydantic import BaseModel

class MacroeconomicDataRepository:
async def get_data(self, country_code: str) -> dict:
# Simulado de obtención de datos macroeconómicos
data = {
"inflation_rate": 0.05,
"devaluation_rate": 0.02,
"mortgage_accessibility_factor": 1.0,
"volatility_change": 0.03
}
return data