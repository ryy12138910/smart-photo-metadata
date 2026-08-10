#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import unittest
from unittest import mock

import photo_metadata_gui as gui
import smart_photo_metadata as smart


class GuiCommandTests(unittest.TestCase):
    def test_dashscope_key_can_be_read_from_process_environment(self):
        with mock.patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "test-key"},
            clear=False,
        ):
            self.assertEqual(gui.get_dashscope_api_key(), "test-key")

    def test_progress_lines_report_image_and_folder_phases(self):
        self.assertEqual(
            gui.parse_progress_line("[20/185] 已完成初步处理：a.jpg"),
            ("images", 20, 185),
        )
        self.assertEqual(
            gui.parse_progress_line("[folder 2/4] 正在检查同目录坐标"),
            ("folder", 2, 4),
        )
        self.assertEqual(
            gui.parse_progress_line("[resume 120/185] 已恢复上次断点"),
            ("resume", 120, 185),
        )
        self.assertEqual(
            gui.parse_progress_line("[ocr 80/185] 已完成 OCR 阶段"),
            ("ocr", 80, 185),
        )
        self.assertEqual(
            gui.parse_progress_line("[model 4/20] 已完成模型复核"),
            ("model", 4, 20),
        )

    def test_review_command_uses_safe_defaults(self):
        command = gui.build_review_command(
            "python.exe",
            r"D:\photos",
            r"D:\output\review.xlsx",
        )
        self.assertIn("review", command)
        self.assertEqual(command[command.index("--llm-review-mode") + 1], "suspicious")
        self.assertEqual(command[command.index("--excel-image-mode") + 1], "thumbnail")
        self.assertEqual(
            command[command.index("--group-coordinate-threshold-meters") + 1],
            "500.0",
        )
        parsed = smart.build_parser().parse_args(command[2:])
        self.assertEqual(parsed.command, "review")
        self.assertEqual(parsed.llm_provider, "dashscope")
        self.assertEqual(parsed.llm_model, "gemma4:cloud")
        self.assertEqual(parsed.llm_review_mode, "suspicious")
        self.assertEqual(parsed.llm_batch_size, 1)

    def test_review_command_passes_cooperative_cancel_file(self):
        command = gui.build_review_command(
            "python.exe",
            r"D:\photos",
            r"D:\output\review.xlsx",
            cancel_file=r"D:\output\review.xlsx.cancel",
        )
        self.assertEqual(
            command[command.index("--cancel-file") + 1],
            r"D:\output\review.xlsx.cancel",
        )

    def test_review_command_can_disable_model_and_limit_pilot(self):
        command = gui.build_review_command(
            "python.exe",
            r"D:\photos",
            r"D:\output\review.xlsx",
            model_provider="none",
            path_contains="40\u5e62",
            max_images=5,
            image_mode="none",
        )
        self.assertEqual(command[command.index("--llm-provider") + 1], "none")
        self.assertNotIn("--llm-model", command)
        self.assertEqual(command[command.index("--path-contains") + 1], "40\u5e62")
        self.assertEqual(command[command.index("--max-images") + 1], "5")

    def test_write_command_is_non_destructive_by_default(self):
        command = gui.build_write_command(
            "python.exe",
            r"D:\photos",
            r"D:\output\review.xlsx",
            r"D:\output\new_photos",
            dry_run=True,
        )
        self.assertIn("--dry-run", command)
        self.assertNotIn("--overwrite-existing-exif", command)
        self.assertNotIn("--in-place", command)
        parsed = smart.build_parser().parse_args(command[2:])
        self.assertEqual(parsed.command, "write")
        self.assertTrue(parsed.dry_run)


if __name__ == "__main__":
    unittest.main()
