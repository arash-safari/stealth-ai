## common/models.py`

from dataclasses import dataclass, field
from typing import Optional
import yaml

@dataclass
class UserData:
    # Contact
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None

    # Service location
    street: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None

    # Job details
    problem_description: Optional[str] = None
    urgency: Optional[str] = None  # "normal" | "urgent" | "emergency"

    # Appointment
    appointment_id: Optional[str] = None
    appointment_date: Optional[str] = None          # YYYY-MM-DD
    appointment_window: Optional[str] = None        # HH:MM-HH:MM
    appointment_status: Optional[str] = None        # scheduled | en_route | complete | canceled

    # Parts cart
    cart: list[dict] = field(default_factory=list)
    cart_total: float = 0.0

    # Pricing / estimate
    estimate_low: Optional[float] = None
    estimate_high: Optional[float] = None

    # Payments
    card_number: Optional[str] = None
    card_expiry: Optional[str] = None
    card_cvv: Optional[str] = None
    amount_authorized: Optional[float] = None

    # Agent shared
    agents: dict[str, "BaseAgent"] = field(default_factory=dict)
    prev_agent: Optional["BaseAgent"] = None

    def address_str(self) -> str:
        parts = [self.street or "", self.unit or "", self.city or "", self.state or "", self.postal_code or ""]
        return ", ".join([p for p in parts if p]) or "unknown"

    def summarize(self) -> str:
        data = {
            "customer": {
                "name": self.customer_name or "unknown",
                "phone": self.customer_phone or "unknown",
                "email": self.customer_email or "unknown",
            },
            "address": {
                "street": self.street or "unknown",
                "unit": self.unit or None,
                "city": self.city or "unknown",
                "state": self.state or "unknown",
                "postal_code": self.postal_code or "unknown",
            },
            "job": {
                "description": self.problem_description or "unknown",
                "urgency": self.urgency or "normal",
            },
            "appointment": {
                "id": self.appointment_id or None,
                "date": self.appointment_date or None,
                "window": self.appointment_window or None,
                "status": self.appointment_status or None,
            },
            "cart": self.cart or [],
            "cart_total": round(self.cart_total, 2),
            "estimate": {
                "low": self.estimate_low,
                "high": self.estimate_high,
            },
            "payment": {
                "card_number": self.card_number or None,
                "expiry": self.card_expiry or None,
                "cvv": self.card_cvv or None,
                "amount_authorized": self.amount_authorized,
            },
        }
        return yaml.dump(data, sort_keys=False)


def scrub_user_data(u: "UserData") -> None:
    # Contact
    u.customer_name = None
    u.customer_phone = None
    u.customer_email = None

    # Address
    u.street = u.unit = u.city = u.state = u.postal_code = None

    # Job details
    u.problem_description = None
    u.urgency = None

    # Appointment
    u.appointment_id = None
    u.appointment_date = None
    u.appointment_window = None
    u.appointment_status = None

    # Parts / cart
    u.cart.clear()
    u.cart_total = 0.0

    # Pricing / estimate
    u.estimate_low = None
    u.estimate_high = None

    # Payment
    u.card_number = None
    u.card_expiry = None
    u.card_cvv = None
    u.amount_authorized = None

    # Agent cross-refs
    u.prev_agent = None