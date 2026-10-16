#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kaggle-agent kwatch：一键 push kernel → 轮询状态 → 完成后自动拉取 output。

用法：
    python scripts/kwatch.py -p <kernel目录>                    push 并守到完成
    python scripts/kwatch.py -p <kernel目录> --watch-only       已 push 过，只盯状态+拉结果
    python scripts/kwatch.py -p <kernel目录> --dry-run          校验配置与计划，不实际提交
    python scripts/kwatch.py -p <kernel目录> --json             机器可读输出（最终快照）
状态机：QUEUED/RUNNING/UNKNOWN → 继续等；COMPLETE → 拉 output；ERROR/CANCEL → 拉 logs 后退出 1。
退出码：0 = COMPLETE 且 output 拉取完成（或 --dry-run 通过）；1 = 失败。stdout 末行为一行摘要。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_NAME = "kwatch"
TERMINAL_DEAD = ("ERROR", "CANCEL")
ACTIVE = ("UNKNOWN", "QUEUED", "RUNNING")


def stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def fail(msg):
    print(msg)
    sys.exit(1)


def kaggle(args, timeout=300):
    exe = shutil.which("kaggle")
    if not exe:
        fail("[kwatch] FAIL: 未找到 kaggle 命令：pip install kaggle 后重试")
    return subprocess.run([exe] + args, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def load_metadata(kernel_dir):
    meta_path = Path(kernel_dir) / "kernel-metadata.json"
    if not meta_path.exists():
        fail(f"[{SCRIPT_NAME}] FAIL: 缺少 {meta_path}（用 kaggle kernels init -p {kernel_dir} 生成模板）")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"[{SCRIPT_NAME}] FAIL: kernel-metadata.json 不是合法 JSON: {exc}")
    for key in ("id", "code_file"):
        if not meta.get(key):
            fail(f"[{SCRIPT_NAME}] FAIL: kernel-metadata.json 缺字段 {key}")
    code = Path(kernel_dir) / meta["code_file"]
    if not code.exists():
        fail(f"[{SCRIPT_NAME}] FAIL: code_file 指向的 {code} 不存在")
    return meta


def parse_status(text):
    m = re.search(r'KernelWorkerStatus\.(\w+)', text or "")
    if m:
        return m.group(1)
    m = re.search(r'status[":\s]+([A-Za-z_]+)', text or "", re.IGNORECASE)
    return m.group(1).upper() if m else ""


def push_kernel(kernel_dir):
    out = kaggle(["kernels", "push", "-p", str(kernel_dir)], timeout=600)
    combined = (out.stdout or "") + (out.stderr or "")
    useful = [ln for ln in combined.splitlines() if ln.strip() and "Warning" not in ln]
    for ln in useful:
        print("  push |", ln.strip())
    if out.returncode != 0:
        print(combined.strip()[-1500:])
        return False, "kaggle kernels push 退出码非 0（上方为原始输出）"
    return True, ""


def fetch_output(ref, out_dir):
    out = kaggle(["kernels", "output", "-k", ref, "-p", str(out_dir)], timeout=600)
    if out.returncode != 0:
        return False, (out.stderr or out.stdout or "").strip()[:300]
    files = sorted(p.name for p in Path(out_dir).rglob("*") if p.is_file())
    return True, files


def fetch_logs_tail(ref, lines=40):
    out = kaggle(["kernels", "logs", "-k", ref], timeout=300)
    text = (out.stdout or "") + (out.stderr or "")
    tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return tail


def main():
    stdout_utf8()
    parser = argparse.ArgumentParser(description="kaggle-agent kwatch：push → 轮询 → 拉 output")
    parser.add_argument("-p", "--path", required=True, help="kernel 目录（含 kernel-metadata.json）")
    parser.add_argument("--watch-only", action="store_true", help="不 push，只轮询既有 kernel")
    parser.add_argument("--dry-run", action="store_true", help="校验配置并打印计划，不提交")
    parser.add_argument("--timeout-min", type=int, default=360, help="最长守候分钟数（默认 360）")
    parser.add_argument("--interval-start", type=int, default=15, help="轮询起始间隔秒（默认 15）")
    parser.add_argument("--interval-max", type=int, default=120, help="轮询间隔封顶秒（默认 120）")
    parser.add_argument("--json", action="store_true", help="输出机器可解析 JSON（最终快照）")
    args = parser.parse_args()

    kernel_dir = Path(args.path)
    meta = load_metadata(kernel_dir)
    ref = meta["id"]
    gpu = bool(meta.get("enable_gpu"))
    result = {"tool": SCRIPT_NAME, "ok": False, "ref": ref, "status": None,
              "elapsed_min": None, "output_dir": None, "output_files": None}

    plan = [
        f"kaggle kernels push -p {kernel_dir}" if not args.watch_only else "(跳过 push，watch-only)",
        f"kaggle kernels status -k {ref}  # 轮询至终态，间隔 {args.interval_start}s→{args.interval_max}s",
        f"kaggle kernels output -k {ref} -p {kernel_dir / 'output'}",
    ]
    result["plan"] = plan
    if not args.json:
        print(f"[{SCRIPT_NAME}] 计划 kernel={ref} gpu={gpu}")
        for ln in plan:
            print("  ", ln)
    if args.dry_run:
        result["ok"] = True
        tail = f"[{SCRIPT_NAME}] OK: dry-run 校验通过（metadata/code_file 就绪，命令计划见上）"
        result["summary"] = tail
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(tail)
        return 0

    t0 = time.time()
    if not args.watch_only:
        ok, err = push_kernel(kernel_dir)
        if not ok:
            tail = f"[{SCRIPT_NAME}] FAIL: {err}"
            result["error"] = err
            result["summary"] = tail
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(tail)
            return 1
        print(f"  push 完成，等 20s 让 slug 生效（刚 push 的 status 可能暂时报 Permission denied）")
        time.sleep(20)

    interval = args.interval_start
    status, consecutive_errors, last_printed = "", 0, ""
    deadline = t0 + args.timeout_min * 60
    while time.time() < deadline:
        out = kaggle(["kernels", "status", "-k", ref], timeout=120)
        status = parse_status((out.stdout or "") + (out.stderr or ""))
        if out.returncode != 0 and not status:
            consecutive_errors += 1
            if consecutive_errors >= 8:
                tail = f"[{SCRIPT_NAME}] FAIL: status 连续 8 次失败，放弃（最后输出: {(out.stderr or out.stdout or '').strip()[:200]}）"
                result["error"] = tail
                break
            time.sleep(min(interval, 30))
            continue
        consecutive_errors = 0
        elapsed = (time.time() - t0) / 60
        if status != last_printed:
            print(f"  {time.strftime('%H:%M:%S')} status={status or '?'} elapsed={elapsed:.1f}min interval={interval}s")
            last_printed = status
        if status == "COMPLETE":
            out_dir = kernel_dir / "output"
            ok, payload = fetch_output(ref, out_dir)
            result["status"] = status
            result["elapsed_min"] = round(elapsed, 1)
            result["output_dir"] = str(out_dir)
            if ok:
                result["ok"] = True
                result["output_files"] = payload
                tail = (f"[{SCRIPT_NAME}] OK: {ref} COMPLETE in {elapsed:.1f}min，"
                        f"output → {out_dir}（{len(payload)} 个文件）")
            else:
                result["ok"] = True
                result["output_files"] = []
                tail = (f"[{SCRIPT_NAME}] OK: {ref} COMPLETE in {elapsed:.1f}min（output 拉取失败为 WARN：{payload}）")
            break
        if any(d in status for d in TERMINAL_DEAD):
            result["status"] = status
            result["elapsed_min"] = round(elapsed, 1)
            logs = fetch_logs_tail(ref)
            print("  ---- logs tail ----")
            for ln in logs:
                print("   ", ln)
            tail = f"[{SCRIPT_NAME}] FAIL: {ref} 终态 {status}（日志尾部见上，完整日志: kaggle kernels logs -k {ref}）"
            result["error"] = tail
            break
        if status not in ACTIVE:
            print(f"  未知状态 {status!r}，继续轮询")
        time.sleep(interval)
        interval = min(interval + 15, args.interval_max)
    else:
        tail = f"[{SCRIPT_NAME}] FAIL: 守候超时（{args.timeout_min}min），最后状态 {status or '?'}——可加 --timeout-min 或改用 --watch-only 续盯"
        result["status"] = status or None
        result["error"] = tail

    result["summary"] = tail
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(tail)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
