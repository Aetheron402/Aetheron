from datetime import datetime

_partial_payments = {}

def get_payment_key(wallet, component, currency):
    return f"{wallet}:{component}:{currency}"

def get_partial(wallet, component, currency):
    return _partial_payments.get(get_payment_key(wallet, component, currency))

def add_partial(wallet, component, currency, amount, required):
    key = get_payment_key(wallet, component, currency)
    entry = _partial_payments.get(key)

    if not entry:
        entry = {
            "wallet": wallet,
            "component": component,
            "currency": currency,
            "paid": 0,
            "required": required,
            "updated_at": datetime.utcnow(),
        }

    entry["paid"] += amount
    entry["updated_at"] = datetime.utcnow()
    _partial_payments[key] = entry
    return entry

def clear_partial(wallet, component, currency):
    _partial_payments.pop(get_payment_key(wallet, component, currency), None)
