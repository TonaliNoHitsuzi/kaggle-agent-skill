# -*- coding: utf-8 -*-
"""四脚本 CLI 契约自动化测试（SPEC「测试 Seam」落地）。

覆盖纯本地分支，不依赖网络与 Kaggle 凭据，任何环境可跑：

    python -m unittest discover tests -v

契约（SPEC）：退出码 0=健康/成功、1=异常；stdout 末行 `[脚本名] OK/FAIL: 摘要`；
`--json` 输出整体可 json.loads 且含 tool/ok/summary 字段。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def run_py(args, timeout=180):
    p = subprocess.run([sys.executable] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=str(REPO))
    return p.returncode, p.stdout


def last_line(stdout):
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def make_kernel_dir(root, code="print('hi')\n", meta_extra=None, meta_override=None,
                    with_code=True, meta_raw=None):
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    if with_code:
        (d / "kernel.py").write_text(code, encoding="utf-8")
    meta = {"id": "tester/demo", "title": "demo", "code_file": "kernel.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": False, "enable_internet": False}
    if meta_extra:
        meta.update(meta_extra)
    if meta_override:
        meta = meta_override
    text = meta_raw if meta_raw is not None else json.dumps(meta, ensure_ascii=False, indent=2)
    (d / "kernel-metadata.json").write_text(text, encoding="utf-8")
    return d


class KwatchContract(unittest.TestCase):

    def test_missing_dir_fail(self):
        rc, out = run_py([SCRIPTS / "kwatch.py", "-p", "no-such-dir", "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertTrue(last_line(out).startswith("[kwatch] FAIL:"), last_line(out))

    def test_missing_metadata_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "empty"
            d.mkdir()
            rc, out = run_py([SCRIPTS / "kwatch.py", "-p", d, "--dry-run"])
            self.assertEqual(rc, 1)
            self.assertTrue(last_line(out).startswith("[kwatch] FAIL:"), last_line(out))

    def test_bad_json_metadata_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_kernel_dir(Path(tmp) / "bad", meta_raw="{not json")
            rc, out = run_py([SCRIPTS / "kwatch.py", "-p", d, "--dry-run"])
            self.assertEqual(rc, 1)
            self.assertTrue(last_line(out).startswith("[kwatch] FAIL:"), last_line(out))

    def test_missing_code_file_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_kernel_dir(Path(tmp) / "nocode", with_code=False)
            rc, out = run_py([SCRIPTS / "kwatch.py", "-p", d, "--dry-run"])
            self.assertEqual(rc, 1)
            self.assertTrue(last_line(out).startswith("[kwatch] FAIL:"), last_line(out))

    def test_metadata_missing_id_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_kernel_dir(Path(tmp) / "noid", meta_override={"title": "x", "code_file": "kernel.py"})
            rc, out = run_py([SCRIPTS / "kwatch.py", "-p", d, "--dry-run"])
            self.assertEqual(rc, 1)
            self.assertTrue(last_line(out).startswith("[kwatch] FAIL:"), last_line(out))

    def test_dry_run_ok_and_json_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_kernel_dir(Path(tmp) / "ok")
            rc, out = run_py([SCRIPTS / "kwatch.py", "-p", d, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertTrue(last_line(out).startswith("[kwatch] OK:"), last_line(out))
            rc2, out2 = run_py([SCRIPTS / "kwatch.py", "-p", d, "--dry-run", "--json"])
            self.assertEqual(rc2, 0)
            data = json.loads(out2)
            for key in ("tool", "ok", "summary"):
                self.assertIn(key, data)
            self.assertEqual(data["tool"], "kwatch")
            self.assertTrue(data["summary"].startswith("[kwatch] OK:"))

    def test_smoke_without_jobs_fails_at_build(self):
        sys.path.insert(0, str(SCRIPTS))
        import kwatch
        with tempfile.TemporaryDirectory() as tmp:
            d = make_kernel_dir(Path(tmp) / "nojobs")
            meta = json.loads((d / "kernel-metadata.json").read_text(encoding="utf-8"))
            with self.assertRaises(SystemExit):
                kwatch.build_push_copy(d, meta, "abcd1234", smoke=True)


class UploaderContract(unittest.TestCase):

    def test_missing_dataset_metadata_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "ds"
            d.mkdir()
            rc, out = run_py([SCRIPTS / "uploader.py", "-p", d, "--probe-only"])
            self.assertEqual(rc, 1)
            self.assertTrue(last_line(out).startswith("[uploader] FAIL:"), last_line(out))

    def test_metadata_missing_id_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "ds"
            d.mkdir()
            (d / "dataset-metadata.json").write_text('{"title": "x"}', encoding="utf-8")
            rc, out = run_py([SCRIPTS / "uploader.py", "-p", d, "--probe-only"])
            self.assertEqual(rc, 1)
            self.assertTrue(last_line(out).startswith("[uploader] FAIL:"), last_line(out))

    def test_help_zero_exit(self):
        rc, out = run_py([SCRIPTS / "uploader.py", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn("usage", out)


class JsonShapeContract(unittest.TestCase):
    """--json 输出：整体可解析、含契约字段。ok 真值随环境（凭据/网络）变化，不做断言。"""

    def test_doctor_json_shape(self):
        rc, out = run_py([SCRIPTS / "doctor.py", "--json"])
        data = json.loads(out)
        self.assertEqual(data["tool"], "doctor")
        for key in ("ok", "summary", "checks"):
            self.assertIn(key, data)
        self.assertIn(rc, (0, 1))
        human_rc, human_out = run_py([SCRIPTS / "doctor.py"])
        self.assertEqual(human_rc, rc)
        self.assertTrue(last_line(human_out).startswith("[doctor] "))

    def test_quota_json_shape(self):
        rc, out = run_py([SCRIPTS / "quota.py", "--json"])
        data = json.loads(out)
        self.assertEqual(data["tool"], "quota")
        for key in ("ok", "summary"):
            self.assertIn(key, data)
        self.assertIn(rc, (0, 1))
        human_rc, human_out = run_py([SCRIPTS / "quota.py"])
        self.assertEqual(human_rc, rc)
        self.assertTrue(last_line(human_out).startswith("[quota] "))


class KwatchInternals(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        import kwatch
        self.kw = kwatch

    def test_progress_regex_and_filter(self):
        rx = self.kw.PROGRESS_RE
        m = rx.search("x\nPROGRESS 3/6 [b1] job=ab12cd34")
        self.assertEqual((m.group(1), m.group(2), m.group(3), m.group(4)), ("3", "6", "b1", "ab12cd34"))
        m2 = rx.search("PROGRESS 1/2")
        self.assertIsNone(m2.group(4))

    def test_calc_job_id_stable_and_distinct(self):
        src = "JOBS = {'batch': [{'a': 1}]}\n"
        jobs, _ = self.kw.extract_jobs(src)
        self.assertEqual(self.kw.calc_job_id(src, jobs), self.kw.calc_job_id(src, jobs))
        self.assertEqual(self.kw.calc_job_id(src, jobs), self.kw.calc_job_id(src + "\n# comment\n", jobs),
                         "有 JOBS 时指纹只锚定任务配置，源码无关变化不应改变 job_id")
        self.assertNotEqual(self.kw.calc_job_id(src, None), self.kw.calc_job_id(src + "\n", None),
                            "无 JOBS 退化全文哈希时，源文件变化应改变 job_id")

    def test_count_items_seeds_or_one(self):
        jobs = {"batch": [{"seeds": [1, 2, 3]}, {"seeds": [1]}, {"no_seeds": True}]}
        self.assertEqual(self.kw.count_items(jobs), 5)
        self.assertIsNone(self.kw.count_items({"batch": "notalist"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
