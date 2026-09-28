"""设置页可修改的业务配置（购买方主体清单、金额校验规则等）。"""

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, String

from app.database import Base


class AppSetting(Base):
    """一个配置项一行，值是 JSON。表里没有的配置项用出厂默认值（见 app/services/app_settings.py）。"""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
