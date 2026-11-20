import importlib.util
import io
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


radar = load_script("generate-radar-report")
runner = load_script("run-nightly-deepseek-report")
sync = load_script("sync-knowledge-from-report")
mailer = load_script("send-daily-report")


def good_card(keyword="Topic"):
    return {
        "keyword": keyword,
        "intro": "A concrete explanation of the topic. " * 8,
        "recent_news": "There are no reliable recent developments.",
        "relevance": "Keep the note for the next meeting.",
        "next_step": "Read the original note.",
    }


def empty_sources(web=False):
    return {"config": {"enable_web_search": web}, "text_sources": [], "search_notes": []}


class ReportTests(unittest.TestCase):
    def test_quiet_day_with_no_topics_is_valid(self):
        brief = {"summary": "A quiet day.", "knowledge_cards": []}
        sources = empty_sources(web=True)
        tex = radar.render_report_from_brief("2026-07-27", brief, sources)
        quality = radar.quality_check_simple(tex, [], sources, brief, ["knowledge/topic.md"])
        self.assertTrue(quality["passed"], quality["issues"])

    def test_wrong_keyword_is_rejected_even_with_correct_card_count(self):
        brief = {"summary": "Daily summary.", "knowledge_cards": [good_card("Unexpected")]}
        sources = empty_sources()
        tex = radar.render_report_from_brief("2026-07-27", brief, sources)
        quality = radar.quality_check_simple(
            tex, ["Expected"], sources, brief, ["knowledge/topic.md"]
        )
        self.assertFalse(quality["passed"])

    def test_reference_metadata_comes_from_collected_sources(self):
        brief = {"reference_sources": [{"id": "S1", "url": "https://example.org/wrong"}, {"id": "S9"}]}
        sources = {"text_sources": [{"id": "S1", "url": "https://example.org/original"}]}
        self.assertEqual(radar.brief_reference_sources(brief, sources), sources["text_sources"])

    def test_duplicate_keyword_targets_keep_each_card_and_all_issues(self):
        brief = {"summary": "Daily summary.", "knowledge_cards": [good_card(), {"keyword": "Topic", "recent_news": "[S1] [S2]"}]}
        sources = empty_sources()
        tex = radar.render_report_from_brief("2026-07-27", brief, sources)
        quality = radar.quality_check_simple(tex, ["Topic", "Topic"], sources, brief)
        targets = runner.repair_targets_from_issues(brief, quality["issues"], quality["field_issues"])
        by_path = {target["path"]: target["issues"] for target in targets}
        self.assertEqual(set(by_path), {f"knowledge_cards[1].{field}" for field in runner.CARD_REPAIR_FIELDS})
        self.assertGreater(len(by_path["knowledge_cards[1].recent_news"]), 1)
        replacement = {"path": "knowledge_cards[1].intro", "value": good_card()["intro"]}
        repaired = runner.apply_repair_patches(brief, targets, [replacement])
        self.assertEqual(repaired["knowledge_cards"][0], brief["knowledge_cards"][0])
        self.assertEqual(repaired["knowledge_cards"][1]["intro"], replacement["value"])
        with self.assertRaises(runner.RunnerError):
            runner.apply_repair_patches(brief, targets, [{"path": "summary", "value": "A different summary."}])

    def test_incomplete_http_read_retries(self):
        config = runner.DeepSeekConfig("test", "test", "https://example.org", "model", "enabled", "max", 100, 1, 1, 2, "test")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [
            IncompleteRead(b"x"),
            b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}',
        ]
        with patch.object(runner.urllib.request, "urlopen", return_value=response) as request:
            with patch.object(runner.time, "sleep"), patch.object(runner, "log"):
                self.assertEqual(runner.call_deepseek(config, []), ("{}", {}))
        self.assertEqual(request.call_count, 2)

    def test_dry_run_leaves_report_directory_absent(self):
        args = SimpleNamespace(date="2026-07-27", dry_run=True)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(runner, "REPORT_ROOT", Path(temp)), patch.object(runner, "log"):
                with patch.object(runner, "load_config", return_value=SimpleNamespace(api_key_source="test")):
                    self.assertEqual(runner.run(args), 0)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_runner_reaches_collection_after_acquiring_its_lock(self):
        args = SimpleNamespace(date="2026-07-27", dry_run=False, enforce_window=False, force=False, mock_brief="fixture.json")
        config = runner.DeepSeekConfig("", "test", "https://example.org", "model", "enabled", "max", 100, 1, 1, 2, "test")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(runner, "REPORT_ROOT", root / "reports"), patch.object(runner, "LOCK_DIR", root / "locks"):
                with patch.object(runner, "load_config", return_value=config), patch.object(runner, "log"):
                    with patch.object(runner, "run_command", side_effect=runner.RunnerError("test collection stopped")) as collect:
                        self.assertEqual(runner.run(args), 1)
            self.assertIn("--collect-only", collect.call_args.args[0])
            self.assertEqual(list((root / "locks").iterdir()), [])

    def test_only_compiled_reports_publish_candidates(self):
        brief = {"summary": "Summary.", "knowledge_cards": [good_card()], "memory_candidates": ["A candidate."]}
        context = {"inputs": [{"keyword": "Topic", "weight": "3"}], "local_knowledge": {"scanned_paths": ["node.md"]}}
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            radar.write_report_json(folder / "report_brief.json", brief)
            with patch.object(radar, "compile_pdf", return_value=(False, "Compile failed.")) as compile_pdf:
                with patch.object(radar, "sync_review_queue_from_brief") as publish, patch("builtins.print"):
                    for no_compile, expected_exit in [(True, 0), (False, 1)]:
                        status = radar.render_saved_report("2026-07-27", folder, {}, context, empty_sources(), brief, no_compile)
                        self.assertEqual(status, expected_exit)
                        quality = json.loads((folder / "quality_check.json").read_text(encoding="utf-8"))
                        self.assertFalse(quality["pdf_ready"])
                    self.assertEqual(compile_pdf.call_count, 1)
                    publish.assert_not_called()

    def test_review_append_tolerates_existing_damaged_jsonl(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            queue = folder / "2026-07-27.jsonl"
            queue.write_text("broken\n", encoding="utf-8")
            brief = {"memory_candidates": [{"text": "A candidate.", "kind": "task_candidate"}]}
            with patch.object(radar, "REVIEW_QUEUE_DIR", folder):
                radar.sync_review_queue_from_brief("2026-07-27", brief)
                radar.sync_review_queue_from_brief("2026-07-27", brief)
            rows = queue.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 2)
            self.assertEqual(json.loads(rows[1])["kind"], "memory_candidate")

    def test_weight_review_requires_an_existing_node_and_updates_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            node = root / "knowledge" / "example.md"
            node.parent.mkdir()
            node.write_text("# Example\n\nWeight: 3\n", encoding="utf-8")
            decisions = root / "tracking" / "weight_decisions.jsonl"
            value = [
                {"text": "No target", "suggested_weight": "4"},
                {"text": "Raise the weight", "path": "knowledge/example.md", "suggested_weight": "4"},
                {"text": "No change", "path": "knowledge/example.md", "suggested_weight": "3"},
            ]
            with patch.object(radar, "ROOT", root):
                candidates = radar.normalize_candidate_items(value, "weight_change_candidate", "2026-07-27")
            self.assertEqual(len(candidates), 1)
            with patch.object(app, "ROOT", root), patch.object(app, "WEIGHT_DECISIONS", decisions):
                written = app.materialize_review_acceptance(candidates[0])
            self.assertIn("knowledge/example.md", written)
            self.assertIn("Weight: 4", node.read_text(encoding="utf-8"))
            self.assertEqual(app.read_jsonl(decisions)[0]["payload"]["new_weight"], "4")


class StorageTests(unittest.TestCase):
    def test_blank_metadata_does_not_consume_next_field(self):
        node = "# Topic\n\nAliases:\nRelated: Another topic\n"
        self.assertEqual(sync.field_value(node, "Aliases"), "")
        self.assertIn("Related: Another topic", sync.replace_field(node, "Aliases", "Alias"))

    def test_knowledge_slug_collision_preserves_both_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(sync, "ROOT", root), patch.object(sync, "KNOWLEDGE_DIR", root / "knowledge"):
                brief = {"knowledge_cards": [good_card("C++"), good_card("C#")]}
                created, updated, _ = sync.sync_knowledge_cards(brief, {}, "2026-07-27", False)
                self.assertEqual(len(created), 2)
                self.assertEqual(updated, [])
                titles = {path.read_text(encoding="utf-8").splitlines()[0] for path in (root / "knowledge").glob("*.md")}
                self.assertEqual(titles, {"# C++", "# C#"})

    def test_sync_refuses_failed_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "2026-07-27"
            folder.mkdir()
            (folder / "report_brief.json").write_text(json.dumps({"knowledge_cards": [good_card()]}))
            (folder / "report_context.json").write_text("{}")
            (folder / "quality_check.json").write_text('{"passed":false}')
            (folder / "report.pdf").touch()
            with patch.object(sync, "ROOT", root), patch.object(sync, "REPORT_ROOT", root):
                with patch.object(sync, "KNOWLEDGE_DIR", root / "knowledge"):
                    with self.assertRaises(ValueError):
                        sync.sync("2026-07-27", False)
            self.assertFalse((root / "knowledge").exists())

    def test_failed_pdf_and_debug_tex_are_not_mail_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "2026-07-27"
            folder.mkdir()
            (folder / "report.tex").touch()
            (folder / "quality_check.json").write_text('{"passed":false}')
            with patch.object(mailer, "REPORT_DIR", root):
                self.assertEqual(mailer.report_artifact_for(folder.name)[1], "missing")
                (folder / "report.pdf").touch()
                self.assertEqual(mailer.report_artifact_for(folder.name)[1], "missing")

    def test_uncompiled_revision_does_not_publish_old_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "report.pdf").write_bytes(b"%PDF-1.7")
            quality = folder / "quality_check.json"
            quality.write_text('{"passed":true,"pdf_ready":false}', encoding="utf-8")
            self.assertFalse(mailer.report_pdf_ready(folder))
            quality.write_text('{"passed":true,"pdf_ready":true}', encoding="utf-8")
            self.assertTrue(mailer.report_pdf_ready(folder))
            (folder / "report.tex").write_text("TeX", encoding="utf-8")
            brief = folder / "report_brief.json"
            brief.write_text("{}", encoding="utf-8")
            quality.write_text(json.dumps({
                "passed": True, "pdf_ready": True, "report_fingerprint": radar.report_fingerprint(folder),
            }), encoding="utf-8")
            self.assertTrue(mailer.report_pdf_ready(folder))
            brief.write_text('{"summary":"Changed"}', encoding="utf-8")
            self.assertFalse(mailer.report_pdf_ready(folder))


class WebTests(unittest.TestCase):
    def test_invalid_http_body_returns_400(self):
        for body, length in [(b"[]", "2"), (b"\xff", "1"), (b"", "-1"), (b"", "invalid"), (b"", "3")]:
            with self.subTest(body=body, length=length):
                handler = SimpleNamespace(headers={"Content-Length": length}, rfile=io.BytesIO(body), send_json=Mock())
                self.assertIsNone(app.IdeaSproutHandler.read_json_body(handler))
                self.assertEqual(handler.send_json.call_args.args[1], 400)

    def test_markdown_bootstrap_preserves_colons_in_values(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "2026-07-27.md"
            source.write_text("- \u5173\u952e\u8bcd: Topic\uff1a detail\n", encoding="utf-8")
            self.assertEqual(app.markdown_keyword_records(source)[0]["keyword"], "Topic\uff1a detail")

    def test_parallel_confirmation_and_partial_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = root / "review_queue"
            queue.mkdir()
            preferences = root / "preferences.jsonl"
            day = queue / "2026-07-27.jsonl"
            app.append_jsonl(day, {"kind": "memory_candidate", "id": "candidate-01", "payload": {
                "text": "Original", "target": "themes", "source_report": "original.json",
            }})
            from scripts import context_builder

            with patch.object(app, "ROOT", root), patch.object(app, "MEMORY_PREFERENCES", preferences):
                with patch.object(context_builder, "ROOT", root), patch.object(context_builder, "REVIEW_QUEUE_DIR", queue):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        accept = lambda _: app.decide_review("candidate-01", "accept", {"text": "Edited"})
                        results = list(pool.map(accept, range(2)))
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(len(app.read_jsonl(day)), 2)
            events = app.read_jsonl(preferences)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["text"], "Edited")
            self.assertEqual(events[0]["payload"]["target"], "themes")
            self.assertEqual(events[0]["payload"]["source_report"], "original.json")


if __name__ == "__main__":
    unittest.main()
