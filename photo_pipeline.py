#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Photo metadata completion pipeline using local OCR and a generic vision API.

The review command scans source photos, reads existing EXIF, runs Umi-OCR,
optionally uses an OpenAI-compatible vision API for uncertain rows, fuses the fields, and
creates the review workbook consumed by the existing EXIF writer.

Examples:
  python photo_pipeline.py review --image-root data/photos \
      --review-xlsx output/review.xlsx
  python photo_pipeline.py write --image-root data/photos \
      --review-xlsx output/review.xlsx --output-dir output/photos
"""

from __future__ import print_function

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from PIL import Image

import metadata_workbook as core
from exif_utils import validate_lat_lon


PIPELINE_VERSION = 3
PROMPT_VERSION = 5
DEFAULT_UMI_ENDPOINT = "http://127.0.0.1:1224/api/ocr"
DEFAULT_OCR_LIMIT_SIDE_LEN = 960
DEFAULT_API_ENDPOINT = "https://api.openai.com/v1/chat/completions"


def application_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_umi_executable():
    runtime_dir = application_dir() / "runtime"
    direct = runtime_dir / "Umi-OCR" / "Umi-OCR.exe"
    if direct.is_file():
        return str(direct)
    if runtime_dir.is_dir():
        matches = sorted(runtime_dir.glob("**/Umi-OCR.exe"))
        if matches:
            return str(matches[0])
    return str(direct)


DEFAULT_UMI_EXE = bundled_umi_executable()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Local OCR + optional vision API + EXIF field fusion"
    )
    subparsers = parser.add_subparsers(dest="command")

    review = subparsers.add_parser(
        "review",
        aliases=["REVIEW"],
        help="scan photos and create a smart review workbook",
    )
    add_common_paths(review)
    review.add_argument("--umi-ocr-exe", default=DEFAULT_UMI_EXE)
    review.add_argument("--umi-endpoint", default=DEFAULT_UMI_ENDPOINT)
    review.add_argument(
        "--llm-provider",
        choices=["openai", "none"],
        default="none",
        help="openai: OpenAI-compatible multimodal API; none: local OCR only",
    )
    review.add_argument("--llm-model", default="")
    review.add_argument(
        "--llm-endpoint",
        default="",
        help="OpenAI-compatible chat-completions endpoint",
    )
    review.add_argument("--llm-api-key", default="")
    review.add_argument("--llm-timeout", type=int, default=180)
    review.add_argument(
        "--api-max-retries",
        type=int,
        default=4,
        help="retries for 429 and temporary 5xx API failures (default: 4)",
    )
    review.add_argument(
        "--llm-batch-size",
        type=int,
        default=1,
        help=(
            "number of problem images sent in one multimodal request (default: 1; "
            "single-image calls are faster on the free Ollama cloud tier)"
        ),
    )
    review.add_argument("--ocr-timeout", type=int, default=60)
    review.add_argument(
        "--ocr-batch-size",
        type=int,
        default=32,
        help="number of image paths submitted to one Umi-OCR mission (default: 32)",
    )
    review.add_argument(
        "--llm-min-confidence",
        type=float,
        default=0.60,
        help=(
            "model confidence below this value still fills a valid candidate, "
            "but forces human review"
        ),
    )
    review.add_argument(
        "--llm-review-mode",
        choices=["all", "suspicious"],
        default="suspicious",
        help=(
            "all: review every image with incomplete EXIF; "
            "suspicious: call the model only for abnormal OCR"
        ),
    )
    review.add_argument(
        "--refresh-model",
        action="store_true",
        help=(
            "ignore successful model checkpoints from an earlier model/provider "
            "and review those images again"
        ),
    )
    review.add_argument("--auto-approve-threshold", type=float, default=0.88)
    review.add_argument(
        "--no-auto-approve",
        action="store_true",
        help="leave high-confidence rows unreviewed instead of marking automatic approval",
    )
    review.add_argument(
        "--skip-ocr",
        action="store_true",
        help="only use EXIF, filename and optional vision API",
    )
    review.add_argument(
        "--cache-file",
        default="",
        help="resumable OCR/model cache; defaults next to the review workbook",
    )
    review.add_argument(
        "--cancel-file",
        default="",
        help=argparse.SUPPRESS,
    )
    review.add_argument("--max-images", type=int, default=0)
    review.add_argument(
        "--path-contains",
        default="",
        help="process only paths containing this text (useful for a pilot run)",
    )
    review.add_argument("--photo-display-max-width", type=int, default=520)
    review.add_argument("--photo-display-max-height", type=int, default=320)
    review.add_argument("--language", choices=["zh", "en"], default="zh")
    review.add_argument(
        "--excel-image-mode",
        choices=["thumbnail", "none", "original"],
        default="thumbnail",
        help=(
            "thumbnail: embed compressed previews (default); "
            "none: keep only original-photo hyperlinks; "
            "original: embed original image bytes"
        ),
    )
    review.add_argument(
        "--thumbnail-quality",
        type=int,
        default=70,
        help="JPEG quality for embedded thumbnails (30-95)",
    )
    review.add_argument(
        "--group-coordinate-threshold-meters",
        type=float,
        default=500.0,
        help=(
            "OCR coordinates farther than this from the direct-folder median "
            "trigger vision-API review"
        ),
    )
    review.add_argument(
        "--group-min-images",
        type=int,
        default=3,
        help="minimum complete coordinate rows required for folder consistency check",
    )
    review.add_argument(
        "--no-group-consistency",
        action="store_true",
        help="disable direct-folder coordinate outlier checking",
    )
    add_expected_range(review)

    write = subparsers.add_parser(
        "write",
        aliases=["WRITE"],
        help="write approved workbook fields to copied photos",
    )
    add_common_paths(write, include_output=True)
    write.add_argument("--dry-run", action="store_true")
    write.add_argument("--in-place", action="store_true")
    write.add_argument(
        "--overwrite-existing-exif",
        action="store_true",
        help="allow reviewed values to replace EXIF fields that already exist",
    )
    write.add_argument("--photo-display-max-width", type=int, default=520)
    write.add_argument("--photo-display-max-height", type=int, default=340)
    add_expected_range(write)

    return parser


def add_common_paths(parser, include_output=False):
    parser.add_argument("--image-root", required=True)
    parser.add_argument("--review-xlsx", required=True)
    if include_output:
        parser.add_argument("--output-dir", default="reviewed_exif_output")


def add_expected_range(parser):
    parser.add_argument("--expected-lat-min", type=float, default=core.EXPECTED_LAT_MIN)
    parser.add_argument("--expected-lat-max", type=float, default=core.EXPECTED_LAT_MAX)
    parser.add_argument("--expected-lon-min", type=float, default=core.EXPECTED_LON_MIN)
    parser.add_argument("--expected-lon-max", type=float, default=core.EXPECTED_LON_MAX)


def clean(value):
    return core.clean_cell(value)


def first_exif_time(summary):
    return (
        clean(summary.get("DateTimeOriginal"))
        or clean(summary.get("DateTimeDigitized"))
        or clean(summary.get("ImageDateTime"))
    )


def normalize_datetime(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    parsed = core.parse_datetime_from_ocr(clean(value))
    return parsed or ""


def datetime_text_has_clock(value):
    """Return whether the source text visibly contains an hour and minute."""
    text = clean(value)
    if not text:
        return False
    try:
        normalized_text = core.normalize_text(text)
        return core.first_time_candidate(normalized_text) is not None
    except Exception:
        return bool(
            re.search(
                r"(?<!\d)(?:[01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)",
                text,
            )
        )


def datetime_with_visible_precision(value):
    """Normalize a visible date while preserving whether a clock was shown."""
    normalized = normalize_datetime(value)
    if not normalized:
        return "", False
    has_clock = datetime_text_has_clock(value)
    if has_clock:
        return normalized, True
    return normalized[:10], False


def datetime_from_filename(image_path):
    """Return (EXIF datetime, confidence, source note) from common camera names."""
    name = os.path.basename(image_path)
    patterns = [
        (
            r"(?i)\b(?:IMG|PXL|DSC)[_-]?(\d{8})[_-]?(\d{6})",
            0.96,
            "camera filename",
        ),
        (
            r"\u5fae\u4fe1\u56fe\u7247[_-]?(\d{8})[_-]?(\d{6})",
            0.76,
            "WeChat filename",
        ),
        (
            r"(?<!\d)(20\d{6})[_-]?(\d{6})(?!\d)",
            0.72,
            "filename",
        ),
    ]
    for pattern, confidence, note in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        try:
            value = datetime.strptime(
                match.group(1) + match.group(2), "%Y%m%d%H%M%S"
            )
        except ValueError:
            continue
        return value.strftime("%Y:%m:%d %H:%M:%S"), confidence, note
    return "", 0.0, ""


def repair_common_ocr_errors(text):
    """Apply narrow, auditable corrections before the coordinate parser."""
    original = clean(text)
    repaired = original
    replacements = {
        "\u4e1c\u7eaa": "\u4e1c\u7ecf",
        "\u4e1c\u5f84": "\u4e1c\u7ecf",
        "\u4e1c\u8f7b": "\u4e1c\u7ecf",
        "\u4e1c\u7ec3": "\u4e1c\u7ecf",
        "\u5317\u4f1f": "\u5317\u7eac",
        "\u5317\u56f4": "\u5317\u7eac",
    }
    for source, target in replacements.items():
        repaired = repaired.replace(source, target)

    # A frequent Umi result in this dataset is 720°xx for a visibly printed
    # 120°xx. Restrict the correction to an east-longitude context and to the
    # project's 120-degree region.
    if re.search(r"\u4e1c\u7ecf", repaired):
        repaired = re.sub(r"(?<!\d)[7TIl]20(?=\s*[\u00b0\u5ea6])", "120", repaired)

    return repaired, repaired != original


def decode_process_output(data):
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def open_url_no_proxy(request, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def umi_api_available(endpoint, timeout=2):
    options_url = endpoint.rsplit("/", 1)[0] + "/ocr/get_options"
    request = urllib.request.Request(options_url, method="GET")
    try:
        with open_url_no_proxy(request, timeout) as response:
            return response.status == 200
    except Exception:
        return False


def start_umi_server(executable, endpoint, timeout):
    if not os.path.isfile(executable):
        raise RuntimeError("Umi-OCR executable not found: %s" % executable)
    executable = os.path.abspath(executable)
    executable_dir = os.path.dirname(executable)
    command = [executable, "--hide"]
    kwargs = {
        "cwd": executable_dir,
        "shell": False,
    }
    restore_dll_directory = None
    if os.name == "nt":
        # Umi-OCR must not inherit the worker's redirected console handles.
        # A separate hidden console keeps its standard streams valid while the
        # GUI continues to capture this worker's progress output.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        if getattr(sys, "frozen", False):
            # PyInstaller points the process DLL search directory at its
            # temporary extraction folder. External applications must inherit
            # the normal Windows search path instead.
            import ctypes

            ctypes.windll.kernel32.SetDllDirectoryW(None)
            restore_dll_directory = getattr(sys, "_MEIPASS", None)
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        if restore_dll_directory:
            ctypes.windll.kernel32.SetDllDirectoryW(restore_dll_directory)
    startup_timeout = max(30.0, min(float(timeout), 120.0))
    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if umi_api_available(endpoint):
            return
        time.sleep(0.5)
    exit_code = process.poll()
    exit_note = (
        " The launcher exited with code %s." % exit_code
        if exit_code is not None
        else " The process is still running but its local API is unavailable."
    )
    raise RuntimeError(
        "Umi-OCR could not start its local API at %s after %.0f seconds.%s "
        "Make sure the entire portable ZIP was extracted, the runtime folder is writable, "
        "and security software is not blocking Umi-OCR.exe or local port 1224. "
        "Executable: %s"
        % (endpoint, startup_timeout, exit_note, executable)
    )


def ensure_umi_server_ready(executable, endpoint, timeout):
    """Check/start Umi once before the first OCR request in a review task."""
    if not umi_api_available(endpoint):
        start_umi_server(executable, endpoint, timeout)


def run_umi_ocr(executable, image_path, timeout, endpoint=DEFAULT_UMI_ENDPOINT):
    """Call an Umi local API that was checked once by the review task."""
    with open(image_path, "rb") as handle:
        image_base64 = base64.b64encode(handle.read()).decode("ascii")
    payload = {
        "base64": image_base64,
        "options": {
            "ocr.limit_side_len": DEFAULT_OCR_LIMIT_SIDE_LEN,
            "tbpu.parser": "multi_line",
            "data.format": "text",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with open_url_no_proxy(request, timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError("cannot reach Umi-OCR local API: %s" % exc.reason)
    if result.get("code") != 100:
        raise RuntimeError("Umi-OCR API error %s: %s" % (result.get("code"), result.get("data")))
    output = clean(result.get("data"))
    if not output:
        raise RuntimeError("Umi-OCR returned empty text")
    return output


def run_umi_ocr_batch(
    executable,
    image_paths,
    timeout,
    endpoint=DEFAULT_UMI_ENDPOINT,
):
    """Run one supported Umi HTTP request per image and return mapped results."""
    if not image_paths:
        return {}
    paths = [os.path.abspath(path) for path in image_paths]
    mapped = {}
    for path in paths:
        key = os.path.normcase(path)
        try:
            mapped[key] = {
                "text": run_umi_ocr(executable, path, timeout, endpoint),
                "error": "",
            }
        except Exception as exc:
            mapped[key] = {"text": "", "error": str(exc)}
    return mapped


def image_data_url(image_path, max_side=1800):
    with Image.open(image_path) as source:
        image = source.convert("RGB")
        if max(image.size) > max_side:
            scale = float(max_side) / float(max(image.size))
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def model_schema():
    confidence_object = {
        "type": "object",
        "properties": {
            "latitude": {"type": "number", "minimum": 0, "maximum": 1},
            "longitude": {"type": "number", "minimum": 0, "maximum": 1},
            "shooting_datetime": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["latitude", "longitude", "shooting_datetime"],
    }
    evidence_object = {
        "type": "object",
        "properties": {
            "latitude": {"type": "string"},
            "longitude": {"type": "string"},
            "shooting_datetime": {"type": "string"},
        },
        "required": ["latitude", "longitude", "shooting_datetime"],
    }
    return {
        "type": "object",
        "properties": {
            "latitude_text": {"type": ["string", "null"]},
            "longitude_text": {"type": ["string", "null"]},
            "shooting_datetime_text": {"type": ["string", "null"]},
            "confidence": confidence_object,
            "evidence": evidence_object,
            "notes": {"type": "string"},
        },
        "required": [
            "latitude_text",
            "longitude_text",
            "shooting_datetime_text",
            "confidence",
            "evidence",
            "notes",
        ],
    }


def model_prompt(ocr_text, args):
    return (
        "\u4f60\u662f\u56fe\u7247\u6c34\u5370\u5b57\u6bb5\u6821\u5bf9\u5668\u3002"
        "\u53ea\u8f6c\u5199\u56fe\u7247\u4e0a\u660e\u786e\u53ef\u89c1\u7684\u7ecf\u5ea6\u3001\u7eac\u5ea6\u548c\u62cd\u6444\u65e5\u671f/\u65f6\u95f4\uff1b"
        "\u4e0d\u5f97\u6839\u636e\u5730\u540d\u3001\u5efa\u7b51\u7269\u3001\u6587\u4ef6\u540d\u6216\u5e38\u8bc6\u63a8\u6d4b\u7f3a\u5931\u503c\u3002"
        "\u82e5\u5b57\u6bb5\u770b\u4e0d\u6e05\u6216\u4e0d\u5b58\u5728\uff0c\u8fd4\u56de null \u4e14\u964d\u4f4e\u7f6e\u4fe1\u5ea6\u3002"
        "\u6ce8\u610f OCR \u5e38\u5c06 120 \u8bc6\u522b\u4e3a 720\uff0c\u5c06\u201c\u4e1c\u7ecf\u201d\u8bc6\u522b\u4e3a\u201c\u4e1c\u7eaa\u201d\u3002"
        "\u5728 latitude_text/longitude_text \u4e2d\u9010\u5b57\u8fd4\u56de\u6c34\u5370\u539f\u6587\uff08\u4f8b\u5982 31\u00b020' \u3001120\u00b041'\uff09\uff0c"
        "\u4e0d\u8981\u6362\u7b97\u6210\u5341\u8fdb\u5236\uff0c\u6362\u7b97\u7531\u7a0b\u5e8f\u5b8c\u6210\u3002"
        "\u5728 shooting_datetime_text \u4e2d\u8fd4\u56de\u6c34\u5370\u65e5\u671f\u539f\u6587\u3002"
        "evidence \u5fc5\u987b\u662f\u4f60\u4ece\u56fe\u50cf\u50cf\u7d20\u4e2d\u5b9e\u9645\u770b\u5230\u7684\u77ed\u6587\u672c\uff0c"
        "\u4e0d\u80fd\u590d\u5236\u540e\u9762\u7684 OCR \u6587\u672c\u3002"
        "\n\u9879\u76ee\u9884\u671f\u7eac\u5ea6 %.6f~%.6f\uff0c\u7ecf\u5ea6 %.6f~%.6f\uff1b"
        "\u8303\u56f4\u53ea\u7528\u4e8e\u53d1\u73b0 OCR \u9519\u8bef\uff0c\u4e0d\u80fd\u7528\u4e8e\u731c\u6d4b\u3002"
        "\nUmi-OCR \u6587\u672c\uff08\u53ef\u80fd\u6709\u9519\uff09\uff1a\n%s"
        % (
            args.expected_lat_min,
            args.expected_lat_max,
            args.expected_lon_min,
            args.expected_lon_max,
            ocr_text or "(empty)",
        )
    )


def http_json(url, payload, timeout, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("model API HTTP %s: %s" % (exc.code, detail[:500]))
    except urllib.error.URLError as exc:
        raise RuntimeError("cannot reach model API: %s" % exc.reason)
    return json.loads(raw.decode("utf-8"))


def http_json_with_retry(url, payload, timeout, headers=None, max_retries=4):
    """Retry only throttling and temporary provider failures."""
    retryable = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")
    last_error = None
    for attempt in range(max(0, int(max_retries)) + 1):
        try:
            return http_json(url, payload, timeout, headers=headers)
        except RuntimeError as exc:
            last_error = exc
            if not any(code in str(exc) for code in retryable):
                raise
            if attempt >= int(max_retries):
                break
            delay = min(20.0, 1.5 * (2 ** attempt))
            time.sleep(delay)
    raise last_error


def extract_json_object(value):
    if isinstance(value, dict):
        return value
    text = clean(value)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise RuntimeError("model API did not return JSON: %s" % text[:300])


def call_vision_api(image_path, ocr_text, args):
    data_url = image_data_url(image_path)
    prompt = model_prompt(ocr_text, args)
    endpoint = clean(args.llm_endpoint) or DEFAULT_API_ENDPOINT
    headers = {}
    if args.llm_api_key:
        headers["Authorization"] = "Bearer " + args.llm_api_key
    payload = {
        "model": args.llm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "\u4e25\u683c\u8f93\u51fa\u4e00\u4e2a JSON \u5bf9\u8c61\uff0c\u4e0d\u8981\u8f93\u51fa\u989d\u5916\u6587\u5b57\u3002",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt + "\nJSON schema:\n" + json.dumps(model_schema(), ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    response = http_json_with_retry(
        endpoint,
        payload,
        args.llm_timeout,
        headers=headers,
        max_retries=args.api_max_retries,
    )
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI-compatible API returned no choices")
    return extract_json_object((choices[0].get("message") or {}).get("content"))


def batch_model_schema():
    single = model_schema()
    item_properties = {"id": {"type": "string"}}
    item_properties.update(single["properties"])
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": item_properties,
                    "required": ["id"] + list(single["required"]),
                },
            }
        },
        "required": ["results"],
    }


def batch_model_prompt(batch, args):
    lines = [
        "你是图片水印字段批量校对器。以下图片与编号严格按顺序一一对应。",
        "必须独立查看每张图片，不得把一张图片的字段复制或推测到另一张。",
        "只转写像素中明确可见的经度、纬度和拍摄日期/时间；看不清则返回 null。",
        "latitude_text/longitude_text 保留水印原文，不换算十进制度。",
        "evidence 必须来自对应图片像素，不能复制 OCR 文本。",
        "请为每个编号返回且只返回一条结果，不得漏项。",
        "项目预期纬度 %.6f~%.6f，经度 %.6f~%.6f；范围仅用于发现错误，不得用于猜测。"
        % (
            args.expected_lat_min,
            args.expected_lat_max,
            args.expected_lon_min,
            args.expected_lon_max,
        ),
    ]
    for index, work in enumerate(batch, start=1):
        lines.extend(
            [
                "",
                "图片%d，id=%s" % (index, work["batch_id"]),
                "对应 Umi-OCR 文本（可能有错）：",
                clean(work.get("ocr_text")) or "(empty)",
            ]
        )
    return "\n".join(lines)


def parse_batch_model_response(value, batch):
    parsed = extract_json_object(value)
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        raise RuntimeError("batch model response has no results array")
    expected = {work["batch_id"] for work in batch}
    mapped = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        result_id = clean(result.get("id"))
        if result_id not in expected or result_id in mapped:
            continue
        mapped[result_id] = {
            key: value
            for key, value in result.items()
            if key != "id"
        }
    missing = expected.difference(mapped)
    if missing:
        raise RuntimeError(
            "batch model response missing ids: %s" % ", ".join(sorted(missing))
        )
    return mapped


def call_vision_api_batch(batch, args):
    """Review multiple problem images in one multimodal request."""
    if not batch:
        return {}
    if len(batch) == 1:
        work = batch[0]
        return {
            work["batch_id"]: call_vision_api(
                work["image_path"],
                work["ocr_text"],
                args,
            )
        }

    data_urls = [image_data_url(work["image_path"]) for work in batch]
    prompt = batch_model_prompt(batch, args)
    endpoint = clean(args.llm_endpoint) or DEFAULT_API_ENDPOINT
    headers = {}
    if args.llm_api_key:
        headers["Authorization"] = "Bearer " + args.llm_api_key
    content = [
        {
            "type": "text",
            "text": prompt
            + "\nJSON schema:\n"
            + json.dumps(batch_model_schema(), ensure_ascii=False),
        }
    ]
    for work, data_url in zip(batch, data_urls):
        content.append(
            {
                "type": "text",
                "text": "下一张图片的 id=%s" % work["batch_id"],
            }
        )
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    payload = {
        "model": args.llm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "严格输出一个符合 schema 的 JSON 对象，不要输出额外文字。",
            },
            {"role": "user", "content": content},
        ],
    }
    response = http_json_with_retry(
        endpoint,
        payload,
        args.llm_timeout,
        headers=headers,
        max_retries=args.api_max_retries,
    )
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI-compatible API returned no choices")
    return parse_batch_model_response(
        (choices[0].get("message") or {}).get("content"),
        batch,
    )


class ResumeCache(object):
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.journal_path = self.path + ".journal"
        self.data = {"version": PIPELINE_VERSION, "items": {}}
        self.current_key = None
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if loaded.get("version") == PIPELINE_VERSION:
                self.data = loaded
        except (OSError, ValueError):
            pass
        self._load_journal()

    def _load_journal(self):
        """Replay per-image checkpoints left by a completed or interrupted run."""
        try:
            with open(self.journal_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except (TypeError, ValueError):
                        # A process killed during the final append can leave one
                        # incomplete line. Earlier checkpoints remain valid.
                        continue
                    key = entry.get("key")
                    item = entry.get("item")
                    if key and isinstance(item, dict):
                        self.data["items"][key] = item
        except OSError:
            pass

    @staticmethod
    def signature(path):
        stat = os.stat(path)
        raw = "%s:%s:%s" % (os.path.abspath(path), stat.st_size, stat.st_mtime_ns)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def item(self, key, image_path):
        self.current_key = key
        item = self.data["items"].get(key) or {}
        signature = self.signature(image_path)
        if item.get("signature") != signature:
            item = {"signature": signature}
            self.data["items"][key] = item
        return item

    def save(self, key=None):
        """Append one image checkpoint instead of rewriting the whole cache.

        The previous implementation serialized every cached image after every
        OCR/model call. For large jobs that made cache I/O grow quadratically.
        """
        key = key or self.current_key
        if not key:
            raise ValueError("cache key is required")
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        entry = {
            "key": key,
            "item": self.data["items"].get(key) or {},
        }
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()

    def compact(self):
        """Atomically compact checkpoints after a successful complete run."""
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        fd, temp_path = tempfile.mkstemp(
            prefix=".smart_metadata_", suffix=".json", dir=parent or None
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
            try:
                os.remove(self.journal_path)
            except OSError:
                pass
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise


class TaskCancelled(Exception):
    pass


def check_cancelled(args):
    cancel_file = clean(getattr(args, "cancel_file", ""))
    if cancel_file and os.path.exists(cancel_file):
        raise TaskCancelled("用户已停止任务；已完成图片的断点已保存")


def review_result_cache_key(args):
    """Key all options that can change a preliminary merged review row."""
    values = {
        "pipeline": PIPELINE_VERSION,
        "prompt": PROMPT_VERSION,
        "provider": args.llm_provider,
        "model": args.llm_model,
        "review_mode": args.llm_review_mode,
        "llm_min_confidence": args.llm_min_confidence,
        "auto_approve_threshold": args.auto_approve_threshold,
        "no_auto_approve": args.no_auto_approve,
        "skip_ocr": args.skip_ocr,
        "expected_lat_min": args.expected_lat_min,
        "expected_lat_max": args.expected_lat_max,
        "expected_lon_min": args.expected_lon_min,
        "expected_lon_max": args.expected_lon_max,
    }
    raw = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def model_batch_id(relative_path):
    digest = hashlib.sha256(
        os.path.normcase(relative_path).encode("utf-8")
    ).hexdigest()
    return "img_" + digest[:16]


def review_model_batches(pending, args, cache, model_cache_key, counts):
    """Resolve model work in batches, splitting failed batches down to singles."""
    if not pending:
        return
    batch_size = int(args.llm_batch_size)
    if batch_size < 1 or batch_size > 16:
        raise ValueError("--llm-batch-size must be between 1 and 16")

    total = len(pending)
    progress = {"done": 0}

    def store(work, result, error):
        work["model_result"] = result
        work["model_error"] = clean(error)
        work["item"]["model"] = {
            "key": model_cache_key,
            "result": result,
            "error": clean(error),
        }
        cache.save(work["relative"])
        progress["done"] += 1
        print(
            "[model %d/%d] 已完成模型复核：%s"
            % (progress["done"], total, work["relative"]),
            flush=True,
        )

    def process_batch(batch):
        check_cancelled(args)
        counts["model"] += 1
        try:
            mapped = call_vision_api_batch(batch, args)
        except Exception as exc:
            if isinstance(exc, TaskCancelled):
                raise
            if len(batch) > 1:
                midpoint = max(1, len(batch) // 2)
                print(
                    "[model split] %d 张批量复核失败，自动拆分：%s"
                    % (len(batch), clean(exc)),
                    flush=True,
                )
                process_batch(batch[:midpoint])
                process_batch(batch[midpoint:])
                return
            counts["model_error"] += 1
            store(batch[0], None, str(exc))
            return

        for work in batch:
            result = mapped.get(work["batch_id"])
            if not isinstance(result, dict):
                # This should already be caught by response validation, but keep
                # per-image cache state explicit if a provider behaves oddly.
                counts["model_error"] += 1
                store(work, None, "batch model returned no mapped result")
            else:
                store(work, result, "")
        check_cancelled(args)

    for start in range(0, total, batch_size):
        process_batch(pending[start : start + batch_size])


def expected_range(lat, lon, args):
    return (
        args.expected_lat_min <= float(lat) <= args.expected_lat_max
        and args.expected_lon_min <= float(lon) <= args.expected_lon_max
    )


def valid_model_coordinate(value, kind, args):
    if value is None or clean(value).lower() in ("", "null", "none"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        try:
            parsed = core.parse_coordinate_segment(clean(value), kind)
        except (TypeError, ValueError):
            # Model output is untrusted. A malformed or field-swapped value
            # should trigger final visual review, not abort the entire batch.
            return None
    parsed = abs(parsed)
    if kind == "lat" and not (args.expected_lat_min <= parsed <= args.expected_lat_max):
        return None
    if kind == "lon" and not (args.expected_lon_min <= parsed <= args.expected_lon_max):
        return None
    return parsed


def confidence_value(model_result, field):
    try:
        return max(0.0, min(1.0, float((model_result.get("confidence") or {}).get(field, 0))))
    except (TypeError, ValueError):
        return 0.0


def evidence_value(model_result, field):
    return clean((model_result.get("evidence") or {}).get(field))


def model_text_value(model_result, text_key, legacy_key):
    value = model_result.get(text_key)
    if value is None:
        value = model_result.get(legacy_key)
    return value


def parse_ocr_fields(ocr_text, args):
    repaired, was_repaired = repair_common_ocr_errors(ocr_text)
    fields = {
        "lat": None,
        "lon": None,
        "time": "",
        "time_has_clock": False,
        "repaired_text": repaired,
        "was_repaired": was_repaired,
        "issues": [],
    }
    try:
        _, _, lat, lon = core.gps_candidates_from_ocr(repaired)
        if lat is None:
            fields["issues"].append("OCR latitude missing")
        elif not (args.expected_lat_min <= float(lat) <= args.expected_lat_max):
            fields["issues"].append(
                "OCR latitude outside expected range and discarded: %.8f" % float(lat)
            )
        else:
            fields["lat"] = lat

        if lon is None:
            fields["issues"].append("OCR longitude missing")
        elif not (args.expected_lon_min <= float(lon) <= args.expected_lon_max):
            fields["issues"].append(
                "OCR longitude outside expected range and discarded: %.8f" % float(lon)
            )
        else:
            fields["lon"] = lon
    except Exception as exc:
        fields["issues"].append("OCR coordinate parse failed: %s" % exc)
    fields["time"], fields["time_has_clock"] = datetime_with_visible_precision(
        repaired
    )
    if not fields["time"]:
        fields["issues"].append("OCR shooting date missing")
    elif not fields["time_has_clock"]:
        fields["issues"].append(
            "OCR shooting time missing (date only); visual review requested"
        )
    return fields


def date_part(value):
    normalized = normalize_datetime(value)
    return normalized[:10] if normalized else ""


def merge_fields(image_path, exif, ocr_text, model_result, model_error, args):
    """Fuse fields with EXIF > visual model > OCR priority.

    A valid in-range model value is always shown as the candidate when the
    corresponding EXIF field is absent. Low model confidence does not blank
    the candidate; it only prevents automatic approval.
    """
    tips = []
    conflicts = []
    model_notes = []
    ocr = parse_ocr_fields(ocr_text, args)
    tips.extend(ocr["issues"])
    if ocr["was_repaired"]:
        tips.append("common OCR character/digit repair applied")
    if model_error:
        tips.append("visual model unavailable: %s" % model_error)

    values = {"lat": None, "lon": None, "time": ""}
    sources = {"lat": "", "lon": "", "time": ""}
    confidence = {"lat": 0.0, "lon": 0.0, "time": 0.0}
    trusted = {"lat": False, "lon": False, "time": False}

    exif_lat = clean(exif.get("exif_lat"))
    exif_lon = clean(exif.get("exif_lon"))
    exif_time = normalize_datetime(first_exif_time(exif))
    if exif_lat and exif_lon:
        values["lat"], values["lon"] = abs(float(exif_lat)), abs(float(exif_lon))
        sources["lat"] = sources["lon"] = "EXIF"
        confidence["lat"] = confidence["lon"] = 1.0
        trusted["lat"] = trusted["lon"] = True
    if exif_time:
        values["time"] = exif_time
        sources["time"] = "EXIF"
        confidence["time"] = 1.0
        trusted["time"] = True

    # The model is the primary visual source whenever EXIF does not already
    # own a field. OCR is used for comparison and as a fallback.
    if model_result:
        model_coordinates = {}
        for target, text_key, legacy_key, kind in (
            ("lat", "latitude_text", "latitude", "lat"),
            ("lon", "longitude_text", "longitude", "lon"),
        ):
            raw_value = model_text_value(model_result, text_key, legacy_key)
            try:
                candidate = valid_model_coordinate(raw_value, kind, args)
            except Exception as exc:
                candidate = None
                tips.append("invalid vision-API %s: %s" % (target, exc))
            if raw_value not in (None, "") and candidate is None:
                tips.append(
                    "vision-API %s is invalid or outside expected range and was discarded"
                    % target
                )
            model_coordinates[target] = candidate

        raw_model_time = model_text_value(
            model_result,
            "shooting_datetime_text",
            "shooting_datetime",
        )
        model_time, model_time_has_clock = datetime_with_visible_precision(
            raw_model_time
        )
        mapping = [
            ("lat", "latitude", model_coordinates["lat"], 0.0008),
            ("lon", "longitude", model_coordinates["lon"], 0.0008),
        ]
        for target, model_key, candidate, tolerance in mapping:
            model_conf = confidence_value(model_result, model_key)
            evidence = evidence_value(model_result, model_key)
            if candidate is None:
                continue

            if sources[target] == "EXIF":
                if abs(float(values[target]) - float(candidate)) > tolerance:
                    conflicts.append(
                        "%s conflict: EXIF %.8f, model %.8f; EXIF preserved"
                        % (target, values[target], candidate)
                    )
                else:
                    tips.append("vision API agrees with EXIF %s" % target)
                continue

            values[target] = candidate
            sources[target] = "大模型 API"
            confidence[target] = model_conf
            has_evidence = bool(evidence)
            trusted[target] = bool(
                has_evidence and model_conf >= args.llm_min_confidence
            )

            if not has_evidence:
                tips.append(
                    "vision-API %s candidate filled but visible evidence is missing"
                    % target
                )
            if model_conf < args.llm_min_confidence:
                tips.append(
                    "vision-API %s confidence is very low (%.2f); candidate filled for review"
                    % (target, model_conf)
                )

            ocr_candidate = ocr.get(target)
            if ocr_candidate is not None:
                if abs(float(ocr_candidate) - float(candidate)) <= tolerance:
                    sources[target] += "+OCR agrees"
                else:
                    tips.append(
                        "vision API overrode OCR %s: OCR %.8f -> model %.8f"
                        % (target, float(ocr_candidate), float(candidate))
                    )

        model_time_conf = confidence_value(model_result, "shooting_datetime")
        model_time_evidence = evidence_value(model_result, "shooting_datetime")
        if model_time:
            if sources["time"] == "EXIF":
                if date_part(values["time"]) != date_part(model_time):
                    conflicts.append(
                        "date conflict: EXIF %s, model %s; EXIF preserved"
                        % (values["time"], model_time)
                    )
                else:
                    tips.append("vision API agrees with EXIF date")
            else:
                ocr_has_more_precise_time = bool(
                    ocr["time"]
                    and ocr["time_has_clock"]
                    and not model_time_has_clock
                    and date_part(ocr["time"]) == date_part(model_time)
                )
                if ocr_has_more_precise_time:
                    values["time"] = ocr["time"]
                    sources["time"] = "Umi-OCR+model date agrees"
                    confidence["time"] = min(
                        0.96,
                        max(0.92, model_time_conf),
                    )
                    trusted["time"] = bool(
                        model_time_evidence
                        and model_time_conf >= args.llm_min_confidence
                    )
                    tips.append(
                        "Umi-OCR clock time preserved because the visual model "
                        "confirmed the same date but returned date only"
                    )
                else:
                    values["time"] = model_time
                    sources["time"] = "大模型 API"
                    confidence["time"] = model_time_conf
                    trusted["time"] = bool(
                        model_time_evidence
                        and model_time_conf >= args.llm_min_confidence
                    )
                if not model_time_evidence:
                    tips.append(
                        "vision-API date candidate filled but visible evidence is missing"
                    )
                if model_time_conf < args.llm_min_confidence:
                    tips.append(
                        "vision-API date confidence is very low (%.2f); candidate filled for review"
                        % model_time_conf
                    )
                if ocr["time"]:
                    if date_part(ocr["time"]) == date_part(model_time):
                        sources["time"] += "+OCR agrees"
                    else:
                        tips.append(
                            "vision API overrode OCR date: OCR %s -> model %s"
                            % (ocr["time"], model_time)
                        )

        notes = clean(model_result.get("notes"))
        if notes:
            model_notes.append(notes)
        evidence_parts = []
        for key, label in (
            ("latitude", "\u7eac\u5ea6"),
            ("longitude", "\u7ecf\u5ea6"),
            ("shooting_datetime", "\u65e5\u671f"),
        ):
            evidence = evidence_value(model_result, key)
            if evidence:
                evidence_parts.append("%s=%s" % (label, evidence))
        if evidence_parts:
            model_notes.append("\u53ef\u89c1\u8bc1\u636e: " + "; ".join(evidence_parts))

    # OCR fills only fields for which neither EXIF nor a valid model candidate
    # is available. Values outside the expected range were already discarded
    # independently by parse_ocr_fields().
    for target in ("lat", "lon"):
        if values[target] is None and ocr[target] is not None:
            values[target] = abs(float(ocr[target]))
            sources[target] = "Umi-OCR"
            confidence[target] = 0.88 if ocr["was_repaired"] else 0.92
            trusted[target] = bool(
                not ocr["was_repaired"]
                and confidence[target] >= args.auto_approve_threshold
            )
            if ocr["was_repaired"]:
                tips.append(
                    "repaired OCR %s used as fallback; human review required" % target
                )

    if not values["time"] and ocr["time"]:
        values["time"] = ocr["time"]
        sources["time"] = "Umi-OCR"
        confidence["time"] = 0.92
        trusted["time"] = bool(
            confidence["time"] >= args.auto_approve_threshold
        )

    if conflicts:
        tips.extend(conflicts)

    complete = bool(values["lat"] is not None and values["lon"] is not None and values["time"])
    in_range = bool(
        values["lat"] is not None
        and values["lon"] is not None
        and expected_range(values["lat"], values["lon"], args)
    )
    if not complete:
        for field, label in (("lat", "latitude"), ("lon", "longitude"), ("time", "shooting date")):
            if not values[field]:
                tips.append("%s still missing" % label)

    exif_complete = bool(exif_lat and exif_lon and exif_time)
    automatic_ok = (
        complete
        and in_range
        and not conflicts
        and all(trusted.values())
    )
    if exif_complete:
        status = core.STATUS_EXIF_COMPLETE
        decision = core.V_SKIP
    elif automatic_ok:
        status = "AUTO_OK"
        decision = core.V_UNREVIEWED if args.no_auto_approve else core.V_AUTO_APPROVED
    else:
        status = "NEEDS_REVIEW"
        decision = core.V_CHECK

    source_text = "; ".join(
        [
            "\u7ecf\u5ea6=%s" % (sources["lon"] or "-"),
            "\u7eac\u5ea6=%s" % (sources["lat"] or "-"),
            "\u65f6\u95f4=%s" % (sources["time"] or "-"),
        ]
    )
    confidence_text = "; ".join(
        [
            "\u7ecf\u5ea6=%.2f" % confidence["lon"],
            "\u7eac\u5ea6=%.2f" % confidence["lat"],
            "\u65f6\u95f4=%.2f" % confidence["time"],
        ]
    )
    return {
        core.L_REVIEW: decision,
        core.L_PARSE_STATUS: status,
        core.L_SOURCE: source_text,
        core.L_OCR_TEXT: ocr_text,
        core.L_LON: "%.8f" % values["lon"] if values["lon"] is not None else "",
        core.L_LAT: "%.8f" % values["lat"] if values["lat"] is not None else "",
        core.L_TIME: values["time"],
        core.L_CONFIDENCE: confidence_text,
        core.L_PHOTO: "",
        core.L_PHOTO_LINK: image_path,
        core.L_SOURCE_PATH: image_path,
        core.L_TIPS: "; ".join(dict.fromkeys(tips)),
        core.L_MODEL_NOTE: "; ".join(model_notes),
        core.L_NOTE: "",
    }


def iter_images(image_root, path_contains=""):
    path_contains = clean(path_contains).lower()
    for root, directories, files in os.walk(image_root):
        directories.sort()
        for name in sorted(files):
            path = os.path.abspath(os.path.join(root, name))
            if os.path.splitext(name)[1].lower() not in core.IMAGE_EXTENSIONS:
                continue
            if path_contains and path_contains not in path.lower():
                continue
            yield path


def needs_model(exif, ocr_fields, review_mode="all"):
    if clean(exif.get("exif_lat")) and clean(exif.get("exif_lon")) and first_exif_time(exif):
        return False
    if review_mode == "all":
        return True
    if ocr_fields["was_repaired"]:
        return True
    if ocr_fields["issues"]:
        return True
    return False


def haversine_meters(lat1, lon1, lat2, lon2):
    """Return the great-circle distance between two decimal-degree points."""
    radius = 6371008.8
    lat1_rad = math.radians(float(lat1))
    lat2_rad = math.radians(float(lat2))
    delta_lat = lat2_rad - lat1_rad
    delta_lon = math.radians(float(lon2) - float(lon1))
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2.0) ** 2
    )
    return radius * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def folder_coordinate_outliers(
    work_items,
    threshold_meters=500.0,
    min_images=3,
):
    """Find complete coordinate rows far from their direct-folder median."""
    groups = {}
    for index, work in enumerate(work_items):
        record = work["record"]
        lat_text = clean(record.get(core.L_LAT))
        lon_text = clean(record.get(core.L_LON))
        if not lat_text or not lon_text:
            continue
        try:
            lat = float(lat_text)
            lon = float(lon_text)
        except (TypeError, ValueError):
            continue
        group_key = os.path.normcase(os.path.dirname(work["relative"]))
        groups.setdefault(group_key, []).append((index, lat, lon))

    outliers = []
    required = max(3, int(min_images))
    for group_key, points in groups.items():
        if len(points) < required:
            continue
        median_lat = statistics.median(point[1] for point in points)
        median_lon = statistics.median(point[2] for point in points)
        for index, lat, lon in points:
            distance = haversine_meters(lat, lon, median_lat, median_lon)
            if distance > float(threshold_meters):
                outliers.append(
                    {
                        "index": index,
                        "group": group_key,
                        "median_lat": median_lat,
                        "median_lon": median_lon,
                        "distance": distance,
                    }
                )
    return outliers


def append_record_tip(record, message):
    tips = [part.strip() for part in clean(record.get(core.L_TIPS)).split(";") if part.strip()]
    if message not in tips:
        tips.append(message)
    record[core.L_TIPS] = "; ".join(tips)


def force_human_review(record):
    if record.get(core.L_PARSE_STATUS) != core.STATUS_EXIF_COMPLETE:
        record[core.L_PARSE_STATUS] = "NEEDS_REVIEW"
        record[core.L_REVIEW] = core.V_CHECK


def create_review(args):
    image_root = os.path.abspath(args.image_root)
    review_xlsx = os.path.abspath(args.review_xlsx)
    if not os.path.isdir(image_root):
        raise ValueError("image root not found: %s" % image_root)
    if int(args.llm_batch_size) < 1 or int(args.llm_batch_size) > 16:
        raise ValueError("--llm-batch-size must be between 1 and 16")
    if int(args.ocr_batch_size) < 1 or int(args.ocr_batch_size) > 256:
        raise ValueError("--ocr-batch-size must be between 1 and 256")
    cache_path = args.cache_file or os.path.splitext(review_xlsx)[0] + ".cache.json"
    cache = ResumeCache(cache_path)
    image_paths = list(iter_images(image_root, args.path_contains))
    if args.max_images > 0:
        image_paths = image_paths[: args.max_images]
    if not image_paths:
        raise ValueError("no supported images found: %s" % image_root)

    model_descriptor = args.llm_model
    print(
        "Smart review: images=%d, llm=%s/%s, cache=%s"
        % (len(image_paths), args.llm_provider, model_descriptor, cache.path)
    )
    records = []
    work_items = []
    counts = {
        "model": 0,
        "auto": 0,
        "review": 0,
        "exif": 0,
        "ocr_error": 0,
        "model_error": 0,
    }
    model_cache_key = "%s:%s:%s" % (
        PROMPT_VERSION,
        args.llm_provider,
        model_descriptor,
    )
    result_cache_key = review_result_cache_key(args)
    prepared_items = []
    resume_prefix = 0
    prefix_is_complete = True
    for image_path in image_paths:
        relative = os.path.relpath(image_path, image_root)
        existing_item = cache.data["items"].get(relative)
        item = cache.item(relative, image_path)
        has_checkpoint = bool(existing_item) and existing_item is item
        cached_exif = item.get("exif")
        cached_exif_complete = bool(
            isinstance(cached_exif, dict)
            and clean(cached_exif.get("exif_lat"))
            and clean(cached_exif.get("exif_lon"))
            and first_exif_time(cached_exif)
        )
        retry_ocr_failure = bool(
            clean(item.get("ocr_error"))
            and not args.skip_ocr
            and not cached_exif_complete
        )
        cached_model = item.get("model")
        retry_model_failure = bool(
            args.llm_provider != "none"
            and isinstance(cached_model, dict)
            and clean(cached_model.get("error"))
        )
        is_completed = bool(
            item.get("result_key") == result_cache_key
            and isinstance(item.get("record"), dict)
            and isinstance(item.get("exif"), dict)
            and not retry_ocr_failure
            and not retry_model_failure
        )
        prepared_items.append(
            (image_path, relative, item, is_completed, has_checkpoint)
        )
        if prefix_is_complete and has_checkpoint:
            resume_prefix += 1
        else:
            prefix_is_complete = False

    if resume_prefix:
        if resume_prefix == len(image_paths):
            resume_message = "全部图片均已从断点恢复"
        else:
            resume_message = "已恢复上次完成的断点，将从第 %d 张继续" % (
                resume_prefix + 1
            )
        print(
            "[resume %d/%d] %s"
            % (resume_prefix, len(image_paths), resume_message),
            flush=True,
        )

    resumed_count = 0
    progress_step = max(1, len(image_paths) // 500)
    pending_model = []
    stage_items = []
    pending_ocr = []

    # Phase 1a: read EXIF and collect every image that still needs OCR. The
    # actual OCR calls are deliberately deferred so progress and checkpointing
    # can be managed in predictable groups.
    for prepared in prepared_items:
        image_path, relative, item, is_completed, has_checkpoint = prepared
        check_cancelled(args)
        if is_completed:
            resumed_count += 1
            stage_items.append(
                {
                    "relative": relative,
                    "image_path": image_path,
                    "item": item,
                    "exif": dict(item["exif"]),
                    "completed": True,
                    "has_checkpoint": has_checkpoint,
                }
            )
            continue

        cached_exif = item.get("exif")
        exif = (
            dict(cached_exif)
            if isinstance(cached_exif, dict)
            else core.read_exif_summary(image_path)
        )
        item["exif"] = exif
        stage = {
            "relative": relative,
            "image_path": image_path,
            "item": item,
            "exif": exif,
            "completed": False,
            "has_checkpoint": has_checkpoint,
        }
        stage_items.append(stage)
        exif_complete = bool(
            clean(exif.get("exif_lat"))
            and clean(exif.get("exif_lon"))
            and first_exif_time(exif)
        )
        if (
            not exif_complete
            and not args.skip_ocr
            and not clean(item.get("ocr_text"))
        ):
            pending_ocr.append(stage)

    # Phase 1b: one readiness check, then use Umi's documented image OCR API.
    # Individual image failures are checkpointed so later runs can retry them.
    ocr_done = len(image_paths) - len(pending_ocr)
    if pending_ocr:
        ensure_umi_server_ready(
            args.umi_ocr_exe,
            args.umi_endpoint,
            args.ocr_timeout,
        )
        for offset in range(0, len(pending_ocr), int(args.ocr_batch_size)):
            check_cancelled(args)
            batch = pending_ocr[offset : offset + int(args.ocr_batch_size)]
            started = time.perf_counter()
            batch_results = run_umi_ocr_batch(
                args.umi_ocr_exe,
                [stage["image_path"] for stage in batch],
                args.ocr_timeout,
                args.umi_endpoint,
            )
            elapsed = time.perf_counter() - started
            for stage in batch:
                key = os.path.normcase(os.path.abspath(stage["image_path"]))
                result = batch_results[key]
                item = stage["item"]
                if result["error"]:
                    item["ocr_error"] = result["error"]
                    counts["ocr_error"] += 1
                else:
                    item["ocr_text"] = result["text"]
                    item.pop("ocr_error", None)
                cache.save(stage["relative"])
                ocr_done += 1
            print(
                "[ocr %d/%d] Umi OCR 完成 %d 张，耗时 %.2f 秒（%.2f 秒/张）"
                % (
                    ocr_done,
                    len(image_paths),
                    len(batch),
                    elapsed,
                    elapsed / len(batch),
                ),
                flush=True,
            )

    # Phase 1c: parse the completed OCR checkpoints and decide which images
    # need the model. No model request can begin before this loop.
    for stage in stage_items:
        relative = stage["relative"]
        image_path = stage["image_path"]
        item = stage["item"]
        exif = stage["exif"]
        has_checkpoint = stage["has_checkpoint"]
        if stage["completed"]:
            work_items.append(
                {
                    "relative": relative,
                    "image_path": image_path,
                    "item": item,
                    "exif": exif,
                    "ocr_text": clean(item.get("ocr_text")),
                    "model_result": item.get("model_result"),
                    "model_error": clean(item.get("model_error")),
                    "record": dict(item["record"]),
                    "has_checkpoint": has_checkpoint,
                    "batch_id": model_batch_id(relative),
                }
            )
            continue

        ocr_text = clean(item.get("ocr_text"))
        ocr_error = clean(item.get("ocr_error"))
        ocr_fields = parse_ocr_fields(ocr_text, args)
        model_result = None
        model_error = ""
        should_call_model = (
            args.llm_provider != "none"
            and needs_model(exif, ocr_fields, args.llm_review_mode)
        )
        if should_call_model:
            cached_model = item.get("model")
            exact_model_checkpoint = bool(
                isinstance(cached_model, dict)
                and cached_model.get("key") == model_cache_key
                and isinstance(cached_model.get("result"), dict)
                and not clean(cached_model.get("error"))
                and not args.refresh_model
            )
            reusable_model_checkpoint = bool(
                isinstance(cached_model, dict)
                and isinstance(cached_model.get("result"), dict)
                and not clean(cached_model.get("error"))
                and clean(cached_model.get("key")).startswith(
                    str(PROMPT_VERSION) + ":"
                )
                and not args.refresh_model
            )
            if exact_model_checkpoint or reusable_model_checkpoint:
                model_result = cached_model.get("result")
                model_error = clean(cached_model.get("error"))

        cache.save(relative)
        work = {
            "relative": relative,
            "image_path": image_path,
            "item": item,
            "exif": exif,
            "ocr_text": ocr_text,
            "ocr_error": ocr_error,
            "model_result": model_result,
            "model_error": model_error,
            "record": None,
            "has_checkpoint": has_checkpoint,
            "batch_id": model_batch_id(relative),
        }
        work_items.append(work)
        cached_model = item.get("model")
        has_exact_model = bool(
            isinstance(cached_model, dict)
            and cached_model.get("key") == model_cache_key
            and isinstance(cached_model.get("result"), dict)
            and not clean(cached_model.get("error"))
            and not args.refresh_model
        )
        has_reusable_model = bool(
            isinstance(cached_model, dict)
            and isinstance(cached_model.get("result"), dict)
            and not clean(cached_model.get("error"))
            and clean(cached_model.get("key")).startswith(
                str(PROMPT_VERSION) + ":"
            )
            and not args.refresh_model
        )
        if should_call_model and not (has_exact_model or has_reusable_model):
            pending_model.append(work)

    check_cancelled(args)
    print(
        "[ocr done] OCR 阶段完成：共 %d 张，需要模型复核 %d 张"
        % (len(image_paths), len(pending_model)),
        flush=True,
    )

    # Phase 2: send only problem images to the selected vision API. Failed
    # batches split recursively and ultimately fall back to one.
    review_model_batches(
        pending_model,
        args,
        cache,
        model_cache_key,
        counts,
    )
    check_cancelled(args)

    # Phase 3: merge every field after all OCR and model results are available.
    records = []
    for work in work_items:
        if work["record"] is not None:
            records.append(work["record"])
            continue
        model_error = clean(work.get("model_error"))
        if work.get("ocr_error"):
            model_error = "; ".join(
                filter(None, [model_error, "Umi-OCR: " + work["ocr_error"]])
            )
        record = merge_fields(
            work["image_path"],
            work["exif"],
            work["ocr_text"],
            work.get("model_result"),
            model_error,
            args,
        )
        work["model_error"] = model_error
        work["record"] = record
        item = work["item"]
        item["model_result"] = work.get("model_result")
        item["model_error"] = model_error
        item["record"] = dict(record)
        item["result_key"] = result_cache_key
        cache.save(work["relative"])
        if work["has_checkpoint"]:
            resumed_count += 1
        records.append(record)

    if resumed_count:
        print(
            "[%d/%d] 断点续跑完成：复用 %d 张，仅新处理 %d 张"
            % (
                len(image_paths),
                len(image_paths),
                resumed_count,
                len(image_paths) - resumed_count,
            ),
            flush=True,
        )

    check_cancelled(args)
    if not args.no_group_consistency:
        outliers = folder_coordinate_outliers(
            work_items,
            args.group_coordinate_threshold_meters,
            args.group_min_images,
        )
        folder_review_jobs = []
        pending_folder_model = []
        for folder_index, outlier in enumerate(outliers, start=1):
            work = work_items[outlier["index"]]
            print(
                "[folder %d/%d] 正在检查同目录坐标：%s"
                % (folder_index, len(outliers), work["relative"]),
                flush=True,
            )
            record = work["record"]
            source_text = clean(record.get(core.L_SOURCE))
            coordinates_from_exif = (
                "\u7ecf\u5ea6=EXIF" in source_text
                and "\u7eac\u5ea6=EXIF" in source_text
            )
            coordinates_from_model = (
                "\u7ecf\u5ea6=大模型 API" in source_text
                and "\u7eac\u5ea6=大模型 API" in source_text
            )
            if coordinates_from_exif or coordinates_from_model:
                continue

            distance_tip = (
                "folder coordinate outlier: %.0f m from direct-folder median; "
                "vision-API review triggered"
            ) % outlier["distance"]
            print(
                "[folder check] %s -> %.0f m outlier"
                % (work["relative"], outlier["distance"]),
                flush=True,
            )

            if args.llm_provider == "none":
                append_record_tip(record, distance_tip.replace(
                    "vision-API review triggered",
                    "vision API disabled; human review required",
                ))
                force_human_review(record)
                continue

            job = {
                "outlier": outlier,
                "work": work,
                "distance_tip": distance_tip,
                "prior_model_error": clean(work.get("model_error")),
            }
            folder_review_jobs.append(job)
            cached_model = work["item"].get("model")
            if isinstance(cached_model, dict) and cached_model.get("key") == model_cache_key:
                work["model_result"] = cached_model.get("result")
                work["model_error"] = clean(cached_model.get("error"))
            else:
                pending_folder_model.append(work)

        review_model_batches(
            pending_folder_model,
            args,
            cache,
            model_cache_key,
            counts,
        )
        for job in folder_review_jobs:
            outlier = job["outlier"]
            work = job["work"]
            model_result = work.get("model_result")
            model_error = clean(work.get("model_error"))
            combined_error = "; ".join(
                filter(None, [model_error, job["prior_model_error"]])
            )
            reviewed_record = merge_fields(
                work["image_path"],
                work["exif"],
                work["ocr_text"],
                model_result,
                combined_error,
                args,
            )
            append_record_tip(reviewed_record, job["distance_tip"])
            try:
                reviewed_distance = haversine_meters(
                    float(reviewed_record[core.L_LAT]),
                    float(reviewed_record[core.L_LON]),
                    outlier["median_lat"],
                    outlier["median_lon"],
                )
            except (TypeError, ValueError):
                reviewed_distance = float("inf")
            if reviewed_distance > args.group_coordinate_threshold_meters:
                append_record_tip(
                    reviewed_record,
                    "coordinate remains inconsistent with direct-folder median; human review required",
                )
                force_human_review(reviewed_record)
            work["record"] = reviewed_record
            records[outlier["index"]] = reviewed_record

    for index, work in enumerate(work_items, start=1):
        record = work["record"]
        if record[core.L_PARSE_STATUS] == core.STATUS_EXIF_COMPLETE:
            counts["exif"] += 1
        elif record[core.L_PARSE_STATUS] == "AUTO_OK":
            counts["auto"] += 1
        else:
            counts["review"] += 1
        if (
            len(work_items) <= 100
            or index == len(work_items)
            or index % progress_step == 0
        ):
            print(
                "[result] %s -> %s / %s"
                % (
                    work["relative"],
                    record[core.L_PARSE_STATUS],
                    record[core.L_REVIEW],
                ),
                flush=True,
            )

    check_cancelled(args)
    print("[workbook] 正在写入审核 XLSX……", flush=True)
    core.write_review_workbook(
        review_xlsx,
        records,
        args.photo_display_max_width,
        args.photo_display_max_height,
        image_mode=args.excel_image_mode,
        thumbnail_quality=args.thumbnail_quality,
        language=args.language,
    )
    cache.compact()
    print(
        "Done: EXIF complete=%d, auto=%d, needs review=%d, model calls=%d, "
        "OCR errors=%d, model errors=%d"
        % (
            counts["exif"],
            counts["auto"],
            counts["review"],
            counts["model"],
            counts["ocr_error"],
            counts["model_error"],
        )
    )
    print("Review workbook: %s" % review_xlsx)
    return 0


def write_review(args):
    # Delegate to the proven writer while keeping a single user-facing CLI.
    return core.write_exif_from_review(args)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    args.command = args.command.lower()
    args.image_root = os.path.abspath(args.image_root)
    args.review_xlsx = os.path.abspath(args.review_xlsx)
    if args.command == "review":
        args.llm_api_key = clean(args.llm_api_key) or clean(
            os.environ.get("PHOTO_METADATA_API_KEY")
        )
        if args.llm_provider == "openai":
            if not clean(args.llm_endpoint):
                raise ValueError("大模型 API 地址不能为空")
            if not clean(args.llm_model):
                raise ValueError("大模型名称不能为空")
            if not args.llm_api_key:
                raise ValueError("大模型 API Key 不能为空")
        try:
            return create_review(args)
        except TaskCancelled as exc:
            print("[cancelled] %s" % exc, flush=True)
            return 130
    args.output_dir = os.path.abspath(args.output_dir)
    return write_review(args)


if __name__ == "__main__":
    sys.exit(main())
