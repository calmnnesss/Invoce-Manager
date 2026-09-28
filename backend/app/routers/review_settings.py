"""业务配置接口：购买方主体清单、金额组校验规则（设置页使用）。

暂不做权限隔离：能打开前端的人都能修改。每次修改写一条审计日志（entity_type="settings"）。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import app_settings
from app.services.app_settings import (
    InvalidSetting,
    ReviewSettings,
    amount_rules_to_dict,
    normalize_amount_rules,
    normalize_buyer_entities,
)
from app.services.audit_service import get_client_info, log_audit_no_commit

router = APIRouter()


class BuyerEntityIn(BaseModel):
    id: Optional[str] = None      # 已有主体带上原 id；新增的不填，由后端分配
    name: Optional[str] = None
    tax_id: Optional[str] = None


class BuyerEntitiesUpdate(BaseModel):
    entities: List[BuyerEntityIn]


class AmountRulesUpdate(BaseModel):
    # 类型放宽到 Optional：空值交给 normalize_amount_rules 报中文错误，而不是 422
    add_tolerance: Optional[float] = None
    mul_tolerance: Optional[float] = None
    rounding_tolerance: Optional[float] = None
    standard_rates: Optional[List[float]] = None   # 百分数，13 表示 13%


def _view(cfg: ReviewSettings) -> Dict[str, Any]:
    return {
        "buyer_entities": cfg.entities,
        "amount_rules": amount_rules_to_dict(cfg.amount_rules),
    }


def _response(cfg: ReviewSettings) -> Dict[str, Any]:
    return {**_view(cfg), "defaults": _view(app_settings.defaults())}


async def _audit(db: AsyncSession, request: Request, action: str, old: Any, new: Any) -> None:
    client = get_client_info(request)
    await log_audit_no_commit(
        db=db,
        entity_type="settings",
        entity_id=0,
        action=action,
        old_value={"value": old},
        new_value={"value": new},
        ip_address=client.get("ip_address"),
        user_agent=client.get("user_agent"),
    )


@router.get("/review")
async def get_review_settings():
    """当前生效的主体清单与金额规则，以及出厂值（设置页的「恢复默认」用）。"""
    return _response(app_settings.current())


@router.put("/buyer-entities")
async def update_buyer_entities(
    body: BuyerEntitiesUpdate, request: Request, db: AsyncSession = Depends(get_db)
):
    """整体替换购买方主体清单。对之后识别的发票生效。"""
    try:
        entities = normalize_buyer_entities(e.model_dump() for e in body.entities)
    except InvalidSetting as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _audit(db, request, "update_buyer_entities", app_settings.current().entities, entities)
    return _response(await app_settings.save(db, buyer_entities=entities))


@router.put("/amount-rules")
async def update_amount_rules(
    body: AmountRulesUpdate, request: Request, db: AsyncSession = Depends(get_db)
):
    """整体替换金额校验规则。对之后识别的发票、以及之后对已有发票的修改生效。"""
    try:
        rules = normalize_amount_rules(body.model_dump())
    except InvalidSetting as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _audit(db, request, "update_amount_rules",
                 amount_rules_to_dict(app_settings.current().amount_rules),
                 amount_rules_to_dict(rules))
    return _response(await app_settings.save(db, amount_rules=rules))
