"""详情页原件预览：把发票原件统一渲染成一张 JPEG。

图片与 PDF 走同一套前端查看器（缩放 / 平移 / 旋转），后续的字段框叠加层
也画在这张图的像素坐标上，所以这里统一做两件事：
  - 图片按 EXIF 转正。浏览器显示 <img> 时会自动按 EXIF 旋转，但像素坐标是
    原始方向的，不转正的话叠加层会和画面错位。
  - PDF 只渲染首页，与识别管线一致（视觉模型也只看第一页）。
/api/invoices/{id}/file 仍然返回原文件，用于下载。
"""

from __future__ import annotations

from io import BytesIO

PDF_PREVIEW_DPI = 150
JPEG_QUALITY = 88


def render_preview(file_data: bytes, file_type: str) -> bytes:
    from PIL import Image, ImageOps

    if (file_type or "").lower() == "pdf":
        from pdf2image import convert_from_bytes

        pages = convert_from_bytes(file_data, dpi=PDF_PREVIEW_DPI, first_page=1, last_page=1)
        if not pages:
            raise ValueError("PDF 没有可渲染的页面")
        image = pages[0]
    else:
        image = ImageOps.exif_transpose(Image.open(BytesIO(file_data)))

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()
