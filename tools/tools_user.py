## tools/tools_user.py`

import yaml
from typing import Optional
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext
from services import user_service as users

@function_tool()
async def usr_create_user(context: RunContext, full_name: str, phone: str, email: Optional[str] = None) -> str:
    res = await users.create_user(full_name=full_name, phone=phone, email=email)
    return yaml.dump(res, sort_keys=False)

@function_tool()
async def usr_get_user(context: RunContext, user_id: str) -> str:
    res = await users.get_user(user_id)
    return yaml.dump(res or {}, sort_keys=False)

@function_tool()
async def get_user_by_phone(context: RunContext, phone: str) -> str:
    res = await users.get_user_by_phone(phone)
    return yaml.dump(res or {}, sort_keys=False)

@function_tool()
async def usr_update_user(context: RunContext, user_id: str, full_name: Optional[str] = None,
                          phone: Optional[str] = None, email: Optional[str] = None) -> str:
    data = {}
    if full_name is not None: data["full_name"] = full_name
    if phone is not None: data["phone"] = phone
    if email is not None: data["email"] = email
    res = await users.update_user(user_id, **data)
    return yaml.dump(res, sort_keys=False)

@function_tool()
async def usr_add_address(
    context: RunContext,
    user_id: str,
    line1: str,
    line2: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
    label: Optional[str] = None,
    is_default: bool = False,
) -> str:
    res = await users.add_address(
        user_id, line1=line1, line2=line2, city=city, state=state, postal_code=postal_code,
        label=label, is_default=is_default
    )
    return yaml.dump(res, sort_keys=False)

@function_tool()
async def usr_get_default_address(context: RunContext, user_id: str) -> str:
    res = await users.get_default_address(user_id)
    return yaml.dump(res or {}, sort_keys=False)
