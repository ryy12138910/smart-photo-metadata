#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Simple desktop interface for the photo metadata workflow."""

from __future__ import print_function

import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


PROJECT_DIR = Path(__file__).resolve().parent
PIPELINE_SCRIPT = PROJECT_DIR / "smart_photo_metadata.py"
DEFAULT_DATA_DIR = PROJECT_DIR / "data"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "outputs"
DEFAULT_REVIEW_XLSX = DEFAULT_OUTPUT_DIR / "metadata_review.xlsx"
DEFAULT_PHOTO_OUTPUT = DEFAULT_OUTPUT_DIR / "photos_with_exif"
DEFAULT_LOCAL_MODEL = "qwen3-vl:4b-instruct"
DEFAULT_CLOUD_MODEL = "gemma4:cloud"
DEFAULT_DASHSCOPE_OCR_MODEL = "qwen3.5-ocr"
DEFAULT_DASHSCOPE_REVIEW_MODEL = "qwen3.6-flash"
DEFAULT_MODEL = "%s + %s" % (
    DEFAULT_DASHSCOPE_OCR_MODEL,
    DEFAULT_DASHSCOPE_REVIEW_MODEL,
)
OLLAMA_TAGS_ENDPOINT = "http://127.0.0.1:11434/api/tags"

MODEL_MODE_LABELS = {
    "百炼 API：Qwen OCR + Qwen Flash（最快，推荐）": (
        "dashscope",
        "suspicious",
    ),
    "Ollama 云端 API：仅复核异常图片（最快，推荐）": (
        "ollama-cloud",
        "suspicious",
    ),
    "Ollama 云端 API：复核所有 EXIF 不完整图片": ("ollama-cloud", "all"),
    "本地模型：仅复核异常图片（推荐）": ("ollama", "suspicious"),
    "本地模型：复核所有 EXIF 不完整图片": ("ollama", "all"),
    "关闭本地模型，仅使用 OCR": ("none", "suspicious"),
}
IMAGE_MODE_LABELS = {
    "不嵌入图片，仅保留链接（最快）": "none",
    "压缩缩略图": "thumbnail",
    "嵌入原图（文件会很大）": "original",
}


def get_dashscope_api_key():
    """Read the process value, then the persistent Windows user value."""
    value = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if value or winreg is None:
        return value
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, "DASHSCOPE_API_KEY")
    except OSError:
        return ""
    return str(value).strip()


def build_review_command(
    python_executable,
    image_root,
    review_xlsx,
    *,
    model_provider="dashscope",
    model_review_mode="suspicious",
    model_name=DEFAULT_MODEL,
    image_mode="thumbnail",
    path_contains="",
    max_images=0,
    group_threshold_meters=500.0,
    cancel_file="",
):
    command = [
        str(python_executable),
        str(PIPELINE_SCRIPT),
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
    ]
    if model_provider not in ("none", "dashscope"):
        command.extend(["--llm-model", model_name.strip() or DEFAULT_MODEL])
        command.extend(
            [
                "--llm-batch-size",
                "1" if model_provider == "ollama-cloud" else "4",
            ]
        )
    if path_contains.strip():
        command.extend(["--path-contains", path_contains.strip()])
    if int(max_images) > 0:
        command.extend(["--max-images", str(int(max_images))])
    if str(cancel_file).strip():
        command.extend(["--cancel-file", str(cancel_file)])
    return command


def build_write_command(
    python_executable,
    image_root,
    review_xlsx,
    output_dir,
    *,
    dry_run=False,
    overwrite_existing_exif=False,
):
    command = [
        str(python_executable),
        str(PIPELINE_SCRIPT),
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


def ollama_available(timeout=1.5):
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_ENDPOINT, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


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
        self.title("图片经纬度与拍摄日期补全工具")
        self.geometry("1080x780")
        self.minsize(920, 680)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))

        self._process = None
        self._task_running = False
        self._cancel_requested = False
        self._task_kind = ""
        self._task_output = ""
        self._task_dry_run = False
        self._cancel_file = ""
        self._event_queue = queue.Queue()
        self._running_buttons = []

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

    def _create_variables(self):
        self.review_image_root = tk.StringVar(value=str(DEFAULT_DATA_DIR))
        self.review_xlsx = tk.StringVar(value=str(DEFAULT_REVIEW_XLSX))
        self.model_mode = tk.StringVar(value=next(iter(MODEL_MODE_LABELS)))
        self.model_name = tk.StringVar(value=DEFAULT_MODEL)
        self.excel_image_mode = tk.StringVar(value=next(iter(IMAGE_MODE_LABELS)))
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
        container = ttk.Frame(self, padding=(22, 18, 22, 16))
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="图片属性补全", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="先批量完成 OCR，再用云端视觉 API 复核问题图片；人工只处理“待核”行。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)
        review_tab = ttk.Frame(self.notebook, padding=16)
        write_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(review_tab, text="  1. 生成审核 XLSX  ")
        self.notebook.add(write_tab, text="  2. 生成新图片  ")
        self._build_review_tab(review_tab)
        self._build_write_tab(write_tab)

        activity = ttk.LabelFrame(
            container,
            text="运行状态与日志",
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
        ttk.Button(controls, text="清空日志", command=self._clear_log).pack(side="left")
        self.stop_button = ttk.Button(
            controls,
            text="停止当前任务",
            style="Danger.TButton",
            command=self._stop_task,
            state="disabled",
        )
        self.stop_button.pack(side="right")

    def _build_review_tab(self, parent):
        paths = ttk.LabelFrame(parent, text="输入与输出", style="Section.TLabelframe")
        paths.pack(fill="x")
        self._path_row(
            paths,
            0,
            "照片根目录",
            self.review_image_root,
            lambda: self._choose_directory(self.review_image_root),
            "选择照片文件夹",
        )
        self._path_row(
            paths,
            1,
            "审核表保存位置",
            self.review_xlsx,
            lambda: self._choose_save_xlsx(self.review_xlsx),
            "选择保存位置",
        )

        options = ttk.LabelFrame(parent, text="处理选项", style="Section.TLabelframe")
        options.pack(fill="x", pady=(12, 0))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text="模型策略").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        model_combo = ttk.Combobox(
            options,
            textvariable=self.model_mode,
            values=list(MODEL_MODE_LABELS),
            state="readonly",
        )
        model_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=5)
        model_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_model_state())

        ttk.Label(options, text="模型名称").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        self.model_entry = ttk.Entry(options, textvariable=self.model_name)
        self.model_entry.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(options, text="审核表图片").grid(row=1, column=2, sticky="w", padx=(18, 10), pady=5)
        ttk.Combobox(
            options,
            textvariable=self.excel_image_mode,
            values=list(IMAGE_MODE_LABELS),
            state="readonly",
        ).grid(row=1, column=3, sticky="ew", pady=5)

        ttk.Label(options, text="仅处理路径包含").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(options, textvariable=self.path_contains).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(options, text="最多处理图片数").grid(row=2, column=2, sticky="w", padx=(18, 10), pady=5)
        ttk.Entry(options, textvariable=self.max_images, width=12).grid(row=2, column=3, sticky="ew", pady=5)

        ttk.Label(options, text="同目录离群阈值（米）").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(options, textvariable=self.group_threshold).grid(
            row=3, column=1, sticky="ew", pady=5
        )
        ttk.Label(
            options,
            text=(
                "百炼模式读取 DASHSCOPE_API_KEY；建议离群阈值保持 500。"
            ),
            foreground="#52606D",
        ).grid(row=3, column=2, columnspan=2, sticky="w", padx=(18, 0), pady=5)

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(14, 0))
        self.review_run_button = ttk.Button(
            actions,
            text="开始生成审核表",
            style="Primary.TButton",
            command=self._start_review,
        )
        self.review_run_button.pack(side="left")
        self._running_buttons.append(self.review_run_button)
        ttk.Button(
            actions,
            text="打开审核表",
            command=lambda: self._open_path(self.review_xlsx.get()),
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            actions,
            text="打开所在文件夹",
            command=lambda: self._open_parent(self.review_xlsx.get()),
        ).pack(side="left", padx=(8, 0))

    def _build_write_tab(self, parent):
        paths = ttk.LabelFrame(parent, text="输入与输出", style="Section.TLabelframe")
        paths.pack(fill="x")
        self._path_row(
            paths,
            0,
            "原始照片根目录",
            self.write_image_root,
            lambda: self._choose_directory(self.write_image_root),
            "选择照片文件夹",
        )
        self._path_row(
            paths,
            1,
            "审核后的 XLSX",
            self.write_xlsx,
            lambda: self._choose_open_xlsx(self.write_xlsx),
            "选择审核表",
        )
        self._path_row(
            paths,
            2,
            "新图片输出目录",
            self.write_output_dir,
            lambda: self._choose_directory(self.write_output_dir, allow_new=True),
            "选择输出文件夹",
        )

        options = ttk.LabelFrame(parent, text="写入选项", style="Section.TLabelframe")
        options.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(
            options,
            text="仅演练，不生成图片（第一次运行推荐）",
            variable=self.dry_run,
        ).pack(anchor="w", pady=4)
        ttk.Checkbutton(
            options,
            text="允许审核表覆盖原图已有 EXIF 字段（谨慎使用）",
            variable=self.overwrite_exif,
        ).pack(anchor="w", pady=4)
        ttk.Label(
            options,
            text=(
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
            text="开始检查 / 生成图片",
            style="Primary.TButton",
            command=self._start_write,
        )
        self.write_run_button.pack(side="left")
        self._running_buttons.append(self.write_run_button)
        ttk.Button(
            actions,
            text="打开结果报告",
            command=lambda: self._open_path(
                str(Path(self.write_output_dir.get()) / "write_result_report.xlsx")
            ),
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            actions,
            text="打开输出目录",
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
        provider, _mode = MODEL_MODE_LABELS[self.model_mode.get()]
        self.model_entry.configure(
            state="normal"
            if provider in ("ollama-cloud", "ollama", "openai")
            else "disabled"
        )
        current = self.model_name.get().strip()
        if provider == "ollama-cloud" and current in ("", DEFAULT_LOCAL_MODEL):
            self.model_name.set(DEFAULT_CLOUD_MODEL)
        elif provider == "ollama" and current in ("", DEFAULT_CLOUD_MODEL, DEFAULT_MODEL):
            self.model_name.set(DEFAULT_LOCAL_MODEL)
        elif provider == "dashscope":
            self.model_name.set(DEFAULT_MODEL)

    def _choose_directory(self, variable, allow_new=False):
        current = variable.get().strip()
        initial = current if os.path.isdir(current) else str(PROJECT_DIR)
        selected = filedialog.askdirectory(
            title="选择文件夹",
            initialdir=initial,
            mustexist=not allow_new,
        )
        if selected:
            variable.set(os.path.normpath(selected))

    def _choose_save_xlsx(self, variable):
        current = Path(variable.get().strip() or DEFAULT_REVIEW_XLSX)
        selected = filedialog.asksaveasfilename(
            title="保存审核表",
            initialdir=str(current.parent),
            initialfile=current.name,
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
        )
        if selected:
            variable.set(os.path.normpath(selected))
            self.write_xlsx.set(os.path.normpath(selected))

    def _choose_open_xlsx(self, variable):
        current = Path(variable.get().strip() or DEFAULT_REVIEW_XLSX)
        selected = filedialog.askopenfilename(
            title="选择审核后的 XLSX",
            initialdir=str(current.parent if current.parent.exists() else PROJECT_DIR),
            filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")],
        )
        if selected:
            variable.set(os.path.normpath(selected))

    def _validated_number(self, value, label, minimum=0.0, integer=False):
        try:
            parsed = int(value) if integer else float(value)
        except (TypeError, ValueError):
            raise ValueError("%s必须是数字。" % label)
        if parsed < minimum:
            raise ValueError("%s不能小于 %s。" % (label, minimum))
        return parsed

    def _start_review(self):
        if self._task_running:
            return
        try:
            image_root_text = self.review_image_root.get().strip()
            review_xlsx_text = self.review_xlsx.get().strip()
            if not image_root_text:
                raise ValueError("请选择照片根目录。")
            if not review_xlsx_text:
                raise ValueError("请选择审核表保存位置。")
            image_root = os.path.abspath(image_root_text)
            review_xlsx = os.path.abspath(review_xlsx_text)
            if not os.path.isdir(image_root):
                raise ValueError("照片根目录不存在。")
            if not review_xlsx.lower().endswith(".xlsx"):
                raise ValueError("审核表保存位置必须是 .xlsx 文件。")
            max_images = self._validated_number(
                self.max_images.get().strip() or "0",
                "最多处理图片数",
                minimum=0,
                integer=True,
            )
            threshold = self._validated_number(
                self.group_threshold.get().strip() or "500",
                "同目录离群阈值",
                minimum=0,
            )
            provider, review_mode = MODEL_MODE_LABELS[self.model_mode.get()]
            image_mode = IMAGE_MODE_LABELS[self.excel_image_mode.get()]
        except (KeyError, ValueError) as exc:
            messagebox.showerror("参数有误", str(exc), parent=self)
            return

        if provider in ("ollama", "ollama-cloud") and not ollama_available():
            proceed = messagebox.askyesno(
                "未检测到 Ollama",
                (
                    "本地模型服务 http://127.0.0.1:11434 当前不可用。\n\n"
                    "可以先启动 Ollama 后重试；也可以继续，模型相关行将可能标记为待核。\n"
                    "是否仍然继续？"
                ),
                parent=self,
            )
            if not proceed:
                return
        if provider == "dashscope":
            dashscope_api_key = get_dashscope_api_key()
            if not dashscope_api_key:
                messagebox.showerror(
                    "未读取到百炼 API Key",
                    (
                        "未读取到用户环境变量 DASHSCOPE_API_KEY。\n\n"
                        "请确认 PowerShell 检测结果为 True 后重新运行。"
                    ),
                    parent=self,
                )
                return
            # The child process inherits this value; it is never added to the
            # command line, configuration, cache, or logs.
            os.environ["DASHSCOPE_API_KEY"] = dashscope_api_key

        command = build_review_command(
            sys.executable,
            image_root,
            review_xlsx,
            model_provider=provider,
            model_review_mode=review_mode,
            model_name=self.model_name.get(),
            image_mode=image_mode,
            path_contains=self.path_contains.get(),
            max_images=max_images,
            group_threshold_meters=threshold,
            cancel_file=review_xlsx + ".cancel",
        )
        self.write_image_root.set(image_root)
        self.write_xlsx.set(review_xlsx)
        self._run_command(
            "review",
            command,
            review_xlsx,
            cancel_file=review_xlsx + ".cancel",
        )

    def _start_write(self):
        if self._task_running:
            return
        image_root_text = self.write_image_root.get().strip()
        review_xlsx_text = self.write_xlsx.get().strip()
        output_dir_text = self.write_output_dir.get().strip()
        if not image_root_text or not review_xlsx_text or not output_dir_text:
            messagebox.showerror(
                "参数有误",
                "请选择原始照片目录、审核后的 XLSX 和输出目录。",
                parent=self,
            )
            return
        image_root = os.path.abspath(image_root_text)
        review_xlsx = os.path.abspath(review_xlsx_text)
        output_dir = os.path.abspath(output_dir_text)
        if not os.path.isdir(image_root):
            messagebox.showerror("参数有误", "原始照片根目录不存在。", parent=self)
            return
        if not os.path.isfile(review_xlsx):
            messagebox.showerror("参数有误", "找不到审核后的 XLSX 文件。", parent=self)
            return
        if os.path.normcase(image_root) == os.path.normcase(output_dir):
            messagebox.showerror(
                "输出目录不安全",
                "输出目录不能与原始照片根目录相同。",
                parent=self,
            )
            return
        if self.overwrite_exif.get():
            confirmed = messagebox.askyesno(
                "确认覆盖已有 EXIF",
                (
                    "你已启用“覆盖原图已有 EXIF 字段”。\n"
                    "虽然程序仍会输出到新目录，但审核表的值将优先于原图现有属性。\n\n"
                    "确认继续吗？"
                ),
                parent=self,
            )
            if not confirmed:
                return

        command = build_write_command(
            sys.executable,
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
    ):
        if not PIPELINE_SCRIPT.is_file():
            messagebox.showerror("程序不完整", "找不到 smart_photo_metadata.py。", parent=self)
            return
        Path(task_output).parent.mkdir(parents=True, exist_ok=True)
        self._task_kind = task_kind
        self._task_output = task_output
        self._task_dry_run = bool(dry_run)
        self._cancel_file = str(cancel_file or "")
        if self._cancel_file:
            try:
                os.remove(self._cancel_file)
            except FileNotFoundError:
                pass
        self._task_running = True
        self._cancel_requested = False
        self.status_text.set("正在运行……")
        self.progress_text.set("等待处理进度")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.stop_button.configure(state="normal")
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
        try:
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
                            self.status_text.set("正在进行同目录坐标复核……")
                            self.progress_text.set(
                                "目录复核 %d / %d" % (current, total)
                            )
                        elif phase == "resume":
                            self.status_text.set("已恢复上次断点，正在继续……")
                            self.progress_text.set(
                                "从图片 %d / %d 继续" % (current, total)
                            )
                        elif phase == "ocr":
                            self.status_text.set("正在批量进行 OCR……")
                            self.progress_text.set(
                                "OCR %d / %d" % (current, total)
                            )
                        elif phase == "model":
                            self.status_text.set("正在批量进行模型复核……")
                            self.progress_text.set(
                                "模型复核 %d / %d" % (current, total)
                            )
                        else:
                            self.status_text.set("正在生成审核表……")
                            self.progress_text.set(
                                "图片 %d / %d" % (current, total)
                            )
                    elif value.startswith("[workbook]"):
                        self.status_text.set("正在写入审核 XLSX……")
                        self.progress_text.set("正在保存文件")
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                elif event == "done":
                    self._finish_task(int(value))
                elif event == "error":
                    self._append_log("启动失败：" + value)
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
        for button in self._running_buttons:
            button.configure(state="normal")
        self._process = None
        self._task_running = False
        self._cancel_requested = False
        if self._cancel_file:
            try:
                os.remove(self._cancel_file)
            except OSError:
                pass
        self._cancel_file = ""

        if was_cancelled:
            self.status_text.set("任务已停止")
            self.progress_text.set("断点已保存，下次可继续")
            self._append_log("任务已停止；再次使用相同照片目录和审核表路径即可续跑。")
        elif return_code == 0:
            self.status_text.set("任务已完成")
            self.progress_text.set("成功")
            message = "审核表已生成。" if self._task_kind == "review" else (
                "演练报告已生成。" if self._task_dry_run else "新图片已生成。"
            )
            if messagebox.askyesno(
                "任务完成",
                message + "\n\n是否打开结果位置？",
                parent=self,
            ):
                if self._task_kind == "review":
                    self._open_path(self._task_output)
                else:
                    self._open_path(self._task_output)
        else:
            self.status_text.set("任务失败")
            self.progress_text.set("返回代码 %d" % return_code)
            messagebox.showerror(
                "任务失败",
                "处理没有完成，请查看下方日志中的最后几行。",
                parent=self,
            )

    def _stop_task(self):
        process = self._process
        if not self._task_running:
            return
        if not messagebox.askyesno(
            "停止任务",
            "确认停止当前任务吗？已写入缓存的 OCR/模型结果仍会保留。",
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
            self._append_log("正在安全停止：先保存当前图片断点，再结束任务……")
            self.status_text.set("正在保存断点并停止……")
        except Exception as exc:
            messagebox.showerror("停止失败", str(exc), parent=self)

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
            self._append_log("安全停止等待超时，已结束后台进程；此前断点仍会保留。")
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
            messagebox.showerror("无法打开", str(exc), parent=self)

    def _open_parent(self, path):
        self._open_path(str(Path(path).resolve().parent))

    def _on_close(self):
        if self._task_running:
            if not messagebox.askyesno(
                "任务正在运行",
                "关闭窗口会停止当前任务，确认关闭吗？",
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
