from __future__ import annotations
from .types import FlowMap, FlowSpec
from .flows import FLOWS as FLOW_DEFS

def flow_for_intent(intent: str) -> FlowSpec:
    return FLOW_DEFS.get(intent, FLOW_DEFS["OTHER"])
