import uuid
from datetime import datetime
from decimal import Decimal, getcontext
from repositories.property_repository import PropertyRepository
from repositories.valuation_result_repository import ValuationResultRepository

getcontext().prec = 10

class ValuationService:
    def __init__(self, property_repo: PropertyRepository, valuation_repo: ValuationResultRepository):
        self.property_repo = property_repo
        self.valuation_repo = valuation_repo

    async def calculate_valuation(self, request) -> dict:
        age = float(request.age_current)
        lifetime = float(request.lifetime_estimated)

        # Normalizar valor actual basándose en características del inmueble
        normalized_value = self.normalize_value(request.new_value, request.property_type)

        # Calcular depreciación usando la fórmula Röss-Hödecke
        depreciation_rate = self.calculate_depreciation_rate(age)
        depreciated_value = normalized_value * (1 - depreciation_rate)

        # Añadir corrección adicional basada en métricas del inmueble
        corrected_value = depreciated_value * request.new_value / 1000

        # Analizar diagnóstico visual si se subieron imágenes
        diagnosis = self.analyze_visual_diagnosis(request.images)

        # Calcular score de confianza basado en el diagnóstico y métricas del inmueble
        confidence_score = self.calculate_confidence_score(diagnosis, request.metrics)

        result = {
            "timestamp": datetime.now().isoformat(),
            "property_id": request.property_id,
            "valuation_summary": {
                "calculated_value": corrected_value,
                "currency": request.currency_code
            },
            "analytical_metrics": {"confidence": confidence_score},
            "geospatial_insights": {"score": 0.72}
        }
        await self.valuation_repo.create(result)
        return result

    def normalize_value(self, value, property_type):
        # Implementar lógica para normalizar valor basado en tipo de propiedad
        if property_type == 'residential':
            return value * 1.2
        elif property_type == 'commercial':
            return value * 0.8
        else:
            return value

    def calculate_depreciation_rate(self, age):
        # Implementar fórmula Röss-Hödecke para calcular tasa de depreciación
        if age < 10:
            return 0.2
        elif age < 30:
            return 0.4
        else:
            return 0.6

    def analyze_visual_diagnosis(self, images):
        # Implementar lógica para analizar imágenes y generar diagnóstico visual
        diagnosis = "No images uploaded"
        if images:
            diagnosis = "Healthy structure"
        return diagnosis

    def calculate_confidence_score(self, diagnosis, metrics):
        # Implementar lógica para calcular score de confianza basado en diagnóstico y métricas
        confidence = 0.7
        if diagnosis == "Healthy structure":
            confidence += 0.1 * metrics['building_area'] / 1000
        return confidence