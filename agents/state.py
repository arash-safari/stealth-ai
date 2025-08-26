from enum import Enum
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, Tuple

class StateKey(str, Enum):
    user_name = "user_name"
    user_address = "user_address"
    user_exact_issue = "user_exact_issue"
    user_window_slot_meeting_time = "user_window_slot_meeting_time"
    user_phone_number = "user_phone_number"

class StateEntry(BaseModel):
    value: Any = None
    confidence: float = 0.0
    needs_confirmation: bool = False
    source: str = "user"  # or "lookup" | "system"

class AgentState(BaseModel):
    data: Dict[StateKey, StateEntry] = Field(default_factory=dict)

    def set(self, key: StateKey, value: Any, confidence: float, needs_confirmation: bool, source: str = "user"):
        self.data[key] = StateEntry(value=value, confidence=confidence, needs_confirmation=needs_confirmation, source=source)

    def get(self, key: StateKey) -> Optional[StateEntry]:
        return self.data.get(key)

# map flow slot names -> canonical state keys (adjust to your naming)
SLOT_TO_STATE: Dict[str, StateKey] = {
    "name": StateKey.user_name,
    "address": StateKey.user_address,
    "description": StateKey.user_exact_issue,
    "time_window": StateKey.user_window_slot_meeting_time,
    "phone": StateKey.user_phone_number,
    "phone_number": StateKey.user_phone_number,
}