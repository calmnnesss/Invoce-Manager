"""qwen3.5-ocr 抽取线路（v6 管线的 B 路）。

与本地 PaddleOCR 线路的区别 —— 那条线路 v6 已不再使用，代码存档在仓库根目录 archive/paddle-ocr/。
这里调用阿里云百炼的 qwen3.5-ocr 内置任务 key_information_extraction，
直接拿到结构化键值对，不经过"识别成文本再正则抽字段"那一步。

参数为什么是这样（51 张实测）
----------------------------
schema 11 字段但只采信 2 个
    v6 只取 销售方名称 / 销售方纳税人识别号。收窄到 2 字段试过，销售方名称仍是
    51/51，但销售方税号从 45/51 掉到 0/51 —— 全部返回 null。推测是模型需要
    "购买方纳税人识别号"这个对照项才能确定该取哪一个税号。多要的 9 个字段是
    定位上下文，不是冗余，删掉就坏。它们同时可以白拿来做交叉校验。

enable_rotate=False
    开启转正后模型多数会把侧放的票摆正，而摆正之后它进入"表格模式"：
    金额、税额能读，但购买方和税号大量返回 null。关闭后保持侧放，进入
    "主体模式"，销售方税号 11/51 -> 45/51。
    风险：这批测试图 51 张里 44 张恰好是顺时针侧存的。真实用户上传的票大多
    正放，届时可能退回表格模式，销售方税号会下滑。上线前需用正放的真实票复测。

max_pixels=32*32*8192（默认值）
    扫过 2048 / 4096 / 8192 / 16384 四档，字段正确率差异在噪声范围内
    （45~46 / 70）。原图 1313 万像素会被压到 839 万，但不影响结果，
    所以"有效分辨率不足"这个假设在 qwen3.5-ocr 上不成立。
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

OCR_LINE_VERSION = "qwen3.5-ocr-11f-rotoff-n8192"

DEFAULT_MODEL = "qwen3.5-ocr"
DEFAULT_MAX_PIXELS_N = 8192
DEFAULT_MIN_PIXELS_N = 3
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 2

# 11 字段。描述即字段名 —— 带解释性描述时模型会在读不到值的情况下
# 把描述文本当成值抄回来（"税额合计"这类），描述越短这种回声越少。
RESULT_SCHEMA: Dict[str, str] = {
    "发票代码": "发票代码",
    "发票号码": "发票号码",
    "开票日期": "开票日期",
    "购买方名称": "购买方名称",
    "购买方纳税人识别号": "购买方纳税人识别号",
    "销售方名称": "销售方名称",
    "销售方纳税人识别号": "销售方纳税人识别号",
    "金额": "金额",
    "税额": "税额",
    "价税合计": "价税合计",
    "税率": "税率",
}

# v6 实际采信的字段。其余 9 个仅用于定位上下文与交叉校验。
TRUSTED_KEYS = ("销售方名称", "销售方纳税人识别号")


class QwenOcrError(RuntimeError):
    pass


class QwenOcrService:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        enable_rotate: bool = False,
        max_pixels_n: int = DEFAULT_MAX_PIXELS_N,
        min_pixels_n: int = DEFAULT_MIN_PIXELS_N,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self.api_key = api_key or getattr(settings, "qwen_api_key", "")
        self.model = model
        self.base_url = base_url
        self.enable_rotate = enable_rotate
        self.max_pixels = 32 * 32 * max_pixels_n
        self.min_pixels = 32 * 32 * min_pixels_n
        self.timeout = timeout
        self.retries = retries

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import dashscope  # noqa: F401
        except ImportError:
            return False
        return True

    def extract(self, file_data: bytes, suffix: str = ".jpg") -> Dict[str, Any]:
        """对单张图做结构化抽取。

        返回 {"kv_result": {...}, "usage": {...}, "elapsed_s": float,
              "ocr_version": str, "error": str|None}
        任何一路失败都不抛给调用方 —— 融合层按"该路无值"处理即可，
        整条管线不应因为一路挂掉而失败。
        """
        out: Dict[str, Any] = {
            "kv_result": None,
            "usage": {},
            "elapsed_s": 0.0,
            "ocr_version": OCR_LINE_VERSION,
            "model": self.model,
            "error": None,
        }

        if not self.is_available():
            out["error"] = "qwen_api_key 未配置或 dashscope 未安装"
            logger.warning("Qwen OCR 不可用: %s", out["error"])
            return out

        import dashscope
        from dashscope import MultiModalConversation

        if self.base_url:
            dashscope.base_http_api_url = self.base_url

        # DashScope SDK 的本地图片走 file:// 路径，所以要先落临时文件
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
                fh.write(file_data)
                tmp_path = Path(fh.name)

            messages = [{
                "role": "user",
                "content": [{
                    "image": f"file://{tmp_path}",
                    "min_pixels": self.min_pixels,
                    "max_pixels": self.max_pixels,
                    "enable_rotate": self.enable_rotate,
                }],
            }]

            last_err = None
            for attempt in range(1, self.retries + 2):
                t0 = time.time()
                try:
                    resp = MultiModalConversation.call(
                        api_key=self.api_key,
                        model=self.model,
                        messages=messages,
                        ocr_options={
                            "task": "key_information_extraction",
                            "task_config": {"result_schema": RESULT_SCHEMA},
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    last_err = f"{type(e).__name__}: {e}"
                    logger.warning("Qwen OCR 调用异常 (第 %d 次): %s", attempt, last_err)
                    time.sleep(min(2 ** attempt, 8))
                    continue

                out["elapsed_s"] = round(time.time() - t0, 2)
                status = getattr(resp, "status_code", 200)
                if status != 200:
                    last_err = f"HTTP {status}: {getattr(resp, 'message', '')}"
                    logger.warning("Qwen OCR 返回非 200 (第 %d 次): %s", attempt, last_err)
                    time.sleep(min(2 ** attempt, 8))
                    continue

                kv, usage = _extract_kv(resp)
                out["kv_result"] = kv
                out["usage"] = usage
                if kv is None:
                    out["error"] = "响应中没有 ocr_result.kv_result"
                    logger.warning("Qwen OCR 响应缺少 kv_result")
                return out

            out["error"] = last_err or "未知错误"
            return out

        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


def _extract_kv(resp) -> tuple[Optional[dict], dict]:
    kv, usage = None, {}
    try:
        for part in resp["output"]["choices"][0]["message"]["content"]:
            if isinstance(part, dict) and isinstance(part.get("ocr_result"), dict):
                kv = part["ocr_result"].get("kv_result")
    except Exception:  # noqa: BLE001
        pass
    try:
        usage = dict(resp["usage"])
    except Exception:  # noqa: BLE001
        pass
    return kv, usage


_service: Optional[QwenOcrService] = None


def get_qwen_ocr_service() -> QwenOcrService:
    global _service
    if _service is None:
        # 每次新建都重新读配置。模块级的 settings 是导入时的快照，设置页改了 Key
        # 之后会调用 reset_qwen_ocr_service()，这时读快照会拿到旧 Key。
        current = get_settings()
        _service = QwenOcrService(
            api_key=getattr(current, "qwen_api_key", ""),
            enable_rotate=getattr(current, "ocr_enable_rotate", False),
            max_pixels_n=getattr(current, "ocr_max_pixels_n", DEFAULT_MAX_PIXELS_N),
            base_url=getattr(current, "dashscope_base_url", None) or None,
        )
    return _service


def reset_qwen_ocr_service() -> None:
    global _service
    _service = None
