#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kaggle-agent quota：查询本周 GPU/TPU 加速器配额余量（封装 kaggle quota，需 CLI 2.x）。

用法：
    python scripts/quota.py          表格输出 + 末行摘要
    python scripts/quota.py --json   机器可读输出
退出码：0 = 查询成功；1 = 失败（无凭据 / CLI 版本过低 / 网络）。stdout 末行为一行摘要。
"""
import argparse
import json
import shutil
import subprocess
import sys

SCRIPT_NAME = "quota"


def stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def run_kaggle_quota():
    exe = shutil.which("kaggle")
    if not exe:
        return None, 1, "", "未找到 kaggle 命令：pip install kaggle 后重试"
    try:
        out = subprocess.run([exe, "quota", "--format", "json"], capture_output=True, text=True,
                             timeout=60, encoding="utf-8", errors="replace")
    except Exception as exc:
        return None, 1, "", f"{type(exc).__name__}: {exc}"
    if out.returncode == 0 and out.stdout.strip():
        try:
            return json.loads(out.stdout), 0, out.stdout, ""
        except json.JSONDecodeError:
            return None, 1, out.stdout, "kaggle quota 输出不是合法 JSON（尝试升级 kaggle CLI）"
    stderr = (out.stderr or "").strip()
    hint = stderr or (out.stdout or "").strip() or f"kaggle quota 退出码 {out.returncode}"
    low = hint.lower()
    if "invalid choice" in low or "unrecognized" in low or ("usage:" in low and "quota" in low):
        hint = f"kaggle CLI 版本过低，quota 子命令需 2.x：pip install -U kaggle（原始输出: {hint[:120]}）"
    if "401" in hint or "unauthorized" in hint.lower() or "credential" in hint.lower() or "authentication" in hint.lower():
        hint = f"认证失败：先跑 python scripts/doctor.py 检查凭据（原始信息: {hint[:120]}）"
    return None, 1, "", hint


def main():
    stdout_utf8()
    parser = argparse.ArgumentParser(description="kaggle-agent 配额查询（GPU/TPU 周余量）")
    parser.add_argument("--json", action="store_true", help="输出机器可解析 JSON")
    args = parser.parse_args()

    data, code, raw, err = run_kaggle_quota()
    if data is None:
        tail = f"[{SCRIPT_NAME}] FAIL: {err}"
        if args.json:
            print(json.dumps({"tool": SCRIPT_NAME, "ok": False, "error": err, "summary": tail}, ensure_ascii=False, indent=2))
        else:
            print(tail)
        return 1

    gpu = next((r for r in data if r.get("resource") == "GPU"), None)
    tpu = next((r for r in data if r.get("resource") == "TPU"), None)
    parts = []
    if gpu:
        parts.append(f"GPU 余 {gpu.get('remaining')}/{gpu.get('total')}")
    if tpu:
        parts.append(f"TPU 余 {tpu.get('remaining')}/{tpu.get('total')}")
    refresh = (gpu or data[0] or {}).get("refreshAt", "")
    tail = f"[{SCRIPT_NAME}] OK: " + "，".join(parts) + (f"，refreshAt {refresh}" if refresh else "")

    if args.json:
        print(json.dumps({"tool": SCRIPT_NAME, "ok": True, "quota": data, "summary": tail}, ensure_ascii=False, indent=2))
        return 0
    headers = ["resource", "used", "remaining", "total", "refreshAt"]
    widths = [max(len(h), *(len(str(r.get(h, ""))) for r in data)) for h in headers]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in data:
        print("  ".join(str(r.get(h, "")).ljust(w) for h, w in zip(headers, widths)))
    print(tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
