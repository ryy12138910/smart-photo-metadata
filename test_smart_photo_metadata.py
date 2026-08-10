#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from PIL import Image

import smart_photo_metadata as smart


def args(**overrides):
    values = {
        "expected_lat_min": 31.2336111111,
        "expected_lat_max": 31.4361111111,
        "expected_lon_min": 120.635,
        "expected_lon_max": 120.8602777778,
        "llm_min_confidence": 0.60,
        "auto_approve_threshold": 0.88,
        "no_auto_approve": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def ocr_batch_results(paths, text_or_factory):
    results = {}
    for path in paths:
        text = text_or_factory(path) if callable(text_or_factory) else text_or_factory
        results[os.path.normcase(os.path.abspath(path))] = {
            "text": text,
            "error": "",
        }
    return results


class SmartMetadataTests(unittest.TestCase):
    @staticmethod
    def model_result():
        return {
            "latitude_text": "31°20'",
            "longitude_text": "120°41'",
            "shooting_datetime_text": "2026.06.09",
            "confidence": {
                "latitude": 0.95,
                "longitude": 0.95,
                "shooting_datetime": 0.95,
            },
            "evidence": {
                "latitude": "31°20'",
                "longitude": "120°41'",
                "shooting_datetime": "2026.06.09",
            },
            "notes": "",
        }

    def test_default_umi_ocr_limit_is_960(self):
        self.assertEqual(smart.DEFAULT_OCR_LIMIT_SIDE_LEN, 960)

    def test_default_umi_ocr_batch_size_is_32(self):
        parsed = smart.build_parser().parse_args(
            [
                "review",
                "--image-root",
                "data",
                "--review-xlsx",
                "review.xlsx",
            ]
        )
        self.assertEqual(parsed.ocr_batch_size, 32)

    def test_default_provider_is_dashscope_two_stage_review(self):
        parsed = smart.build_parser().parse_args(
            [
                "review",
                "--image-root",
                "data",
                "--review-xlsx",
                "review.xlsx",
            ]
        )
        self.assertEqual(parsed.llm_provider, "dashscope")
        self.assertEqual(parsed.dashscope_ocr_model, "qwen3.5-ocr")
        self.assertEqual(parsed.dashscope_review_model, "qwen3.6-flash")
        self.assertEqual(parsed.dashscope_ocr_concurrency, 10)
        self.assertEqual(parsed.dashscope_review_concurrency, 5)

    def test_dashscope_credentials_uses_user_environment(self):
        parsed = args(llm_api_key="", dashscope_base_url="")
        with mock.patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_BASE_URL": "https://example.invalid/v1/",
            },
            clear=False,
        ):
            key, endpoint = smart.dashscope_credentials(parsed)
        self.assertEqual(key, "test-key")
        self.assertEqual(endpoint, "https://example.invalid/v1/chat/completions")

    def test_dashscope_request_disables_thinking_and_sends_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "photo.jpg")
            Image.new("RGB", (20, 20), "white").save(image_path)
            parsed = args(
                llm_api_key="test-key",
                dashscope_base_url="https://example.invalid/v1",
                llm_timeout=10,
                api_max_retries=2,
            )
            response = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(self.model_result())
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 45,
                },
            }
            work = {
                "image_path": image_path,
                "ocr_text": "纬度 31°20' 经度 120°41'",
            }
            with mock.patch.object(
                smart,
                "http_json_with_retry",
                return_value=response,
            ) as request_mock:
                result, usage, _elapsed = smart.call_dashscope_model(
                    work,
                    parsed,
                    "qwen3.5-ocr",
                )

            endpoint, payload, timeout = request_mock.call_args.args[:3]
            self.assertEqual(
                endpoint,
                "https://example.invalid/v1/chat/completions",
            )
            self.assertEqual(timeout, 10)
            self.assertFalse(payload["enable_thinking"])
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            image_part = payload["messages"][0]["content"][0]
            self.assertTrue(
                image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
            )
            self.assertEqual(result["latitude_text"], "31°20'")
            self.assertEqual(usage, {"input_tokens": 123, "output_tokens": 45})

    def test_dashscope_pipeline_routes_only_conflict_to_flash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = smart.ResumeCache(os.path.join(temp_dir, "cache.json"))
            pending = []
            for index in range(2):
                relative = "%d.jpg" % index
                item = {"signature": str(index)}
                cache.data["items"][relative] = item
                pending.append(
                    {
                        "relative": relative,
                        "image_path": os.path.join(temp_dir, relative),
                        "ocr_text": (
                            "纬度 31°20' 经度 120°41' 拍摄时间 2026.06.09"
                        ),
                        "item": item,
                        "model_result": None,
                        "model_error": "",
                    }
                )
            parsed = args(
                llm_provider="dashscope",
                llm_api_key="test-key",
                dashscope_base_url="https://example.invalid/v1",
                dashscope_ocr_model="qwen3.5-ocr",
                dashscope_review_model="qwen3.6-flash",
                dashscope_ocr_concurrency=2,
                dashscope_review_concurrency=2,
                refresh_model=False,
                cancel_file="",
            )
            counts = {
                "model": 0,
                "model_error": 0,
                "api_input_tokens": 0,
                "api_output_tokens": 0,
                "qwen_ocr_calls": 0,
                "qwen_review_calls": 0,
                "qwen_ocr_seconds": 0.0,
                "qwen_review_seconds": 0.0,
                "qwen_ocr_input_tokens": 0,
                "qwen_ocr_output_tokens": 0,
                "qwen_review_input_tokens": 0,
                "qwen_review_output_tokens": 0,
                "api_cost_cny": 0.0,
            }
            calls = []

            def fake_call(work, _args, model_name, final_review=False):
                calls.append((work["relative"], model_name, final_review))
                result = self.model_result()
                result["shooting_datetime_text"] = "2026.06.09 10:30"
                result["evidence"]["shooting_datetime"] = "2026.06.09 10:30"
                if work["relative"] == "1.jpg" and not final_review:
                    result = dict(result)
                    result["longitude_text"] = "120°50'"
                return result, {"input_tokens": 100, "output_tokens": 20}, 0.1

            with mock.patch.object(
                smart,
                "call_dashscope_model",
                side_effect=fake_call,
            ):
                smart.review_dashscope_pipeline(
                    pending,
                    parsed,
                    cache,
                    "model-key",
                    counts,
                )

            ocr_calls = [call for call in calls if not call[2]]
            review_calls = [call for call in calls if call[2]]
            self.assertEqual(len(ocr_calls), 2)
            self.assertEqual(
                review_calls,
                [("1.jpg", "qwen3.6-flash", True)],
            )
            self.assertEqual(counts["qwen_ocr_calls"], 2)
            self.assertEqual(counts["qwen_review_calls"], 1)
            self.assertTrue(all(work["model_result"] for work in pending))
            self.assertAlmostEqual(counts["api_cost_cny"], 0.000444)

    def test_umi_batch_request_uses_paths_and_maps_each_result(self):
        paths = [
            os.path.abspath(os.path.join("photos", "1.jpg")),
            os.path.abspath(os.path.join("photos", "2.jpg")),
        ]
        response_data = {
            "code": 100,
            "version": 1,
            "data": [
                {"path": paths[0], "code": 100, "data": "first"},
                {"path": paths[1], "code": 100, "data": "second"},
            ],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_data).encode("utf-8")

        with mock.patch.object(
            smart, "open_url_no_proxy", return_value=FakeResponse()
        ) as open_mock:
            mapped = smart.run_umi_ocr_batch(
                "Umi-OCR.exe",
                paths,
                60,
                "http://127.0.0.1:1224/api/ocr",
            )

        request = open_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["paths"], paths)
        self.assertNotIn("base64", payload)
        self.assertTrue(request.full_url.endswith("/api/ocr/batch"))
        self.assertEqual(mapped[os.path.normcase(paths[0])]["text"], "first")
        self.assertEqual(mapped[os.path.normcase(paths[1])]["text"], "second")

    def test_umi_service_is_checked_once_for_multiple_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            Image.new("RGB", (20, 20), "white").save(
                os.path.join(image_root, "1.jpg")
            )
            Image.new("RGB", (20, 20), "white").save(
                os.path.join(image_root, "2.jpg")
            )
            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    os.path.join(temp_dir, "review.xlsx"),
                    "--llm-provider",
                    "none",
                    "--excel-image-mode",
                    "none",
                    "--no-group-consistency",
                ]
            )
            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart, "ensure_umi_server_ready"
            ) as ensure_umi, mock.patch.object(
                smart,
                "run_umi_ocr_batch",
                side_effect=lambda _exe, paths, _timeout, _endpoint: ocr_batch_results(
                    paths, "北纬 31°20 东经 120°41 2026.06.09"
                ),
            ) as run_ocr, mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)

            self.assertEqual(ensure_umi.call_count, 1)
            self.assertEqual(run_ocr.call_count, 1)
            self.assertEqual(len(run_ocr.call_args.args[1]), 2)

    def test_umi_readiness_failure_aborts_once_before_any_ocr_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            for name in ("1.jpg", "2.jpg"):
                Image.new("RGB", (20, 20), "white").save(
                    os.path.join(image_root, name)
                )
            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    os.path.join(temp_dir, "review.xlsx"),
                    "--llm-provider",
                    "none",
                    "--excel-image-mode",
                    "none",
                    "--no-group-consistency",
                ]
            )
            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart,
                "ensure_umi_server_ready",
                side_effect=RuntimeError("Umi unavailable"),
            ) as ensure_umi, mock.patch.object(
                smart, "run_umi_ocr_batch"
            ) as batch_ocr:
                with self.assertRaisesRegex(RuntimeError, "Umi unavailable"):
                    smart.create_review(parsed)

            self.assertEqual(ensure_umi.call_count, 1)
            self.assertEqual(batch_ocr.call_count, 0)

    def test_batch_response_is_mapped_by_stable_id_not_order(self):
        batch = [
            {"batch_id": "img_a"},
            {"batch_id": "img_b"},
        ]
        response = {
            "results": [
                dict({"id": "img_b"}, **self.model_result()),
                dict({"id": "img_a"}, **self.model_result()),
            ]
        }
        mapped = smart.parse_batch_model_response(response, batch)
        self.assertEqual(set(mapped), {"img_a", "img_b"})
        self.assertNotIn("id", mapped["img_a"])

    def test_ollama_batch_request_contains_all_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batch = []
            for index in range(2):
                image_path = os.path.join(temp_dir, "%d.jpg" % index)
                Image.new("RGB", (20, 20), "white").save(image_path)
                batch.append(
                    {
                        "batch_id": "img_%d" % index,
                        "image_path": image_path,
                        "ocr_text": "无法识别",
                    }
                )
            parsed = args(
                llm_provider="ollama",
                llm_model="qwen3-vl:4b-instruct",
                llm_endpoint="",
                llm_timeout=30,
            )
            response_value = {
                "results": [
                    dict({"id": work["batch_id"]}, **self.model_result())
                    for work in batch
                ]
            }
            with mock.patch.object(
                smart,
                "http_json",
                return_value={
                    "message": {
                        "content": __import__("json").dumps(
                            response_value,
                            ensure_ascii=False,
                        )
                    }
                },
            ) as request_mock:
                mapped = smart.call_local_model_batch(batch, parsed)

            payload = request_mock.call_args.args[1]
            self.assertEqual(len(payload["messages"][1]["images"]), 2)
            self.assertEqual(set(mapped), {"img_0", "img_1"})

    def test_ollama_cloud_uses_compact_prompt_without_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "cloud.jpg")
            Image.new("RGB", (20, 20), "white").save(image_path)
            parsed = args(
                llm_provider="ollama-cloud",
                llm_model="gemma4:cloud",
                llm_endpoint="",
                llm_api_key="",
                llm_timeout=30,
            )
            compact = {
                "latitude_text": "31.2",
                "longitude_text": "120.6",
                "shooting_datetime_text": "2026-06-18 15:24",
                "confidence": {
                    "latitude": 0.9,
                    "longitude": 0.9,
                    "shooting_datetime": 0.9,
                },
            }
            with mock.patch.object(
                smart,
                "http_json",
                return_value={
                    "message": {
                        "content": json.dumps(compact, ensure_ascii=False),
                    }
                },
            ) as request_mock:
                result = smart.call_local_model(image_path, "OCR", parsed)

            payload = request_mock.call_args.args[1]
            self.assertFalse(payload["think"])
            self.assertNotIn("format", payload)
            self.assertEqual(result["evidence"]["latitude"], "31.2")
            self.assertIn("云端", result["notes"])

    def test_failed_four_image_batch_splits_into_two_image_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = smart.ResumeCache(os.path.join(temp_dir, "cache.json"))
            pending = []
            for index in range(4):
                relative = "%d.jpg" % index
                item = {"signature": str(index)}
                cache.data["items"][relative] = item
                pending.append(
                    {
                        "relative": relative,
                        "image_path": os.path.join(temp_dir, relative),
                        "ocr_text": "无法识别",
                        "item": item,
                        "batch_id": "img_%d" % index,
                        "model_result": None,
                        "model_error": "",
                    }
                )
            batch_sizes = []

            def fake_batch(batch, _args):
                batch_sizes.append(len(batch))
                if len(batch) == 4:
                    raise RuntimeError("simulated batch overflow")
                return {
                    work["batch_id"]: self.model_result()
                    for work in batch
                }

            parsed = args(
                llm_batch_size=4,
                cancel_file="",
            )
            counts = {"model": 0, "model_error": 0}
            with mock.patch.object(
                smart, "call_local_model_batch", side_effect=fake_batch
            ):
                smart.review_model_batches(
                    pending,
                    parsed,
                    cache,
                    "model-key",
                    counts,
                )

            self.assertEqual(batch_sizes, [4, 2, 2])
            self.assertEqual(counts["model"], 3)
            self.assertEqual(counts["model_error"], 0)
            self.assertTrue(all(work["model_result"] for work in pending))

    def test_all_ocr_finishes_before_models_are_called_in_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            for index in range(5):
                Image.new("RGB", (20, 20), "white").save(
                    os.path.join(image_root, "%d.jpg" % index)
                )
            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    os.path.join(temp_dir, "review.xlsx"),
                    "--llm-provider",
                    "ollama",
                    "--llm-review-mode",
                    "suspicious",
                    "--llm-batch-size",
                    "4",
                    "--excel-image-mode",
                    "none",
                    "--no-group-consistency",
                ]
            )
            events = []
            batch_sizes = []

            def fake_ocr_batch(_exe, paths, _timeout, _endpoint):
                events.extend(["ocr"] * len(paths))
                return ocr_batch_results(paths, "无法识别")

            def fake_batch(batch, _args):
                events.append("model")
                batch_sizes.append(len(batch))
                return {
                    work["batch_id"]: self.model_result()
                    for work in batch
                }

            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart, "run_umi_ocr_batch", side_effect=fake_ocr_batch
            ), mock.patch.object(
                smart, "call_local_model_batch", side_effect=fake_batch
            ), mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)

            self.assertEqual(events[:5], ["ocr"] * 5)
            self.assertEqual(events[5:], ["model", "model"])
            self.assertEqual(batch_sizes, [4, 1])

    def test_restart_after_ocr_resumes_at_model_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            for index in range(5):
                Image.new("RGB", (20, 20), "white").save(
                    os.path.join(image_root, "%d.jpg" % index)
                )
            cancel_file = os.path.join(temp_dir, "review.cancel")
            argv = [
                "review",
                "--image-root",
                image_root,
                "--review-xlsx",
                os.path.join(temp_dir, "review.xlsx"),
                "--llm-provider",
                "ollama",
                "--llm-review-mode",
                "suspicious",
                "--llm-batch-size",
                "4",
                "--excel-image-mode",
                "none",
                "--no-group-consistency",
                "--cancel-file",
                cancel_file,
            ]
            first_args = smart.build_parser().parse_args(argv)

            def cancel_at_model(_batch, _args):
                with open(cancel_file, "w", encoding="ascii") as handle:
                    handle.write("cancel")
                smart.check_cancelled(first_args)

            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart,
                "run_umi_ocr_batch",
                side_effect=lambda _exe, paths, _timeout, _endpoint: ocr_batch_results(
                    paths, "无法识别"
                ),
            ) as first_ocr, mock.patch.object(
                smart, "call_local_model_batch", side_effect=cancel_at_model
            ), mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                with self.assertRaises(smart.TaskCancelled):
                    smart.create_review(first_args)
            self.assertEqual(first_ocr.call_count, 1)
            self.assertEqual(len(first_ocr.call_args.args[1]), 5)

            os.remove(cancel_file)
            second_args = smart.build_parser().parse_args(argv)
            batch_sizes = []

            def finish_batch(batch, _args):
                batch_sizes.append(len(batch))
                return {
                    work["batch_id"]: self.model_result()
                    for work in batch
                }

            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart,
                "run_umi_ocr_batch",
                side_effect=AssertionError("OCR checkpoint should be reused"),
            ) as second_ocr, mock.patch.object(
                smart, "call_local_model_batch", side_effect=finish_batch
            ), mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(second_args), 0)

            self.assertEqual(second_ocr.call_count, 0)
            self.assertEqual(batch_sizes, [4, 1])

    def test_folder_outliers_are_reviewed_in_one_model_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            for folder_index in range(4):
                folder = os.path.join(image_root, "group_%d" % folder_index)
                os.makedirs(folder)
                for image_index in range(4):
                    Image.new("RGB", (20, 20), "white").save(
                        os.path.join(folder, "%d.jpg" % image_index)
                    )
            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    os.path.join(temp_dir, "review.xlsx"),
                    "--llm-provider",
                    "ollama",
                    "--llm-review-mode",
                    "suspicious",
                    "--llm-batch-size",
                    "4",
                    "--excel-image-mode",
                    "none",
                ]
            )

            def fake_ocr_batch(_exe, paths, _timeout, _endpoint):
                def text_for_path(image_path):
                    longitude = (
                        "120°47" if image_path.endswith("3.jpg") else "120°41"
                    )
                    return (
                        "北纬 31°20 东经 %s 2026.06.09 10:30"
                        % longitude
                    )

                return ocr_batch_results(paths, text_for_path)

            batch_sizes = []

            def fake_batch(batch, _args):
                batch_sizes.append(len(batch))
                return {
                    work["batch_id"]: self.model_result()
                    for work in batch
                }

            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart, "run_umi_ocr_batch", side_effect=fake_ocr_batch
            ), mock.patch.object(
                smart, "call_local_model_batch", side_effect=fake_batch
            ), mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)

            self.assertEqual(batch_sizes, [4])

    def test_default_model_review_mode_is_suspicious(self):
        parsed = smart.build_parser().parse_args(
            [
                "review",
                "--image-root",
                "data",
                "--review-xlsx",
                "review.xlsx",
            ]
        )
        self.assertEqual(parsed.llm_review_mode, "suspicious")
        self.assertEqual(parsed.excel_image_mode, "thumbnail")
        self.assertEqual(parsed.group_coordinate_threshold_meters, 500.0)

    def test_folder_coordinate_outlier_detects_wrong_ocr_minutes(self):
        def work(name, lon):
            return {
                "relative": os.path.join("40\u5e62", name),
                "record": {
                    smart.core.L_LAT: "31.33333333",
                    smart.core.L_LON: "%.8f" % lon,
                },
            }

        items = [
            work("1.jpg", 120 + 41 / 60.0),
            work("2.jpg", 120 + 41 / 60.0),
            work("3.jpg", 120 + 41 / 60.0),
            work("4.jpg", 120 + 47 / 60.0),
        ]
        outliers = smart.folder_coordinate_outliers(items, 500.0, 3)
        self.assertEqual(len(outliers), 1)
        self.assertEqual(outliers[0]["index"], 3)
        self.assertGreater(outliers[0]["distance"], 9000)

    def test_resume_cache_replays_interrupted_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "photo.jpg")
            cache_path = os.path.join(temp_dir, "review.cache.json")
            Image.new("RGB", (8, 8), "white").save(image_path)

            first = smart.ResumeCache(cache_path)
            item = first.item("photo.jpg", image_path)
            item["ocr_text"] = "北纬31.3 东经120.7"
            first.save("photo.jpg")

            self.assertFalse(os.path.exists(cache_path))
            self.assertTrue(os.path.exists(cache_path + ".journal"))
            resumed = smart.ResumeCache(cache_path)
            self.assertEqual(
                resumed.item("photo.jpg", image_path)["ocr_text"],
                "北纬31.3 东经120.7",
            )

    def test_completed_review_row_is_reused_without_ocr_or_exif_reread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            image_path = os.path.join(image_root, "photo.jpg")
            review_path = os.path.join(temp_dir, "review.xlsx")
            Image.new("RGB", (20, 20), "white").save(image_path)
            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    review_path,
                    "--llm-provider",
                    "none",
                    "--excel-image-mode",
                    "none",
                    "--no-group-consistency",
                ]
            )
            ocr_text = "北纬 31°20 东经 120°41 2026.06.09"

            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ) as read_exif, mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart,
                "run_umi_ocr_batch",
                side_effect=lambda _exe, paths, _timeout, _endpoint: ocr_batch_results(
                    paths, ocr_text
                ),
            ) as run_ocr, mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)
                self.assertEqual(read_exif.call_count, 1)
                self.assertEqual(run_ocr.call_count, 1)

            with mock.patch.object(
                smart.core,
                "read_exif_summary",
                side_effect=AssertionError("EXIF should come from completed checkpoint"),
            ) as read_exif, mock.patch.object(
                smart,
                "run_umi_ocr_batch",
                side_effect=AssertionError("OCR should not run again"),
            ) as run_ocr, mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)
                self.assertEqual(read_exif.call_count, 0)
                self.assertEqual(run_ocr.call_count, 0)

    def test_completed_row_with_ocr_error_is_retried_on_next_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            image_path = os.path.join(image_root, "photo.jpg")
            review_path = os.path.join(temp_dir, "review.xlsx")
            Image.new("RGB", (20, 20), "white").save(image_path)
            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    review_path,
                    "--llm-provider",
                    "none",
                    "--excel-image-mode",
                    "none",
                    "--no-group-consistency",
                ]
            )

            def failed_batch(_exe, paths, _timeout, _endpoint):
                return {
                    os.path.normcase(os.path.abspath(path)): {
                        "text": "",
                        "error": "temporary Umi failure",
                    }
                    for path in paths
                }

            with mock.patch.object(
                smart.core, "read_exif_summary", return_value={}
            ), mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart, "run_umi_ocr_batch", side_effect=failed_batch
            ), mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)

            with mock.patch.object(
                smart.core,
                "read_exif_summary",
                side_effect=AssertionError("cached EXIF should be reused"),
            ) as read_exif, mock.patch.object(
                smart, "ensure_umi_server_ready"
            ), mock.patch.object(
                smart,
                "run_umi_ocr_batch",
                side_effect=lambda _exe, paths, _timeout, _endpoint: ocr_batch_results(
                    paths, "北纬 31°20 东经 120°41 2026.06.09"
                ),
            ) as run_ocr, mock.patch.object(
                smart.core, "write_review_workbook"
            ):
                self.assertEqual(smart.create_review(parsed), 0)

            self.assertEqual(read_exif.call_count, 0)
            self.assertEqual(run_ocr.call_count, 1)

    def test_legacy_ocr_checkpoint_is_reported_as_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = os.path.join(temp_dir, "photos")
            os.makedirs(image_root)
            image_path = os.path.join(image_root, "photo.jpg")
            review_path = os.path.join(temp_dir, "review.xlsx")
            cache_path = os.path.splitext(review_path)[0] + ".cache.json"
            Image.new("RGB", (20, 20), "white").save(image_path)

            legacy_cache = smart.ResumeCache(cache_path)
            legacy_item = legacy_cache.item("photo.jpg", image_path)
            legacy_item["ocr_text"] = "北纬 31°20 东经 120°41 2026.06.09"
            legacy_cache.save("photo.jpg")
            legacy_cache.compact()

            parsed = smart.build_parser().parse_args(
                [
                    "review",
                    "--image-root",
                    image_root,
                    "--review-xlsx",
                    review_path,
                    "--llm-provider",
                    "none",
                    "--excel-image-mode",
                    "none",
                    "--no-group-consistency",
                ]
            )
            with mock.patch.object(
                smart, "run_umi_ocr_batch"
            ) as run_ocr, mock.patch.object(
                smart.core, "write_review_workbook"
            ), mock.patch(
                "builtins.print"
            ) as print_mock:
                self.assertEqual(smart.create_review(parsed), 0)

            self.assertEqual(run_ocr.call_count, 0)
            output_lines = [" ".join(str(value) for value in call.args) for call in print_mock.call_args_list]
            self.assertTrue(any("[resume 1/1]" in line for line in output_lines))

    def test_excel_thumbnail_is_physically_resized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "large.bmp")
            Image.new("RGB", (2400, 1600), (30, 90, 160)).save(source_path)
            thumbnail_path = smart.core.create_excel_thumbnail(
                source_path,
                temp_dir,
                520,
                320,
                70,
            )
            with Image.open(thumbnail_path) as thumbnail:
                self.assertLessEqual(thumbnail.width, 520)
                self.assertLessEqual(thumbnail.height, 320)
            self.assertLess(
                os.path.getsize(thumbnail_path),
                os.path.getsize(source_path),
            )

    def test_excel_none_mode_keeps_link_without_embedding_image(self):
        import openpyxl

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "photo.jpg")
            workbook_path = os.path.join(temp_dir, "review.xlsx")
            Image.new("RGB", (800, 600), (100, 120, 140)).save(source_path)
            record = {
                smart.core.L_REVIEW: smart.core.V_CHECK,
                smart.core.L_PARSE_STATUS: "NEEDS_REVIEW",
                smart.core.L_PHOTO: "",
                smart.core.L_PHOTO_LINK: source_path,
                smart.core.L_SOURCE_PATH: source_path,
            }
            smart.core.write_review_workbook(
                workbook_path,
                [record],
                520,
                320,
                image_mode="none",
            )
            workbook = openpyxl.load_workbook(workbook_path)
            sheet = workbook["OCR\u5ba1\u6838"]
            self.assertEqual(len(sheet._images), 0)
            link_col = smart.core.REVIEW_HEADERS.index(smart.core.L_PHOTO_LINK) + 1
            self.assertEqual(sheet.cell(2, link_col).hyperlink.target, source_path)

    def test_repairs_observed_umi_error(self):
        text = "\u5317\u7eac 31\u00b020 \u4e1c\u7eaa\n720\u00b041 2026.06.09"
        repaired, changed = smart.repair_common_ocr_errors(text)
        self.assertTrue(changed)
        self.assertIn("\u4e1c\u7ecf", repaired)
        self.assertIn("120\u00b041", repaired)
        parsed = smart.parse_ocr_fields(text, args())
        self.assertAlmostEqual(parsed["lat"], 31 + 20 / 60.0, places=6)
        self.assertAlmostEqual(parsed["lon"], 120 + 41 / 60.0, places=6)
        self.assertEqual(parsed["time"], "2026:06:09")
        self.assertFalse(parsed["time_has_clock"])

    def test_date_only_ocr_requests_visual_review(self):
        parsed = smart.parse_ocr_fields(
            "北纬 31°20' 东经 120°41' 2026.06.09",
            args(),
        )
        self.assertEqual(parsed["time"], "2026:06:09")
        self.assertFalse(parsed["time_has_clock"])
        self.assertTrue(smart.needs_model({}, parsed, "suspicious"))
        self.assertIn("date only", " ".join(parsed["issues"]))

    def test_complete_ocr_clock_does_not_request_suspicious_review(self):
        parsed = smart.parse_ocr_fields(
            "北纬 31°20' 东经 120°41' 2026.06.09 10:35",
            args(),
        )
        self.assertEqual(parsed["time"], "2026:06:09 10:35:00")
        self.assertTrue(parsed["time_has_clock"])
        self.assertFalse(parsed["issues"])
        self.assertFalse(smart.needs_model({}, parsed, "suspicious"))

    def test_date_only_qwen_ocr_routes_to_final_visual_review(self):
        reasons = smart.dashscope_result_needs_final_review(
            {
                "ocr_text": (
                    "北纬 31°20' 东经 120°41' 2026.06.09"
                )
            },
            self.model_result(),
            args(),
        )
        self.assertTrue(any("仅识别到日期" in reason for reason in reasons))

    def test_swapped_qwen_coordinates_route_to_review_instead_of_crashing(self):
        result = self.model_result()
        result["latitude_text"] = "120.64845"
        result["longitude_text"] = "31.3234N"
        result["shooting_datetime_text"] = "2026.06.24 16:34"

        reasons = smart.dashscope_result_needs_final_review(
            {"ocr_text": ""},
            result,
            args(),
        )

        self.assertEqual(len(reasons), 2)

    def test_camera_filename_datetime(self):
        value, confidence, source = smart.datetime_from_filename(
            r"D:\photos\IMG_20260626_092119053.jpg"
        )
        self.assertEqual(value, "2026:06:26 09:21:19")
        self.assertGreaterEqual(confidence, 0.9)
        self.assertEqual(source, "camera filename")

    def test_wechat_filename_is_not_high_trust(self):
        value, confidence, source = smart.datetime_from_filename(
            r"D:\photos\微信图片_20260615111626_1553_20.jpg"
        )
        self.assertEqual(value, "2026:06:15 11:16:26")
        self.assertLess(confidence, 0.88)
        self.assertEqual(source, "WeChat filename")

    def test_model_fills_missing_longitude_and_auto_approves(self):
        image_path = os.path.join(
            tempfile.gettempdir(), "hash_without_filename_date.jpg"
        )
        model = {
            "latitude_text": "31°20'",
            "longitude_text": "120°41'",
            "shooting_datetime_text": "2026.06.09",
            "confidence": {
                "latitude": 0.95,
                "longitude": 0.96,
                "shooting_datetime": 0.94,
            },
            "evidence": {
                "latitude": "北纬 31°20′",
                "longitude": "东经 120°41′",
                "shooting_datetime": "2026.06.09",
            },
            "notes": "watermark is legible",
        }
        record = smart.merge_fields(
            image_path,
            {},
            "\u5317\u7eac 31\u00b020 \u4e1c\u7eaa\n720\u00b041 2026.06.09",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_PARSE_STATUS], "AUTO_OK")
        self.assertEqual(record[smart.core.L_REVIEW], smart.core.V_AUTO_APPROVED)
        self.assertEqual(record[smart.core.L_LON], "120.68333333")
        self.assertEqual(record[smart.core.L_TIME], "2026:06:09")

    def test_model_visible_clock_is_kept(self):
        model = self.model_result()
        model["shooting_datetime_text"] = "2026.06.09 14:26"
        model["evidence"]["shooting_datetime"] = "2026.06.09 14:26"
        record = smart.merge_fields(
            r"D:\photos\x.jpg",
            {},
            "北纬 31°20' 东经 120°41' 2026.06.09",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_TIME], "2026:06:09 14:26:00")

    def test_umi_clock_is_preserved_when_model_confirms_date_only(self):
        record = smart.merge_fields(
            r"D:\photos\x.jpg",
            {},
            "北纬 31°20' 东经 120°41' 2026.06.09 09:42",
            self.model_result(),
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_TIME], "2026:06:09 09:42:00")
        self.assertIn("Umi-OCR", record[smart.core.L_SOURCE])

    def test_no_visible_date_stays_empty_despite_timestamp_filename(self):
        model = self.model_result()
        model["shooting_datetime_text"] = None
        model["confidence"]["shooting_datetime"] = 0.0
        model["evidence"]["shooting_datetime"] = ""
        record = smart.merge_fields(
            r"D:\photos\IMG_20260626_092119.jpg",
            {},
            "北纬 31°20' 东经 120°41'",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_TIME], "")
        self.assertIn("shooting date still missing", record[smart.core.L_TIPS])

    def test_model_overrides_conflicting_ocr(self):
        model = {
            "latitude_text": "31.35",
            "longitude_text": "120.75",
            "shooting_datetime_text": "2026.06.10",
            "confidence": {
                "latitude": 0.99,
                "longitude": 0.99,
                "shooting_datetime": 0.99,
            },
            "evidence": {
                "latitude": "31.35",
                "longitude": "120.75",
                "shooting_datetime": "2026.06.10",
            },
            "notes": "",
        }
        record = smart.merge_fields(
            r"D:\photos\x.jpg",
            {},
            "\u5317\u7eac 31\u00b020 \u4e1c\u7ecf 120\u00b041 2026.06.09",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_PARSE_STATUS], "AUTO_OK")
        self.assertEqual(record[smart.core.L_LAT], "31.35000000")
        self.assertEqual(record[smart.core.L_LON], "120.75000000")
        self.assertIn("overrode OCR", record[smart.core.L_TIPS])

    def test_out_of_range_ocr_is_discarded_and_model_candidate_used(self):
        model = {
            "latitude_text": "31°20'",
            "longitude_text": "120°41'",
            "shooting_datetime_text": "2026.06.09",
            "confidence": {
                "latitude": 0.85,
                "longitude": 0.78,
                "shooting_datetime": 0.60,
            },
            "evidence": {
                "latitude": "北纬 31°20'",
                "longitude": "东经 120°41'",
                "shooting_datetime": "2026.06.09",
            },
            "notes": "",
        }
        record = smart.merge_fields(
            r"D:\photos\hash.jpg",
            {},
            "北纬\n31820\n20°41\n2026.06.09\n花茶",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_PARSE_STATUS], "AUTO_OK")
        self.assertEqual(record[smart.core.L_LAT], "31.33333333")
        self.assertEqual(record[smart.core.L_LON], "120.68333333")
        self.assertIn("outside expected range and discarded", record[smart.core.L_TIPS])

    def test_very_low_model_confidence_fills_candidate_but_requires_review(self):
        model = {
            "latitude_text": "31°20'",
            "longitude_text": "120°41'",
            "shooting_datetime_text": "2026.06.09",
            "confidence": {
                "latitude": 0.40,
                "longitude": 0.45,
                "shooting_datetime": 0.50,
            },
            "evidence": {
                "latitude": "北纬 31°20'",
                "longitude": "东经 120°41'",
                "shooting_datetime": "2026.06.09",
            },
            "notes": "",
        }
        record = smart.merge_fields(
            r"D:\photos\hash.jpg",
            {},
            "无法识别",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_PARSE_STATUS], "NEEDS_REVIEW")
        self.assertEqual(record[smart.core.L_REVIEW], smart.core.V_CHECK)
        self.assertEqual(record[smart.core.L_LAT], "31.33333333")
        self.assertEqual(record[smart.core.L_LON], "120.68333333")
        self.assertIn("candidate filled for review", record[smart.core.L_TIPS])

    def test_partial_exif_fields_are_preserved_over_model(self):
        model = {
            "latitude_text": "31.35",
            "longitude_text": "120.75",
            "shooting_datetime_text": "2026.06.09",
            "confidence": {
                "latitude": 0.95,
                "longitude": 0.95,
                "shooting_datetime": 0.95,
            },
            "evidence": {
                "latitude": "31.35",
                "longitude": "120.75",
                "shooting_datetime": "2026.06.09",
            },
            "notes": "",
        }
        record = smart.merge_fields(
            r"D:\photos\x.jpg",
            {"exif_lat": "31.34000000", "exif_lon": "120.70000000"},
            "2026.06.09",
            model,
            "",
            args(),
        )
        self.assertEqual(record[smart.core.L_LAT], "31.34000000")
        self.assertEqual(record[smart.core.L_LON], "120.70000000")
        self.assertEqual(record[smart.core.L_TIME], "2026:06:09")
        self.assertIn("纬度=EXIF", record[smart.core.L_SOURCE])
        self.assertIn("时间=local model", record[smart.core.L_SOURCE])

    def test_writer_only_fills_missing_exif_fields_by_default(self):
        existing = {
            "exif_lat": "31.34000000",
            "exif_lon": "120.70000000",
            "DateTimeOriginal": "",
        }
        plan = smart.core.exif_write_plan(
            existing,
            has_latlon=True,
            has_time=True,
            overwrite_existing=False,
        )
        self.assertFalse(plan["write_latlon"])
        self.assertTrue(plan["write_time"])

        overwrite_plan = smart.core.exif_write_plan(
            existing,
            has_latlon=True,
            has_time=True,
            overwrite_existing=True,
        )
        self.assertTrue(overwrite_plan["write_latlon"])
        self.assertTrue(overwrite_plan["write_time"])

    def test_date_only_review_value_is_not_written_as_fake_midnight(self):
        self.assertFalse(smart.core.review_datetime_has_clock("2026:06:09"))
        self.assertTrue(
            smart.core.review_datetime_has_clock("2026:06:09 08:30:00")
        )


if __name__ == "__main__":
    unittest.main()
