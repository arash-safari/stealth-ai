from __future__ import annotations
from typing import TypedDict, Literal, Dict, Callable, Any, List

Intent = Literal["BOOK","PRICE","RESCHEDULE","CANCEL","ETA","STATUS","EMERGENCY","OTHER"]

class StepSpec(TypedDict, total=False):
    type: Literal["slot","system"]
    slot: str
    question: str
    expected: str
    next: str
    guard: Callable[[Dict[str, Any]], bool]

class FlowSpec(TypedDict):
    entry: str
    steps: Dict[str, StepSpec]
    aliases: Dict[str, str]
    required_slots: List[str]

FlowMap = Dict[Intent, FlowSpec]
