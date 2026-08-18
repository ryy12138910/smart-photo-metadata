#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Desktop interface for the photo-watermark metadata workflow."""

from __future__ import print_function

import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


PROJECT_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
PIPELINE_SCRIPT = Path(__file__).resolve().parent / "photo_pipeline.py"
PIPELINE_EXE = PROJECT_DIR / "PhotoMetadataWorker.exe"
DEFAULT_DATA_DIR = PROJECT_DIR / "data"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "outputs"
DEFAULT_REVIEW_XLSX = DEFAULT_OUTPUT_DIR / "metadata_review.xlsx"
DEFAULT_PHOTO_OUTPUT = DEFAULT_OUTPUT_DIR / "photos_with_exif"
DEFAULT_API_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_API_MODEL = "gpt-4o-mini"

API_PROVIDER_PRESETS = {
    "openai": {
        "endpoint": DEFAULT_API_ENDPOINT,
        "model": DEFAULT_API_MODEL,
    },
    "qwen": {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen3-vl-plus",
    },
    "custom": {
        "endpoint": "",
        "model": "",
    },
}

ZH_MODEL_MODE_LABELS = {
    "纯 OCR（本地处理）": ("none", "suspicious"),
    "OCR + 大模型 API": ("openai", "suspicious"),
}
ZH_API_PROVIDER_LABELS = {
    "OpenAI": "openai",
    "阿里云百炼（千问）": "qwen",
    "自定义兼容服务": "custom",
}
ZH_IMAGE_MODE_LABELS = {
    "不嵌入图片，仅保留链接（最快）": "none",
    "压缩缩略图": "thumbnail",
    "嵌入原图（文件会很大）": "original",
}

TRANSLATIONS = {
    "图片经纬度与拍摄日期补全工具": "Photo Metadata Tool",
    "照片水印信息整理": "Photo Watermark Metadata",
    "批量提取经纬度与拍摄时间，审核后写入新照片。": "Extract coordinates and capture times in batches, review them, then write them to new photos.",
    "中文": "中文",
    "语言": "Language",
    "1. 识别与审核": "1. Extract & Review",
    "2. 写入新照片": "2. Write New Photos",
    "运行状态与日志": "Status & Log",
    "清空日志": "Clear Log",
    "停止当前任务": "Stop Task",
    "输入与输出": "Input & Output",
    "照片根目录": "Photo Root Folder",
    "选择照片文件夹": "Choose Photo Folder",
    "审核表保存位置": "Review Workbook",
    "选择保存位置": "Choose Save Location",
    "处理选项": "Processing Options",
    "识别方式": "Recognition Mode",
    "纯 OCR（本地处理）": "OCR Only (Local)",
    "OCR + 大模型 API": "OCR + LLM API",
    "API 服务商": "API Provider",
    "阿里云百炼（千问）": "Alibaba Cloud Model Studio (Qwen)",
    "自定义兼容服务": "Custom Compatible Service",
    "API 地址": "API Endpoint",
    "模型名称": "Model Name",
    "API 复核范围": "API Review Scope",
    "仅复核异常结果": "Review Exceptions Only",
    "复核所有缺少属性的图片": "Review All Photos with Missing Metadata",
    "审核表图片": "Workbook Images",
    "不嵌入图片，仅保留链接（最快）": "Links Only (Fastest)",
    "压缩缩略图": "Compressed Thumbnails",
    "嵌入原图（文件会很大）": "Embed Originals (Large File)",
    "路径筛选": "Path Filter",
    "处理数量上限": "Photo Limit",
    "同目录离群阈值（米）": "Folder Outlier Threshold (m)",
    "开始生成审核表": "Create Review Workbook",
    "打开审核表": "Open Workbook",
    "打开所在文件夹": "Open Folder",
    "原始照片根目录": "Original Photo Folder",
    "审核后的 XLSX": "Reviewed XLSX",
    "选择审核表": "Choose Review Workbook",
    "新图片输出目录": "New Photo Output Folder",
    "选择输出文件夹": "Choose Output Folder",
    "写入选项": "Write Options",
    "仅演练，不生成图片（第一次运行推荐）": "Dry run only; do not create photos (recommended first)",
    "允许审核表覆盖原图已有 EXIF 字段（谨慎使用）": "Allow reviewed values to replace existing EXIF fields (use with care)",
    "默认只处理“通过/自动通过”行，并只补充原图缺失的 GPS 或拍摄时间。源照片不会被修改，新图片保存在输出目录。": "By default, only approved rows are processed and only missing GPS or capture time is added. Source photos are unchanged; new files are saved to the output folder.",
    "开始检查 / 生成图片": "Check / Create Photos",
    "打开结果报告": "Open Result Report",
    "打开输出目录": "Open Output Folder",
    "选择文件夹": "Choose Folder",
    "保存审核表": "Save Review Workbook",
    "Excel 工作簿": "Excel Workbook",
    "选择审核后的 XLSX": "Choose Reviewed XLSX",
    "所有文件": "All Files",
    "准备就绪": "Ready",
    "尚未开始任务": "No task started",
    "参数有误": "Invalid Input",
    "请选择照片根目录。": "Choose a photo root folder.",
    "请选择审核表保存位置。": "Choose where to save the review workbook.",
    "照片根目录不存在。": "The photo root folder does not exist.",
    "审核表保存位置必须是 .xlsx 文件。": "The review workbook must be an .xlsx file.",
    "最多处理图片数": "Photo limit",
    "同目录离群阈值": "Folder outlier threshold",
    "请输入大模型 API 地址。": "Enter the LLM API endpoint.",
    "请输入模型名称。": "Enter the model name.",
    "请输入 API Key。": "Enter the API key.",
    "请选择原始照片目录、审核后的 XLSX 和输出目录。": "Choose the original photo folder, reviewed XLSX, and output folder.",
    "原始照片根目录不存在。": "The original photo folder does not exist.",
    "找不到审核后的 XLSX 文件。": "The reviewed XLSX file was not found.",
    "输出目录不安全": "Unsafe Output Folder",
    "输出目录不能与原始照片根目录相同。": "The output folder cannot be the same as the original photo folder.",
    "确认覆盖已有 EXIF": "Confirm EXIF Replacement",
    "你已启用“覆盖原图已有 EXIF 字段”。\n虽然程序仍会输出到新目录，但审核表的值将优先于原图现有属性。\n\n确认继续吗？": "You enabled replacement of existing EXIF fields.\nFiles will still be written to a new folder, but reviewed values will take priority over existing metadata.\n\nContinue?",
    "程序不完整": "Incomplete Package",
    "找不到后台处理程序。": "The background worker was not found.",
    "正在运行……": "Running...",
    "等待处理进度": "Waiting for progress",
    "正在进行同目录坐标复核……": "Checking folder coordinate consistency...",
    "已恢复上次断点，正在继续……": "Resumed from the previous checkpoint...",
    "正在批量进行 OCR……": "Running batch OCR...",
    "正在批量进行模型复核……": "Running model review...",
    "正在生成审核表……": "Creating review workbook...",
    "正在写入审核 XLSX……": "Writing review XLSX...",
    "正在保存文件": "Saving file",
    "启动失败：": "Failed to start: ",
    "任务已停止": "Task stopped",
    "断点已保存，下次可继续": "Checkpoint saved; you can resume later",
    "任务已停止；再次使用相同照片目录和审核表路径即可续跑。": "Task stopped. Use the same photo folder and workbook path to resume.",
    "任务已完成": "Task complete",
    "成功": "Success",
    "审核表已生成。": "The review workbook was created.",
    "演练报告已生成。": "The dry-run report was created.",
    "新图片已生成。": "New photos were created.",
    "任务完成": "Task Complete",
    "是否打开结果位置？": "Open the result location?",
    "任务失败": "Task Failed",
    "处理没有完成，请查看下方日志中的最后几行。": "Processing did not finish. Check the last lines in the log below.",
    "停止任务": "Stop Task",
    "确认停止当前任务吗？已写入缓存的 OCR/模型结果仍会保留。": "Stop the current task? Cached OCR and model results will be kept.",
    "正在安全停止：先保存当前图片断点，再结束任务……": "Stopping safely: saving the current checkpoint first...",
    "正在保存断点并停止……": "Saving checkpoint and stopping...",
    "停止失败": "Failed to Stop",
    "安全停止等待超时，已结束后台进程；此前断点仍会保留。": "Safe stop timed out. The worker was terminated and the previous checkpoint was kept.",
    "无法打开": "Cannot Open",
    "任务正在运行": "Task Running",
    "关闭窗口会停止当前任务，确认关闭吗？": "Closing the window will stop the current task. Close anyway?",
}


def worker_command_prefix():
    """Return the bundled worker or the source-mode Python command."""
    if getattr(sys, "frozen", False):
        return [str(PIPELINE_EXE)]
    return [sys.executable, str(PIPELINE_SCRIPT)]


def build_review_command(
    command_prefix,
    image_root,
    review_xlsx,
    *,
    model_provider="none",
    model_review_mode="suspicious",
    model_name=DEFAULT_API_MODEL,
    api_endpoint=DEFAULT_API_ENDPOINT,
    image_mode="thumbnail",
    path_contains="",
    max_images=0,
    group_threshold_meters=500.0,
    cancel_file="",
    language="zh",
):
    prefix = (
        list(command_prefix)
        if isinstance(command_prefix, (list, tuple))
        else [str(command_prefix), str(PIPELINE_SCRIPT)]
    )
    command = prefix + [
        "review",
        "--image-root",
        str(image_root),
        "--review-xlsx",
        str(review_xlsx),
        "--llm-provider",
        model_provider,
        "--llm-review-mode",
        model_review_mode,
        "--excel-image-mode",
        image_mode,
        "--group-coordinate-threshold-meters",
        str(float(group_threshold_meters)),
        "--language",
        language,
    ]
    if model_provider == "openai":
        command.extend(["--llm-model", model_name.strip()])
        command.extend(["--llm-endpoint", api_endpoint.strip()])
        command.extend(["--llm-batch-size", "1"])
    if path_contains.strip():
        command.extend(["--path-contains", path_contains.strip()])
    if int(max_images) > 0:
        command.extend(["--max-images", str(int(max_images))])
    if str(cancel_file).strip():
        command.extend(["--cancel-file", str(cancel_file)])
    return command


def build_write_command(
    command_prefix,
    image_root,
    review_xlsx,
    output_dir,
    *,
    dry_run=False,
    overwrite_existing_exif=False,
):
    prefix = (
        list(command_prefix)
        if isinstance(command_prefix, (list, tuple))
        else [str(command_prefix), str(PIPELINE_SCRIPT)]
    )
    command = prefix + [
        "write",
        "--image-root",
        str(image_root),
        "--review-xlsx",
        str(review_xlsx),
        "--output-dir",
        str(output_dir),
    ]
    if dry_run:
        command.append("--dry-run")
    if overwrite_existing_exif:
        command.append("--overwrite-existing-exif")
    return command


def parse_progress_line(text):
    match = re.search(r"\[(?:(folder|resume|ocr|model)\s+)?(\d+)/(\d+)\]", text)
    if not match:
        return None
    current = int(match.group(2))
    total = int(match.group(3))
    if total <= 0:
        return None
    return match.group(1) or "images", current, total


def open_local_path(path):
    normalized = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.exists(normalized):
        raise FileNotFoundError(normalized)
    if os.name == "nt":
        os.startfile(normalized)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", normalized])
    else:
        subprocess.Popen(["xdg-open", normalized])


class PhotoMetadataApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.geometry("1080x780")
        self.minsize(920, 680)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))

        self._process = None
        self._task_running = False
        self._cancel_requested = False
        self._task_kind = ""
        self._task_output = ""
        self._task_dry_run = False
        self._task_api_key = ""
        self._cancel_file = ""
        self._event_queue = queue.Queue()
        self._running_buttons = []
        self.language_code = "zh"

        self._configure_style()
        self._create_variables()
        self._build_layout()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self):
        style = ttk.Style(self)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Subtitle.TLabel", foreground="#52606D")
        style.configure("Section.TLabelframe", padding=14)
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(18, 8))
        style.configure("Danger.TButton", foreground="#A61B1B", padding=(14, 7))

    def _(self, text):
        return TRANSLATIONS.get(text, text) if self.language_code == "en" else text

    def _model_labels(self):
        return {self._(label): value for label, value in ZH_MODEL_MODE_LABELS.items()}

    def _api_provider_labels(self):
        return {
            self._(label): value for label, value in ZH_API_PROVIDER_LABELS.items()
        }

    def _image_labels(self):
        return {self._(label): value for label, value in ZH_IMAGE_MODE_LABELS.items()}

    def _translate_choice(self, current, source):
        for zh_label, value in source.items():
            if current in (zh_label, TRANSLATIONS.get(zh_label)):
                return self._(zh_label), value
        first = next(iter(source))
        return self._(first), source[first]

    def _create_variables(self):
        self.language = tk.StringVar(value="中文")
        self.review_image_root = tk.StringVar(value=str(DEFAULT_DATA_DIR))
        self.review_xlsx = tk.StringVar(value=str(DEFAULT_REVIEW_XLSX))
        self.model_mode = tk.StringVar(value=next(iter(ZH_MODEL_MODE_LABELS)))
        self.api_provider = tk.StringVar(value=next(iter(ZH_API_PROVIDER_LABELS)))
        self.model_name = tk.StringVar(value=DEFAULT_API_MODEL)
        self.api_endpoint = tk.StringVar(value=DEFAULT_API_ENDPOINT)
        self.api_key = tk.StringVar(value="")
        self.api_review_scope = tk.StringVar(value="仅复核异常结果")
        self.excel_image_mode = tk.StringVar(value=next(iter(ZH_IMAGE_MODE_LABELS)))
        self.path_contains = tk.StringVar(value="")
        self.max_images = tk.StringVar(value="0")
        self.group_threshold = tk.StringVar(value="500")

        self.write_image_root = tk.StringVar(value=str(DEFAULT_DATA_DIR))
        self.write_xlsx = tk.StringVar(value=str(DEFAULT_REVIEW_XLSX))
        self.write_output_dir = tk.StringVar(value=str(DEFAULT_PHOTO_OUTPUT))
        self.dry_run = tk.BooleanVar(value=True)
        self.overwrite_exif = tk.BooleanVar(value=False)

        self.status_text = tk.StringVar(value="准备就绪")
        self.progress_text = tk.StringVar(value="尚未开始任务")

    def _build_layout(self):
        self.title(self._("图片经纬度与拍摄日期补全工具"))
        container = ttk.Frame(self, padding=(22, 18, 22, 16))
        self._layout_root = container
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 14))
        language_row = ttk.Frame(header)
        language_row.pack(side="right", anchor="ne")
        ttk.Label(language_row, text=self._("语言")).pack(side="left", padx=(0, 8))
        self.language_combo = ttk.Combobox(
            language_row,
            textvariable=self.language,
            values=["中文", "English"],
            state="readonly",
            width=10,
        )
        self.language_combo.pack(side="left")
        self.language_combo.bind("<<ComboboxSelected>>", self._change_language)
        ttk.Label(header, text=self._("照片水印信息整理"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=self._("批量提取经纬度与拍摄时间，审核后写入新照片。"),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)
        review_tab = ttk.Frame(self.notebook, padding=16)
        write_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(review_tab, text="  " + self._("1. 识别与审核") + "  ")
        self.notebook.add(write_tab, text="  " + self._("2. 写入新照片") + "  ")
        self._build_review_tab(review_tab)
        self._build_write_tab(write_tab)

        activity = ttk.LabelFrame(
            container,
            text=self._("运行状态与日志"),
            style="Section.TLabelframe",
        )
        activity.pack(fill="both", expand=False, pady=(14, 0))

        status_row = ttk.Frame(activity)
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status_text).pack(side="left")
        ttk.Label(
            status_row,
            textvariable=self.progress_text,
            foreground="#52606D",
        ).pack(side="right")

        self.progress = ttk.Progressbar(activity, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 9))

        self.log = ScrolledText(
            activity,
            height=9,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log.pack(fill="both", expand=True)

        controls = ttk.Frame(activity)
        controls.pack(fill="x", pady=(9, 0))
        ttk.Button(controls, text=self._("清空日志"), command=self._clear_log).pack(side="left")
        self.stop_button = ttk.Button(
            controls,
            text=self._("停止当前任务"),
            style="Danger.TButton",
            command=self._stop_task,
            state="disabled",
        )
        self.stop_button.pack(side="right")

    def _change_language(self, _event=None):
        if self._task_running:
            self.language.set("English" if self.language_code == "en" else "中文")
            return
        _model_label, model_value = self._translate_choice(
            self.model_mode.get(), ZH_MODEL_MODE_LABELS
        )
        _image_label, image_value = self._translate_choice(
            self.excel_image_mode.get(), ZH_IMAGE_MODE_LABELS
        )
        _api_provider_label, api_provider_value = self._translate_choice(
            self.api_provider.get(), ZH_API_PROVIDER_LABELS
        )
        current_scope_all = self.api_review_scope.get() in (
            "复核所有缺少属性的图片",
            TRANSLATIONS["复核所有缺少属性的图片"],
        )
        selected_tab = self.notebook.index(self.notebook.select())
        log_text = self.log.get("1.0", "end-1c")
        self.language_code = "en" if self.language.get() == "English" else "zh"
        self.model_mode.set(
            next(label for label, value in self._model_labels().items() if value == model_value)
        )
        self.excel_image_mode.set(
            next(label for label, value in self._image_labels().items() if value == image_value)
        )
        self.api_provider.set(
            next(
                label
                for label, value in self._api_provider_labels().items()
                if value == api_provider_value
            )
        )
        self.api_review_scope.set(
            self._("复核所有缺少属性的图片")
            if current_scope_all
            else self._("仅复核异常结果")
        )
        self.status_text.set(self._("准备就绪"))
        self.progress_text.set(self._("尚未开始任务"))
        self._layout_root.destroy()
        self._running_buttons = []
        self._build_layout()
        self.notebook.select(selected_tab)
        if log_text:
            self._append_log(log_text)

    def _build_review_tab(self, parent):
        paths = ttk.LabelFrame(parent, text=self._("输入与输出"), style="Section.TLabelframe")
        paths.pack(fill="x")
        self._path_row(
            paths,
            0,
            self._("照片根目录"),
            self.review_image_root,
            lambda: self._choose_directory(self.review_image_root),
            self._("选择照片文件夹"),
        )
        self._path_row(
            paths,
            1,
            self._("审核表保存位置"),
            self.review_xlsx,
            lambda: self._choose_save_xlsx(self.review_xlsx),
            self._("选择保存位置"),
        )

        options = ttk.LabelFrame(parent, text=self._("处理选项"), style="Section.TLabelframe")
        options.pack(fill="x", pady=(12, 0))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text=self._("识别方式")).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        model_combo = ttk.Combobox(
            options,
            textvariable=self.model_mode,
            values=list(self._model_labels()),
            state="readonly",
        )
        model_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=5)
        model_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_model_state())

        ttk.Label(options, text=self._("API 服务商")).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        self.api_provider_combo = ttk.Combobox(
            options,
            textvariable=self.api_provider,
            values=list(self._api_provider_labels()),
            state="readonly",
        )
        self.api_provider_combo.grid(row=1, column=1, columnspan=3, sticky="ew", pady=5)
        self.api_provider_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._sync_api_provider(reset_model=True),
        )

        ttk.Label(options, text=self._("模型名称")).grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        self.model_entry = ttk.Entry(options, textvariable=self.model_name)
        self.model_entry.grid(row=2, column=1, sticky="ew", pady=5)

        ttk.Label(options, text="API Key").grid(row=2, column=2, sticky="w", padx=(18, 10), pady=5)
        self.api_key_entry = ttk.Entry(options, textvariable=self.api_key, show="●")
        self.api_key_entry.grid(row=2, column=3, sticky="ew", pady=5)

        ttk.Label(options, text=self._("API 地址")).grid(row=3, column=0, sticky="w", padx=(0, 10), pady=5)
        self.api_endpoint_entry = ttk.Entry(options, textvariable=self.api_endpoint)
        self.api_endpoint_entry.grid(row=3, column=1, columnspan=3, sticky="ew", pady=5)

        ttk.Label(options, text=self._("API 复核范围")).grid(row=4, column=0, sticky="w", padx=(0, 10), pady=5)
        self.api_scope_combo = ttk.Combobox(
            options,
            textvariable=self.api_review_scope,
            values=[self._("仅复核异常结果"), self._("复核所有缺少属性的图片")],
            state="readonly",
        )
        self.api_scope_combo.grid(row=4, column=1, sticky="ew", pady=5)

        ttk.Label(options, text=self._("审核表图片")).grid(row=4, column=2, sticky="w", padx=(18, 10), pady=5)
        ttk.Combobox(
            options,
            textvariable=self.excel_image_mode,
            values=list(self._image_labels()),
            state="readonly",
        ).grid(row=4, column=3, sticky="ew", pady=5)

        ttk.Label(options, text=self._("路径筛选")).grid(row=5, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(options, textvariable=self.path_contains).grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(options, text=self._("处理数量上限")).grid(row=5, column=2, sticky="w", padx=(18, 10), pady=5)
        ttk.Entry(options, textvariable=self.max_images, width=12).grid(row=5, column=3, sticky="ew", pady=5)

        ttk.Label(options, text=self._("同目录离群阈值（米）")).grid(
            row=6, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(options, textvariable=self.group_threshold).grid(
            row=6, column=1, sticky="ew", pady=5
        )
        self._sync_model_state()

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(14, 0))
        self.review_run_button = ttk.Button(
            actions,
            text=self._("开始生成审核表"),
            style="Primary.TButton",
            command=self._start_review,
        )
        self.review_run_button.pack(side="left")
        self._running_buttons.append(self.review_run_button)
        ttk.Button(
            actions,
            text=self._("打开审核表"),
            command=lambda: self._open_path(self.review_xlsx.get()),
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            actions,
            text=self._("打开所在文件夹"),
            command=lambda: self._open_parent(self.review_xlsx.get()),
        ).pack(side="left", padx=(8, 0))

    def _build_write_tab(self, parent):
        paths = ttk.LabelFrame(parent, text=self._("输入与输出"), style="Section.TLabelframe")
        paths.pack(fill="x")
        self._path_row(
            paths,
            0,
            self._("原始照片根目录"),
            self.write_image_root,
            lambda: self._choose_directory(self.write_image_root),
            self._("选择照片文件夹"),
        )
        self._path_row(
            paths,
            1,
            self._("审核后的 XLSX"),
            self.write_xlsx,
            lambda: self._choose_open_xlsx(self.write_xlsx),
            self._("选择审核表"),
        )
        self._path_row(
            paths,
            2,
            self._("新图片输出目录"),
            self.write_output_dir,
            lambda: self._choose_directory(self.write_output_dir, allow_new=True),
            self._("选择输出文件夹"),
        )

        options = ttk.LabelFrame(parent, text=self._("写入选项"), style="Section.TLabelframe")
        options.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(
            options,
            text=self._("仅演练，不生成图片（第一次运行推荐）"),
            variable=self.dry_run,
        ).pack(anchor="w", pady=4)
        ttk.Checkbutton(
            options,
            text=self._("允许审核表覆盖原图已有 EXIF 字段（谨慎使用）"),
            variable=self.overwrite_exif,
        ).pack(anchor="w", pady=4)
        ttk.Label(
            options,
            text=self._(
                "默认只处理“通过/自动通过”行，并只补充原图缺失的 GPS 或拍摄时间。"
                "源照片不会被修改，新图片保存在输出目录。"
            ),
            foreground="#52606D",
            wraplength=900,
        ).pack(anchor="w", pady=(8, 0))

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(14, 0))
        self.write_run_button = ttk.Button(
            actions,
            text=self._("开始检查 / 生成图片"),
            style="Primary.TButton",
            command=self._start_write,
        )
        self.write_run_button.pack(side="left")
        self._running_buttons.append(self.write_run_button)
        ttk.Button(
            actions,
            text=self._("打开结果报告"),
            command=lambda: self._open_path(
                str(Path(self.write_output_dir.get()) / "write_result_report.xlsx")
            ),
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            actions,
            text=self._("打开输出目录"),
            command=lambda: self._open_path(self.write_output_dir.get()),
        ).pack(side="left", padx=(8, 0))

    def _path_row(self, parent, row, label, variable, command, button_text):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row,
            column=0,
            sticky="w",
            padx=(0, 12),
            pady=6,
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row,
            column=1,
            sticky="ew",
            pady=6,
        )
        ttk.Button(parent, text=button_text, command=command).grid(
            row=row,
            column=2,
            padx=(10, 0),
            pady=6,
        )

    def _sync_model_state(self):
        provider, _mode = self._model_labels()[self.model_mode.get()]
        api_enabled = provider == "openai"
        self.api_provider_combo.configure(
            state="readonly" if api_enabled else "disabled"
        )
        self.model_entry.configure(state="normal" if api_enabled else "disabled")
        self.api_key_entry.configure(state="normal" if api_enabled else "disabled")
        self.api_scope_combo.configure(
            state="readonly" if api_enabled else "disabled"
        )
        self._sync_api_provider(reset_model=False)

    def _sync_api_provider(self, reset_model=False):
        model_provider, _mode = self._model_labels()[self.model_mode.get()]
        api_enabled = model_provider == "openai"
        preset_name = self._api_provider_labels().get(
            self.api_provider.get(), "openai"
        )
        preset = API_PROVIDER_PRESETS[preset_name]

        if api_enabled and preset_name != "custom":
            self.api_endpoint.set(preset["endpoint"])
        elif api_enabled and reset_model:
            known_endpoints = {
                item["endpoint"]
                for item in API_PROVIDER_PRESETS.values()
                if item["endpoint"]
            }
            if self.api_endpoint.get().strip() in known_endpoints:
                self.api_endpoint.set("")

        if api_enabled and (reset_model or not self.model_name.get().strip()):
            self.model_name.set(preset["model"])

        if not api_enabled:
            endpoint_state = "disabled"
        elif preset_name == "custom":
            endpoint_state = "normal"
        else:
            endpoint_state = "readonly"
        self.api_endpoint_entry.configure(state=endpoint_state)

    def _choose_directory(self, variable, allow_new=False):
        current = variable.get().strip()
        initial = current if os.path.isdir(current) else str(PROJECT_DIR)
        selected = filedialog.askdirectory(
            title=self._("选择文件夹"),
            initialdir=initial,
            mustexist=not allow_new,
        )
        if selected:
            variable.set(os.path.normpath(selected))

    def _choose_save_xlsx(self, variable):
        current = Path(variable.get().strip() or DEFAULT_REVIEW_XLSX)
        selected = filedialog.asksaveasfilename(
            title=self._("保存审核表"),
            initialdir=str(current.parent),
            initialfile=current.name,
            defaultextension=".xlsx",
            filetypes=[(self._("Excel 工作簿"), "*.xlsx")],
        )
        if selected:
            variable.set(os.path.normpath(selected))
            self.write_xlsx.set(os.path.normpath(selected))

    def _choose_open_xlsx(self, variable):
        current = Path(variable.get().strip() or DEFAULT_REVIEW_XLSX)
        selected = filedialog.askopenfilename(
            title=self._("选择审核后的 XLSX"),
            initialdir=str(current.parent if current.parent.exists() else PROJECT_DIR),
            filetypes=[(self._("Excel 工作簿"), "*.xlsx"), (self._("所有文件"), "*.*")],
        )
        if selected:
            variable.set(os.path.normpath(selected))

    def _validated_number(self, value, label, minimum=0.0, integer=False):
        try:
            parsed = int(value) if integer else float(value)
        except (TypeError, ValueError):
            if self.language_code == "en":
                raise ValueError("%s must be a number." % label)
            raise ValueError("%s必须是数字。" % label)
        if parsed < minimum:
            if self.language_code == "en":
                raise ValueError("%s cannot be less than %s." % (label, minimum))
            raise ValueError("%s不能小于 %s。" % (label, minimum))
        return parsed

    def _start_review(self):
        if self._task_running:
            return
        try:
            image_root_text = self.review_image_root.get().strip()
            review_xlsx_text = self.review_xlsx.get().strip()
            if not image_root_text:
                raise ValueError(self._("请选择照片根目录。"))
            if not review_xlsx_text:
                raise ValueError(self._("请选择审核表保存位置。"))
            image_root = os.path.abspath(image_root_text)
            review_xlsx = os.path.abspath(review_xlsx_text)
            if not os.path.isdir(image_root):
                raise ValueError(self._("照片根目录不存在。"))
            if not review_xlsx.lower().endswith(".xlsx"):
                raise ValueError(self._("审核表保存位置必须是 .xlsx 文件。"))
            max_images = self._validated_number(
                self.max_images.get().strip() or "0",
                self._("最多处理图片数"),
                minimum=0,
                integer=True,
            )
            threshold = self._validated_number(
                self.group_threshold.get().strip() or "500",
                self._("同目录离群阈值"),
                minimum=0,
            )
            provider, review_mode = self._model_labels()[self.model_mode.get()]
            if provider == "openai":
                review_mode = (
                    "all"
                    if self.api_review_scope.get() == self._("复核所有缺少属性的图片")
                    else "suspicious"
                )
                if not self.api_endpoint.get().strip():
                    raise ValueError(self._("请输入大模型 API 地址。"))
                if not self.model_name.get().strip():
                    raise ValueError(self._("请输入模型名称。"))
                if not self.api_key.get().strip():
                    raise ValueError(self._("请输入 API Key。"))
            image_mode = self._image_labels()[self.excel_image_mode.get()]
        except (KeyError, ValueError) as exc:
            messagebox.showerror(self._("参数有误"), str(exc), parent=self)
            return

        command = build_review_command(
            worker_command_prefix(),
            image_root,
            review_xlsx,
            model_provider=provider,
            model_review_mode=review_mode,
            model_name=self.model_name.get(),
            api_endpoint=self.api_endpoint.get(),
            image_mode=image_mode,
            path_contains=self.path_contains.get(),
            max_images=max_images,
            group_threshold_meters=threshold,
            cancel_file=review_xlsx + ".cancel",
            language=self.language_code,
        )
        self.write_image_root.set(image_root)
        self.write_xlsx.set(review_xlsx)
        self._run_command(
            "review",
            command,
            review_xlsx,
            cancel_file=review_xlsx + ".cancel",
            api_key=self.api_key.get() if provider == "openai" else "",
        )

    def _start_write(self):
        if self._task_running:
            return
        image_root_text = self.write_image_root.get().strip()
        review_xlsx_text = self.write_xlsx.get().strip()
        output_dir_text = self.write_output_dir.get().strip()
        if not image_root_text or not review_xlsx_text or not output_dir_text:
            messagebox.showerror(
                self._("参数有误"),
                self._("请选择原始照片目录、审核后的 XLSX 和输出目录。"),
                parent=self,
            )
            return
        image_root = os.path.abspath(image_root_text)
        review_xlsx = os.path.abspath(review_xlsx_text)
        output_dir = os.path.abspath(output_dir_text)
        if not os.path.isdir(image_root):
            messagebox.showerror(self._("参数有误"), self._("原始照片根目录不存在。"), parent=self)
            return
        if not os.path.isfile(review_xlsx):
            messagebox.showerror(self._("参数有误"), self._("找不到审核后的 XLSX 文件。"), parent=self)
            return
        if os.path.normcase(image_root) == os.path.normcase(output_dir):
            messagebox.showerror(
                self._("输出目录不安全"),
                self._("输出目录不能与原始照片根目录相同。"),
                parent=self,
            )
            return
        if self.overwrite_exif.get():
            confirmed = messagebox.askyesno(
                self._("确认覆盖已有 EXIF"),
                self._(
                    "你已启用“覆盖原图已有 EXIF 字段”。\n"
                    "虽然程序仍会输出到新目录，但审核表的值将优先于原图现有属性。\n\n"
                    "确认继续吗？"
                ),
                parent=self,
            )
            if not confirmed:
                return

        command = build_write_command(
            worker_command_prefix(),
            image_root,
            review_xlsx,
            output_dir,
            dry_run=self.dry_run.get(),
            overwrite_existing_exif=self.overwrite_exif.get(),
        )
        self._run_command(
            "write",
            command,
            output_dir,
            dry_run=self.dry_run.get(),
        )

    def _run_command(
        self,
        task_kind,
        command,
        task_output,
        dry_run=False,
        cancel_file="",
        api_key="",
    ):
        worker_available = PIPELINE_EXE.is_file() if getattr(sys, "frozen", False) else PIPELINE_SCRIPT.is_file()
        if not worker_available:
            messagebox.showerror(self._("程序不完整"), self._("找不到后台处理程序。"), parent=self)
            return
        Path(task_output).parent.mkdir(parents=True, exist_ok=True)
        self._task_kind = task_kind
        self._task_output = task_output
        self._task_dry_run = bool(dry_run)
        self._task_api_key = str(api_key or "")
        self._cancel_file = str(cancel_file or "")
        if self._cancel_file:
            try:
                os.remove(self._cancel_file)
            except FileNotFoundError:
                pass
        self._task_running = True
        self._cancel_requested = False
        self.status_text.set(self._("正在运行……"))
        self.progress_text.set(self._("等待处理进度"))
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.stop_button.configure(state="normal")
        self.language_combo.configure(state="disabled")
        for button in self._running_buttons:
            button.configure(state="disabled")
        self._append_log("\n> " + subprocess.list2cmdline(command) + "\n")

        worker = threading.Thread(
            target=self._process_worker,
            args=(command,),
            daemon=True,
        )
        worker.start()

    def _process_worker(self, command):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        if self._task_api_key:
            environment["PHOTO_METADATA_API_KEY"] = self._task_api_key
        try:
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                creationflags=creationflags,
            )
            self._process = process
            if self._cancel_requested:
                if self._cancel_file:
                    self._write_cancel_signal()
                else:
                    process.terminate()
            if process.stdout is not None:
                for line in process.stdout:
                    self._event_queue.put(("line", line.rstrip("\r\n")))
            return_code = process.wait()
            self._event_queue.put(("done", return_code))
        except Exception as exc:
            self._event_queue.put(("error", str(exc)))

    def _poll_events(self):
        try:
            while True:
                event, value = self._event_queue.get_nowait()
                if event == "line":
                    self._append_log(value)
                    progress_info = parse_progress_line(value)
                    if progress_info:
                        phase, current, total = progress_info
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=total)
                        self.progress["value"] = current
                        if phase == "folder":
                            self.status_text.set(self._("正在进行同目录坐标复核……"))
                            self.progress_text.set(
                                ("Folder review" if self.language_code == "en" else "目录复核")
                                + " %d / %d" % (current, total)
                            )
                        elif phase == "resume":
                            self.status_text.set(self._("已恢复上次断点，正在继续……"))
                            self.progress_text.set(
                                ("Resume at photo %d / %d" if self.language_code == "en" else "从图片 %d / %d 继续")
                                % (current, total)
                            )
                        elif phase == "ocr":
                            self.status_text.set(self._("正在批量进行 OCR……"))
                            self.progress_text.set(
                                "OCR %d / %d" % (current, total)
                            )
                        elif phase == "model":
                            self.status_text.set(self._("正在批量进行模型复核……"))
                            self.progress_text.set(
                                ("Model review" if self.language_code == "en" else "模型复核")
                                + " %d / %d" % (current, total)
                            )
                        else:
                            self.status_text.set(self._("正在生成审核表……"))
                            self.progress_text.set(
                                ("Photos" if self.language_code == "en" else "图片")
                                + " %d / %d" % (current, total)
                            )
                    elif value.startswith("[workbook]"):
                        self.status_text.set(self._("正在写入审核 XLSX……"))
                        self.progress_text.set(self._("正在保存文件"))
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                elif event == "done":
                    self._finish_task(int(value))
                elif event == "error":
                    self._append_log(self._("启动失败：") + value)
                    self._finish_task(-1)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _finish_task(self, return_code):
        was_cancelled = self._cancel_requested or return_code == 130
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress["value"] = 100 if return_code == 0 else self.progress["value"]
        self.stop_button.configure(state="disabled")
        self.language_combo.configure(state="readonly")
        for button in self._running_buttons:
            button.configure(state="normal")
        self._process = None
        self._task_running = False
        self._task_api_key = ""
        self._cancel_requested = False
        if self._cancel_file:
            try:
                os.remove(self._cancel_file)
            except OSError:
                pass
        self._cancel_file = ""

        if was_cancelled:
            self.status_text.set(self._("任务已停止"))
            self.progress_text.set(self._("断点已保存，下次可继续"))
            self._append_log(self._("任务已停止；再次使用相同照片目录和审核表路径即可续跑。"))
        elif return_code == 0:
            self.status_text.set(self._("任务已完成"))
            self.progress_text.set(self._("成功"))
            message = self._("审核表已生成。") if self._task_kind == "review" else (
                self._("演练报告已生成。") if self._task_dry_run else self._("新图片已生成。")
            )
            if messagebox.askyesno(
                self._("任务完成"),
                message + "\n\n" + self._("是否打开结果位置？"),
                parent=self,
            ):
                if self._task_kind == "review":
                    self._open_path(self._task_output)
                else:
                    self._open_path(self._task_output)
        else:
            self.status_text.set(self._("任务失败"))
            self.progress_text.set(
                ("Exit code" if self.language_code == "en" else "返回代码")
                + " %d" % return_code
            )
            messagebox.showerror(
                self._("任务失败"),
                self._("处理没有完成，请查看下方日志中的最后几行。"),
                parent=self,
            )

    def _stop_task(self):
        process = self._process
        if not self._task_running:
            return
        if not messagebox.askyesno(
            self._("停止任务"),
            self._("确认停止当前任务吗？已写入缓存的 OCR/模型结果仍会保留。"),
            parent=self,
        ):
            return
        self._cancel_requested = True
        try:
            if self._cancel_file:
                self._write_cancel_signal()
                self.after(5000, self._force_terminate_if_running)
            elif process is not None:
                process.terminate()
            self._append_log(self._("正在安全停止：先保存当前图片断点，再结束任务……"))
            self.status_text.set(self._("正在保存断点并停止……"))
        except Exception as exc:
            messagebox.showerror(self._("停止失败"), str(exc), parent=self)

    def _write_cancel_signal(self):
        if not self._cancel_file:
            return
        cancel_path = Path(self._cancel_file)
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("cancel\n", encoding="ascii")

    def _force_terminate_if_running(self):
        process = self._process
        if not self._task_running or process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            self._append_log(self._("安全停止等待超时，已结束后台进程；此前断点仍会保留。"))
        except Exception:
            pass

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + ("\n" if not text.endswith("\n") else ""))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_path(self, path):
        try:
            open_local_path(path)
        except Exception as exc:
            messagebox.showerror(self._("无法打开"), str(exc), parent=self)

    def _open_parent(self, path):
        self._open_path(str(Path(path).resolve().parent))

    def _on_close(self):
        if self._task_running:
            if not messagebox.askyesno(
                self._("任务正在运行"),
                self._("关闭窗口会停止当前任务，确认关闭吗？"),
                parent=self,
            ):
                return
            self._cancel_requested = True
            process = self._process
            if self._cancel_file:
                try:
                    self._write_cancel_signal()
                except Exception:
                    pass
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        process.terminate()
                    except Exception:
                        pass
        self.destroy()


def main():
    app = PhotoMetadataApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
