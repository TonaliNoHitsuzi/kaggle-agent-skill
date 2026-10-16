#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kaggle-agent uploader：大文件上传 Kaggle Dataset 增强（代理自检 + GCS 308 进度探针 + 断点自动重试）。

用法：
    python scripts/uploader.py -p <dataset目录>               自动判定 create/version 并上传，失败自动重试
    python scripts/uploader.py -p <dataset目录> --probe-only  只诊断（代理连通 + resume 会话扫描），不上传
    python scripts/uploader.py -p <dataset目录> -m "v2"       追加版本时的说明
    python scripts/uploader.py -p <dataset目录> --json        机器可读输出
背景：Kaggle API 域可直连，但 blob 上传走 storage.googleapis.com（大陆被墙），不配代理会静默失败（exit 1 零报错）。
kaggle CLI 自带 GCS resumable 断点续传（resume 状态存 %TEMP%/.kaggle/uploads/*.json），中断不白传；本脚本包住它。
退出码：0 = 上传成功（或 probe-only 诊断全通）；1 = 失败。stdout 末行为一行摘要。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_NAME = "uploader"
GCS_PROBE_URL = "https://storage.googleapis.com/"


def stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def ensure_proxy_env():
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return "env HTTPS_PROXY/HTTP_PROXY"
    settings = Path(__file__).resolve().parent.parent / "config" / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
        proxy = (data.get("proxy") or {}).get("https") or ""
        if proxy:
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["HTTP_PROXY"] = (data.get("proxy") or {}).get("http") or proxy
            return f"config/settings.json（{settings}）"
    except Exception:
        pass
    return "未配置（直连）"


def http_reachable(url, timeout=10):
    proxies = {}
    if os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"):
        proxies = {
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"),
        }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    try:
        resp = opener.open(urllib.request.Request(url, method="HEAD", headers={"User-Agent": "kaggle-agent-uploader"}), timeout=timeout)
        return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def fail(msg):
    print(msg)
    sys.exit(1)


def load_metadata(dataset_dir):
    meta_path = Path(dataset_dir) / "dataset-metadata.json"
    if not meta_path.exists():
        fail(f"[{SCRIPT_NAME}] FAIL: 缺少 {meta_path}（用 kaggle datasets init -p {dataset_dir} 生成模板）")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"[{SCRIPT_NAME}] FAIL: dataset-metadata.json 不是合法 JSON: {exc}")
    if not meta.get("id"):
        fail(f"[{SCRIPT_NAME}] FAIL: dataset-metadata.json 缺字段 id")
    return meta


def scan_payload(dataset_dir):
    items = [p for p in Path(dataset_dir).iterdir() if p.name != "dataset-metadata.json"]
    files = [p for p in items if p.is_file()]
    dirs = [p for p in items if p.is_dir()]
    total = sum(p.stat().st_size for p in files)
    return files, dirs, total


def kaggle(args, timeout=3600):
    exe = shutil.which("kaggle")
    if not exe:
        fail(f"[{SCRIPT_NAME}] FAIL: 未找到 kaggle 命令：pip install kaggle 后重试")
    return subprocess.run([exe] + args, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def resume_files():
    roots = [Path(os.environ.get("TEMP", "")) / ".kaggle" / "uploads", Path.home() / ".kaggle" / "uploads"]
    found = []
    for root in roots:
        if root.is_dir():
            found += [p for p in root.glob("*.json")]
    return sorted(set(found))


def probe_resume_entry(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"file": str(path), "probe": "unreadable"}
    entry = {"file": str(path)}
    url = next((v for k, v in data.items() if isinstance(v, str) and v.startswith("http") and ("upload" in v or "googleapis" in v)), None)
    token = next((v for k, v in data.items() if isinstance(v, str) and k.lower() in ("token", "auth_token")), None)
    size = next((v for k, v in data.items() if isinstance(v, int) and k.lower() in ("size", "total", "total_bytes")), None)
    local = next((v for k, v in data.items() if isinstance(v, str) and Path(v).exists() and v.endswith((".safetensors", ".zip", ".bin", ".ckpt", ".pt", ".gguf"))), None)
    if local and not size:
        size = Path(local).stat().st_size
    entry.update({"url": url, "size": size, "local": local})
    if not (url and size):
        entry["probe"] = "no-session-url"
        return entry
    headers = {"Content-Length": "0", "Content-Range": f"bytes */{size}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    proxies = {}
    if os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"):
        proxies = {
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"),
        }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    try:
        req = urllib.request.Request(url, data=b"", headers=headers, method="PUT")
        resp = opener.open(req, timeout=30)
        entry.update({"probe": f"done HTTP {resp.status}", "uploaded_bytes": size})
    except urllib.error.HTTPError as exc:
        if exc.code == 308:
            rng = exc.headers.get("Range") or ""
            uploaded = int(rng.split("bytes=")[-1].split("-")[-1]) + 1 if rng.startswith("bytes=") and "-" in rng else None
            entry.update({"probe": "308 resumable", "uploaded_bytes": uploaded})
        else:
            entry.update({"probe": f"HTTP {exc.code}"})
    except Exception as exc:
        entry["probe"] = f"{type(exc).__name__}: {exc}"
    return entry


class ProgressWatcher(threading.Thread):
    def __init__(self, stop_event, interval=15, events=None, quiet=False):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.interval = interval
        self.events = events if events is not None else []
        self.quiet = quiet

    def emit(self, line):
        self.events.append(line)
        if not self.quiet:
            print(line, flush=True)

    def run(self):
        while not self.stop_event.wait(self.interval):
            entries = [probe_resume_entry(p) for p in resume_files()]
            usable = [e for e in entries if e.get("uploaded_bytes") is not None and e.get("size")]
            if usable:
                for e in usable:
                    pct = e["uploaded_bytes"] / e["size"] * 100
                    self.emit(f"  probe | {Path(e['file']).name}: {e['uploaded_bytes'] / 1e9:.2f}GB / {e['size'] / 1e9:.2f}GB（{pct:.0f}%，308 实测）")
            else:
                self.emit(f"  probe | resume 会话未出现或暂不可解析（发现 {len(entries)} 个文件）——等待 CLI 建立 GCS 会话")


def decide_create_or_version(ref):
    out = kaggle(["datasets", "status", "-k", ref], timeout=120)
    if out.returncode == 0:
        return "version"
    combined = ((out.stderr or "") + (out.stdout or "")).lower()
    if "403" in combined or "404" in combined or "not found" in combined or "forbidden" in combined:
        return "create"
    return "create"


def run_upload(mode, dataset_dir, message, max_minutes, log, events, quiet):
    cmd = ["datasets", "create", "-p", str(dataset_dir)] if mode == "create" else \
          ["datasets", "version", "-p", str(dataset_dir), "-m", message or "auto upload via kaggle-agent uploader"]
    stop = threading.Event()
    watcher = ProgressWatcher(stop, events=events, quiet=quiet)
    watcher.start()
    t0 = time.time()
    try:
        out = kaggle(cmd, timeout=max_minutes * 60)
    finally:
        stop.set()
        watcher.join(timeout=5)
    combined = ((out.stdout or "") + (out.stderr or ""))
    lines = [ln for ln in combined.splitlines() if ln.strip()]
    for ln in lines[-15:]:
        log("  cli  | " + ln.strip())
    return out.returncode == 0, combined


def main():
    stdout_utf8()
    parser = argparse.ArgumentParser(description="kaggle-agent uploader：大文件上传增强（代理自检+308进度+断点重试）")
    parser.add_argument("-p", "--path", required=True, help="dataset 目录（含 dataset-metadata.json）")
    parser.add_argument("--probe-only", action="store_true", help="只诊断代理与 resume 会话，不上传")
    parser.add_argument("-m", "--message", default="", help="version 模式的版本说明")
    parser.add_argument("--max-retries", type=int, default=5, help="上传失败自动重试次数（默认 5）")
    parser.add_argument("--max-minutes", type=int, default=240, help="单次上传最长分钟数（默认 240）")
    parser.add_argument("--json", action="store_true", help="输出机器可解析 JSON")
    args = parser.parse_args()

    dataset_dir = Path(args.path)
    meta = load_metadata(dataset_dir)
    ref = meta["id"]
    files, dirs, total = scan_payload(dataset_dir)
    events = []

    def log(msg):
        events.append(msg)
        if not args.json:
            print(msg, flush=True)

    result = {"tool": SCRIPT_NAME, "ok": False, "ref": ref,
              "payload_files": len(files), "payload_bytes": total}

    proxy_source = ensure_proxy_env()
    log(f"[{SCRIPT_NAME}] dataset={ref} 载荷={len(files)} 文件/{total / 1e9:.2f}GB 代理={proxy_source}")
    if dirs:
        log("  WARN | 目录含子文件夹，kaggle datasets create 默认 skip 子目录——平铺最省事，要保留结构需 --dir-mode zip")

    ok, info = http_reachable(GCS_PROBE_URL, timeout=10)
    if not ok:
        tail = (f"[{SCRIPT_NAME}] FAIL: storage.googleapis.com 不可达（{info}）——"
                "上传走 googleapis 被墙，先配代理：环境变量 HTTPS_PROXY 或 config/settings.json 的 proxy.https")
        result["error"] = tail
        result["summary"] = tail
        result["log"] = events
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(tail)
        return 1
    log(f"  代理链路通（{info}，来源 {proxy_source}）")

    entries = [probe_resume_entry(p) for p in resume_files()]
    if entries:
        for e in entries:
            shown = {k: e.get(k) for k in ("file", "probe", "uploaded_bytes", "size")}
            log("  resume | " + json.dumps(shown, ensure_ascii=False))
        result["resume_sessions"] = entries
    else:
        log("  resume | 无历史会话（首传或已清）")

    if args.probe_only:
        result["ok"] = True
        tail = f"[{SCRIPT_NAME}] OK: probe-only 诊断完成——代理通，resume 会话 {len(entries)} 个，可正式上传"
        result["summary"] = tail
        result["log"] = events
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(tail)
        return 0

    mode = decide_create_or_version(ref)
    log(f"  模式={mode}（云端已存在则 version 追加版本，否则 create）")
    backoffs = [30, 60, 120, 120, 120]
    attempt, last_err = 0, ""
    while attempt <= args.max_retries:
        attempt += 1
        log(f"  ---- 第 {attempt} 次上传（kaggle 自带断点续传，失败不白传）----")
        ok, last_err = run_upload(mode, dataset_dir, args.message, args.max_minutes, log, events, args.json)
        if ok:
            result["ok"] = True
            result["mode"] = mode
            result["attempts"] = attempt
            tail = f"[{SCRIPT_NAME}] OK: {ref} 上传成功（{mode}，第 {attempt} 次尝试）——状态复核: kaggle datasets status -k {ref}"
            break
        if attempt <= args.max_retries:
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            log(f"  失败，{wait}s 后自动重试（resume 会话已保存，将续传）")
            time.sleep(wait)
    else:
        result["error"] = last_err[-500:]
        tail = f"[{SCRIPT_NAME}] FAIL: {args.max_retries + 1} 次尝试均失败——跑 --probe-only 复查代理；resume 状态仍在，网络恢复后重跑本脚本续传"

    result["summary"] = tail
    result["log"] = events
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(tail)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
