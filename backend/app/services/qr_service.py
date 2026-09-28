"""二维码解码线路（v6 管线的 A 路）。

增值税发票左上角的二维码通常是逗号分隔串：
    01,<票种>,<发票代码>,<发票号码>,<不含税金额>,<开票日期YYYYMMDD>,<校验码后6位>,<随机码>

这条线路和另外两条有本质区别：它是确定性的。要么解出来，要么解不出来，
不存在"读了个像的" —— 不会像模型那样把「晶东」读成「晶乐」。解出的字段
优先级高于任何模型输出。

为什么不进默认链路
------------------
当前 51 张测试图 51/51 解码失败，zbar 与 cv2 两个解码器、四档缩放、
灰度变体全试过。可能的原因：二维码在 3120x4208 的画面里只占很小一块，
缩放后已经糊掉；或图像本身压缩过度。所以做成结果页的"用二维码重试"手动触发，
避免每张票都白跑一次。解码方式改进之后可以切回默认链路。
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

QR_LINE_VERSION = "qr-zbar-cv2"


def parse_payload(text: str) -> Dict[str, str]:
    """把二维码原文解析成字段。解析不出来就返回空字典。"""
    out: Dict[str, str] = {}
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 6:
        return out

    code, number, amount, issued = parts[2], parts[3], parts[4], parts[5]
    if re.fullmatch(r"\d{8,12}", code or ""):
        out["invoice_code"] = code
    if re.fullmatch(r"\d{6,10}", number or ""):
        out["invoice_number"] = number
    if re.fullmatch(r"\d+(\.\d+)?", amount or ""):
        out["amount"] = f"{float(amount):.2f}"
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", issued or "")
    if m:
        out["issue_date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return out


def decode_payload(file_data: bytes) -> tuple[Optional[str], str]:
    """返回 (二维码原文, 说明)。先用 zbar，再回退 cv2，各档缩放逐级重试。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, "cv2 未安装"

    try:
        from pyzbar import pyzbar
    except Exception:  # noqa: BLE001
        pyzbar = None

    img = cv2.imdecode(np.frombuffer(file_data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None, "图片解码失败"

    h, w = img.shape[:2]
    for scale in (0.35, 0.5, 0.75, 1.0):
        frame = (img if scale >= 1.0 else
                 cv2.resize(img, (int(w * scale), int(h * scale)),
                            interpolation=cv2.INTER_AREA))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if pyzbar is not None:
            for tag, im in (("", frame), ("gray", gray)):
                try:
                    found = pyzbar.decode(im)
                except Exception:  # noqa: BLE001
                    found = []
                for d in found:
                    if d.type != "QRCODE":
                        continue
                    try:
                        return d.data.decode("utf-8"), f"zbar scale={scale} {tag}".strip()
                    except UnicodeDecodeError:
                        return d.data.decode("gbk", "replace"), f"zbar scale={scale} gbk"

        det = cv2.QRCodeDetector()
        for tag, im in (("", frame), ("gray", gray)):
            try:
                text, _pts, _ = det.detectAndDecode(im)
            except cv2.error:
                continue
            if text:
                return text, f"cv2 scale={scale} {tag}".strip()

    return None, "未检出"


def decode_invoice_qr(file_data: bytes) -> Dict[str, str]:
    """对外入口：解码并返回字段字典。失败返回空字典，不抛异常。"""
    try:
        payload, how = decode_payload(file_data)
    except Exception as e:  # noqa: BLE001
        logger.warning("二维码解码异常: %s: %s", type(e).__name__, e)
        return {}

    if not payload:
        logger.info("二维码未解出: %s", how)
        return {}

    fields = parse_payload(payload)
    logger.info("二维码解出 (%s): %s", how, fields)
    return fields
