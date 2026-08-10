#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run one small DashScope vision request without printing the API key."""

from __future__ import print_function

import json
import os
import tempfile
from types import SimpleNamespace

from PIL import Image, ImageDraw

import smart_photo_metadata as smart


def main():
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        raise RuntimeError("当前进程未读取到 DASHSCOPE_API_KEY")

    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = os.path.join(temp_dir, "dashscope_smoke.jpg")
        image = Image.new("RGB", (960, 540), "white")
        draw = ImageDraw.Draw(image)
        draw.text(
            (80, 130),
            "Latitude 31 20\nLongitude 120 41\nDate 2026.06.09 10:30",
            fill="black",
            spacing=24,
        )
        image.save(image_path, quality=90)

        args = SimpleNamespace(
            llm_api_key="",
            dashscope_base_url="",
            llm_timeout=60,
            api_max_retries=2,
            expected_lat_min=31.0,
            expected_lat_max=32.0,
            expected_lon_min=120.0,
            expected_lon_max=121.0,
        )
        work = {
            "image_path": image_path,
            "ocr_text": (
                "Latitude 31 20 Longitude 120 41 Date 2026.06.09 10:30"
            ),
        }
        ocr_result, ocr_usage, ocr_elapsed = smart.call_dashscope_model(
            work,
            args,
            smart.DEFAULT_DASHSCOPE_OCR_MODEL,
        )
        work["dashscope_ocr_result"] = ocr_result
        review_result, review_usage, review_elapsed = smart.call_dashscope_model(
            work,
            args,
            smart.DEFAULT_DASHSCOPE_REVIEW_MODEL,
            final_review=True,
        )

    print("DashScope API: OK")
    print(
        "model=%s elapsed=%.2fs tokens=%d/%d"
        % (
            smart.DEFAULT_DASHSCOPE_OCR_MODEL,
            ocr_elapsed,
            ocr_usage["input_tokens"],
            ocr_usage["output_tokens"],
        )
    )
    print(
        "model=%s elapsed=%.2fs tokens=%d/%d"
        % (
            smart.DEFAULT_DASHSCOPE_REVIEW_MODEL,
            review_elapsed,
            review_usage["input_tokens"],
            review_usage["output_tokens"],
        )
    )
    print("ocr_result=" + json.dumps(ocr_result, ensure_ascii=False))
    print("review_result=" + json.dumps(review_result, ensure_ascii=False))


if __name__ == "__main__":
    main()
