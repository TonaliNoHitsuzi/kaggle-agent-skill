#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kaggle-agent doctor：环境自检。检查 Python / kaggle CLI / 凭据 / API 连通 / GCS 上传域连通 / GPU 配额。

用法：
    python scripts/doctor.py          人类可读输出
    python scripts/doctor.py --json   机器可读输出
退出码：0 = 无 FAIL 项（WARN 允许）；1 = 存在 FAIL 项。stdout 末行为一行摘要。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_NAME = "doctor"


def stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def load_proxy_from_settings():
    for candidate in (Path(__file__).resolve().parent.parent / "config" / "settings.json",):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            proxy = (data.get("proxy") or {}).get("https") or ""
            if proxy:
                http_proxy = (data.get("proxy") or {}).get("http") or proxy
                return proxy, http_proxy, str(candidate)
        except Exception:
            continue
    return "", "", ""


def ensure_proxy_env():
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return "env HTTPS_PROXY/HTTP_PROXY"
    proxy, http_proxy, where = load_proxy_from_settings()
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"] = http_proxy
        return f"config/settings.json（{where}）"
    return "未配置（直连）"


def run_check(name):
    def deco(fn):
        def wrapped(checks, ctx):
            try:
                status, detail = fn(ctx)
            except Exception as exc:
                status, detail = "FAIL", f"检查自身异常: {exc!r}"
            checks.append({"name": name, "status": status, "detail": detail})
            return status != "FAIL"
        return wrapped
    return deco


@run_check("python")
def check_python(ctx):
    v = sys.version_info
    if v >= (3, 8):
        return "PASS", f"{v.major}.{v.minor}.{v.micro}"
    return "FAIL", f"{v.major}.{v.minor}.{v.micro}（需要 >= 3.8）"


def kaggle_cli_version():
    exe = shutil.which("kaggle")
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=30, encoding="utf-8", errors="replace")
        m = re.search(r"(\d+)\.(\d+)", out.stdout or "")
        if m:
            return exe, (int(m.group(1)), int(m.group(2)))
        return exe, None
    except Exception:
        return exe, None


@run_check("kaggle-cli")
def check_cli(ctx):
    exe, ver = ctx["cli"]
    if not exe:
        return "FAIL", "未找到 kaggle 命令：pip install kaggle 后重试"
    if ver is None:
        return "WARN", f"已安装（{exe}）但版本解析失败，quota 子命令需 2.x"
    if ver >= (2, 0):
        return "PASS", f"kaggle CLI {ver[0]}.{ver[1]}（{exe}）"
    return "WARN", f"kaggle CLI {ver[0]}.{ver[1]} 为 1.x，建议 pip install -U kaggle（quota 子命令需 2.x）"


def find_credentials():
    kaggle_dir = Path(os.environ.get("KAGGLE_CONFIG_DIR") or Path.home() / ".kaggle")
    kaggle_json = kaggle_dir / "kaggle.json"
    access_token = kaggle_dir / "access_token"
    found = []
    if kaggle_json.exists():
        found.append(f"kaggle.json（{kaggle_dir}）")
    if access_token.exists():
        found.append(f"access_token/2.x OAuth（{kaggle_dir}）")
    return found, kaggle_json


@run_check("credentials")
def check_credentials(ctx):
    found, kaggle_json = ctx["creds"]
    if not found:
        hint = "在 Kaggle 网站 Settings → API → Create New Token 下载 kaggle.json，放到 ~/.kaggle/kaggle.json"
        return "FAIL", f"未找到凭据（找过 {kaggle_json.parent}）。{hint}"
    detail = "；".join(found)
    if "kaggle.json" in detail and os.name == "posix":
        mode = oct(kaggle_json.stat().st_mode & 0o777)
        if kaggle_json.stat().st_mode & 0o077:
            detail += f"（警告：权限 {mode} 过宽，建议 chmod 600）"
    return "PASS", detail


def http_reachable(url, timeout=10):
    proxy_handler = None
    if os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"):
        proxy_handler = urllib.request.ProxyHandler({
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"),
        })
    opener = urllib.request.build_opener(proxy_handler) if proxy_handler else urllib.request.build_opener()
    try:
        resp = opener.open(urllib.request.Request(url, method="HEAD", headers={"User-Agent": "kaggle-agent-doctor"}), timeout=timeout)
        return True, f"HTTP {resp.status}", ""
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}", ""
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"


@run_check("api-connect")
def check_api(ctx):
    ok, info, err = http_reachable("https://www.kaggle.com/", timeout=10)
    if ok:
        return "PASS", f"www.kaggle.com 可达（{info}）"
    return "FAIL", f"www.kaggle.com 不可达（{err}）——API 域通常可直连，检查整体网络"


@run_check("gcs-connect")
def check_gcs(ctx):
    via = ctx["proxy_source"]
    ok, info, err = http_reachable("https://storage.googleapis.com/", timeout=10)
    if ok:
        return "PASS", f"storage.googleapis.com 可达（{info}，代理: {via}）——大文件上传链路通"
    return ("FAIL", f"storage.googleapis.com 不可达（{err}，代理: {via}）——"
            "上传域被墙，必须配置代理（HTTPS_PROXY 或 config/settings.json 的 proxy.https）")


def query_quota_json():
    exe, ver = kaggle_cli_version()
    if not exe or not ver or ver < (2, 0):
        return None, "quota 子命令需要 kaggle CLI 2.x"
    try:
        out = subprocess.run([exe, "quota", "--format", "json"], capture_output=True, text=True,
                             timeout=60, encoding="utf-8", errors="replace")
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout), ""
        return None, (out.stderr or out.stdout or "kaggle quota 非零退出").strip()[:200]
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


@run_check("gpu-quota")
def check_quota(ctx):
    found, _ = ctx["creds"]
    if not found:
        return "WARN", "无凭据，跳过配额查询（先解决 credentials 项）"
    data, err = query_quota_json()
    if data:
        gpu = next((r for r in data if r.get("resource") == "GPU"), None)
        if gpu:
            return "PASS", f"GPU 余量 {gpu.get('remaining')}/{gpu.get('total')}，refreshAt {gpu.get('refreshAt')}"
        return "PASS", f"已获取配额：{json.dumps(data, ensure_ascii=False)[:160]}"
    return "WARN", f"配额查询失败（{err}）——网络/凭据问题解决后可单独跑 scripts/quota.py"


def main():
    stdout_utf8()
    parser = argparse.ArgumentParser(description="kaggle-agent 环境自检（CLI/key/代理/GCS/配额）")
    parser.add_argument("--json", action="store_true", help="输出机器可解析 JSON")
    args = parser.parse_args()

    proxy_source = ensure_proxy_env()
    ctx = {
        "proxy_source": proxy_source or "未配置（直连）",
        "cli": kaggle_cli_version(),
        "creds": find_credentials(),
    }

    checks = []
    check_python(checks, ctx)
    check_cli(checks, ctx)
    check_credentials(checks, ctx)
    check_api(checks, ctx)
    check_gcs(checks, ctx)
    check_quota(checks, ctx)

    fails = [c["name"] for c in checks if c["status"] == "FAIL"]
    warns = [c["name"] for c in checks if c["status"] == "WARN"]
    quota_pass = next((c for c in checks if c["name"] == "gpu-quota" and c["status"] == "PASS"), None)
    tail = ""
    if not fails:
        extra = quota_pass["detail"] if quota_pass else ""
        tail = f"[{SCRIPT_NAME}] OK: {len(checks) - len(warns)}/{len(checks)} 项通过" + (f"；{extra}" if extra else "")
    else:
        tail = f"[{SCRIPT_NAME}] FAIL: {len(fails)} 项未过（{', '.join(fails)}）"

    if args.json:
        print(json.dumps({
            "tool": SCRIPT_NAME, "ok": not fails,
            "proxy_source": ctx["proxy_source"], "checks": checks, "summary": tail,
        }, ensure_ascii=False, indent=2))
    else:
        icons = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}
        for c in checks:
            print(f"[{icons[c['status']]:4}] {c['name']}: {c['detail']}")
        print(tail)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
