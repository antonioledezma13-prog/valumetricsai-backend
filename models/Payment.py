# models/payment.py
# Estructura para PyMongo
payment_schema = {
    "user_id": "ObjectId",
    "type": "str",        # 'subscription' o 'report'
    "product_id": "str",
    "status": "str",      # 'pending', 'completed', 'failed'
    "paypal_order_id": "str",
    "amount": "float",
    "metadata": {
        "property_id": "str",
        "billing_cycle": "str"
    },
    "created_at": "datetime"
}