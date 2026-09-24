import logging
from fastapi import APIRouter
from pydantic import BaseModel,Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUserDep,DbDep
from app.core.security import generate_api_key,hash_api_key,mask_api_key

logger=logging.getLogger(__name__)

router=APIRouter(prefix="/auth",tags=["账号"])

class RotateKeyResponse(BaseModel):
    username:str=Field(description="用户名")
    api_key:str=Field(description="⭐新的APIKey，只返回这一次，请立刻保存")
    old_key_masked:str=Field(description="被替换的旧Key(打码)")

@router.post("/rotate-key",response_model=RotateKeyResponse,summary="轮换APIKey")
async def rotate_key(db:DbDep,user:CurrentUserDep)->RotateKeyResponse:
    new_key=generate_api_key()
    old_hash=user.api_key_hash

    user.api_key_hash=hash_api_key(new_key)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.warning(
        "用户轮换了Key",
        extra={"extra_fields":{"user_id":user.id,"old":old_hash[:8]+"..."}},
    )

    return RotateKeyResponse(
        username=user.username,
        api_key=new_key,
        old_key_masked=mask_api_key(old_hash[:16])
        )
