from repositories.macroeconomic_data_repository import MacroeconomicDataRepository

class MacroeconomicDataService:
def __init__(self, repo: MacroeconomicDataRepository):
self.repo = repo

async def get_macroeconomic_data(self, country_code: str) -> dict:
# Simulado de obtención de datos macroeconómicos
data = {
"inflation_rate": 0.05,
"devaluation_rate": 0.02,
"mortgage_accessibility_factor": 1.0,
"volatility_change": 0.03
}
return data