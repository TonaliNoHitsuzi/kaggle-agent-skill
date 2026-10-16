#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kaggle-agent kwatch v2：预检三连 → push → 心跳轮询 → 拉 output 到时间戳目录。

用法：
    python scripts/kwatch.py -p <kernel目录>                    预检(估时/查余额) + push + 守到完成
    python scripts/kwatch.py -p <kernel目录> --smoke            冒烟：batch 缩到前 2 项，临时 -smoke id
    python scripts/kwatch.py -p <kernel目录> --watch-only       已 push 过，只盯状态+拉结果
    python scripts/kwatch.py -p <kernel目录> --dry-run          预检+校验+打印计划，不提交
    python scripts/kwatch.py -p <kernel目录> --json             机器可读输出（最终快照）

v2 特性（ENHANCEMENTS-v2）：
- 预检三连：估时（JOBS 结构：加载秒数 + N×单项秒数；缺省值取自 SDXL@T4 生图案例实测，非生图负载务必覆盖
  --load-sec/--per-item-sec）→ GPU 余量警告（不阻塞）→ 冒烟先行
- JOB_ID 静默注入：sha1(JOBS 配置)[:8]（无 JOBS 退化 sha1 源文件全文），经推送副本注入 kernel 常量，源码零污染
- 任务项级心跳：轮询期解析 kernel 日志 `PROGRESS n/N [tag] job=<id>`，job 匹配才显示进度与剩余估算
  （防上一版本日志误报）；心跳由 kernel 侧自愿打印，kwatch 对无心跳 kernel 照常工作
- 结果落 out/YYYYMMDD-HHMM_<jobid>/；每 10 次成功运行提示一次 out/ 累积大小（维护，不自动删）

退出码：0 = COMPLETE 且 output 拉取完成（或 --dry-run 通过）；1 = 失败。stdout 末行为一行摘要（--json 时为纯 JSON）。
"""
import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_NAME = "kwatch"
TERMINAL_DEAD = ("ERROR", "CANCEL")
ACTIVE = ("UNKNOWN", "QUEUED", "RUNNING")
PROGRESS_RE = re.compile(r"PROGRESS\s+(\d+)\s*/\s*(\d+)(?:\s*\[([^\]]*)\])?(?:\s*job=([0-9a-zA-Z_-]+))?")
DEFAULT_LOAD_SEC = 77
DEFAULT_PER_ITEM_SEC = 24.5
MAINTAIN_EVERY = 10
STATE_FILE = Path.home() / ".kaggle" / "agent_state.json"


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
        fail(f"[{SCRIPT_NAME}] FAIL: 未找到 kaggle 命令：pip install kaggle 后重试")
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


def extract_jobs(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None, None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "JOBS" for t in node.targets):
            try:
                return ast.literal_eval(node.value), (node.lineno, node.end_lineno)
            except Exception:
                return None, None
    return None, None


def count_items(jobs):
    batch = jobs.get("batch") if isinstance(jobs, dict) else None
    if not isinstance(batch, list):
        return None
    n = 0
    for item in batch:
        if isinstance(item, dict) and isinstance(item.get("seeds"), list) and item["seeds"]:
            n += len(item["seeds"])
        else:
            n += 1
    return n


def calc_job_id(text, jobs):
    if jobs is not None:
        payload = json.dumps(jobs, ensure_ascii=False, sort_keys=True)
    else:
        payload = text
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def inject_job_id(text, job_id):
    line = f'JOB_ID = "{job_id}"'
    if re.search(r"^JOB_ID\s*=", text, re.M):
        return re.sub(r"^JOB_ID\s*=.*$", line, text, count=1, flags=re.M)
    lines = text.splitlines()
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if len(lines) > insert_at and re.search(r"coding[:=]", lines[insert_at]):
        insert_at += 1
    return "\n".join(lines[:insert_at] + [line] + lines[insert_at:]) + "\n"


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def trim_batch(text, keep=2):
    jobs, (start, end) = extract_jobs(text)
    if jobs is None or not isinstance(jobs.get("batch"), list) or len(jobs["batch"]) <= keep:
        return text, jobs
    new_jobs = dict(jobs)
    new_jobs["batch"] = jobs["batch"][:keep]
    lines = text.splitlines()
    replacement = "JOBS = " + json.dumps(new_jobs, ensure_ascii=False, indent=4)
    new_text = "\n".join(lines[:start - 1] + [replacement] + lines[end:])
    return new_text, new_jobs


def build_push_copy(kernel_dir, meta, job_id, smoke):
    tmp = Path(tempfile.mkdtemp(prefix="kwatch-push-"))
    src_dir = Path(kernel_dir)
    code_name = meta["code_file"]
    text = (src_dir / code_name).read_text(encoding="utf-8")
    new_meta = dict(meta)
    if smoke:
        jobs, _ = extract_jobs(text)
        if jobs is None:
            fail(f"[{SCRIPT_NAME}] FAIL: --smoke 需要 kernel.py 顶层有 JOBS 字典（字面量）")
        text, new_jobs = trim_batch(text, keep=2)
        smoke_id = calc_job_id(text, new_jobs)
        base_slug = meta["id"].split("/")[-1]
        new_meta["id"] = f'{meta["id"].split("/")[0]}/{base_slug}-smoke'
        new_meta["title"] = f'{meta.get("title", base_slug)}-smoke'
    else:
        smoke_id = None
    text = inject_job_id(text, smoke_id if smoke else job_id)
    (tmp / code_name).write_text(text, encoding="utf-8", newline="\n")
    new_meta.setdefault("is_private", True)
    (tmp / "kernel-metadata.json").write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    for p in src_dir.iterdir():
        if p.name in (code_name, "kernel-metadata.json") or p.is_dir():
            continue
        shutil.copy2(p, tmp / p.name)
    return tmp, new_meta, (smoke_id if smoke else job_id)


def estimate(jobs, load_sec, per_item_sec):
    n = count_items(jobs)
    if n is None:
        return None, None
    return n, load_sec + n * per_item_sec


def quota_gpu_remaining_hours():
    try:
        out = kaggle(["quota", "--format", "json"], timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            for row in json.loads(out.stdout):
                if row.get("resource") == "GPU":
                    return float(str(row.get("remaining", "0h")).rstrip("h"))
    except Exception:
        pass
    return None


def precheck(jobs, args, log):
    notes = []
    n, secs = estimate(jobs, args.load_sec, args.per_item_sec)
    if n is None:
        notes.append("估时跳过（kernel.py 无 JOBS 字面量字典，无法数 batch）")
    else:
        notes.append(f"估时 ≈ {secs / 60:.0f}min（{args.load_sec}s 加载 + {n}×{args.per_item_sec}s）")
        if args.load_sec == DEFAULT_LOAD_SEC and args.per_item_sec == DEFAULT_PER_ITEM_SEC:
            notes.append("缺省耗时参数取自 SDXL@T4 生图案例实测（1024²/25步），非生图负载请 --load-sec/--per-item-sec 覆盖")
        left = quota_gpu_remaining_hours()
        if left is None:
            notes.append("GPU 余量查询失败（凭据/网络），跳过余额比对")
        elif not args.smoke and secs / 3600 > left:
            notes.append(f"WARN 预估 {secs / 3600:.2f}h 超过 GPU 余量 {left:.2f}h——继续提交（Kaggle 会自行处理），请留意")
        else:
            notes.append(f"GPU 余量 {left:.2f}h，足够")
    if args.smoke:
        notes.append("冒烟模式：batch 缩至前 2 项，临时 -smoke kernel")
    for note in notes:
        log("  预检 | " + note)
    return {"items": n, "estimate_seconds": secs, "notes": notes}


def parse_status(text):
    m = re.search(r"KernelWorkerStatus\.(\w+)", text or "")
    if m:
        return m.group(1)
    m = re.search(r'status[":\s]+([A-Za-z_]+)', text or "", re.IGNORECASE)
    return m.group(1).upper() if m else ""


def push_kernel(push_dir):
    out = kaggle(["kernels", "push", "-p", str(push_dir)], timeout=600)
    combined = (out.stdout or "") + (out.stderr or "")
    useful = [ln for ln in combined.splitlines() if ln.strip() and "Warning" not in ln]
    return out.returncode == 0, useful, combined


def fetch_progress(ref, job_id):
    out = kaggle(["kernels", "logs", "-k", ref], timeout=120)
    text = (out.stdout or "") + (out.stderr or "")
    matched = None
    for m in PROGRESS_RE.finditer(text):
        if job_id and m.group(4) and m.group(4) != job_id:
            continue
        if job_id and not m.group(4):
            continue
        matched = (int(m.group(1)), int(m.group(2)), m.group(3) or "")
    return matched


def fetch_output(ref, out_dir):
    out = kaggle(["kernels", "output", "-k", ref, "-p", str(out_dir)], timeout=600)
    if out.returncode != 0:
        return False, (out.stderr or out.stdout or "").strip()[:300]
    files = sorted(p.name for p in Path(out_dir).rglob("*") if p.is_file())
    return True, files


def fetch_logs_tail(ref, lines=40):
    out = kaggle(["kernels", "logs", "-k", ref], timeout=300)
    text = (out.stdout or "") + (out.stderr or "")
    return [ln for ln in text.splitlines() if ln.strip()][-lines:]


def out_dir_for(kernel_dir, job_id):
    stamp = time.strftime("%Y%m%d-%H%M")
    return Path(kernel_dir) / "out" / f"{stamp}_{job_id}"


def bump_and_maybe_maintenance(kernel_dir, log, result):
    try:
        state = {}
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        runs = state.get("kwatch_runs", 0) + 1
        state["kwatch_runs"] = runs
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    except Exception:
        return
    if runs % MAINTAIN_EVERY != 0:
        return
    out_root = Path(kernel_dir) / "out"
    if not out_root.is_dir():
        return
    dirs = [(p, sum(f.stat().st_size for f in p.rglob("*") if f.is_file())) for p in out_root.iterdir() if p.is_dir()]
    total = sum(size for _, size in dirs)
    if not dirs or total < 100 * 1024 * 1024:
        return
    biggest = max(dirs, key=lambda x: x[1])
    msg = (f"MAINT | out/ 已累积 {total / 1e9:.2f}GB（{len(dirs)} 个任务目录，最大 {biggest[0].name} {biggest[1] / 1e9:.2f}GB）"
           f"——确认不需要后可手动删除旧目录")
    log("  " + msg)
    result["maintenance"] = msg


def main():
    stdout_utf8()
    parser = argparse.ArgumentParser(description="kaggle-agent kwatch v2：预检 → push → 心跳轮询 → 拉 output")
    parser.add_argument("-p", "--path", required=True, help="kernel 目录（含 kernel-metadata.json）")
    parser.add_argument("--smoke", action="store_true", help="冒烟模式：JOBS.batch 缩至前 2 项，用临时 -smoke kernel")
    parser.add_argument("--watch-only", action="store_true", help="不 push，只轮询既有 kernel")
    parser.add_argument("--dry-run", action="store_true", help="预检+校验并打印计划，不提交")
    parser.add_argument("--timeout-min", type=int, default=360, help="最长守候分钟数（默认 360）")
    parser.add_argument("--interval-start", type=int, default=15, help="轮询起始间隔秒（默认 15）")
    parser.add_argument("--interval-max", type=int, default=120, help="轮询间隔封顶秒（默认 120）")
    parser.add_argument("--load-sec", type=float, default=DEFAULT_LOAD_SEC, help=f"估时：模型/环境加载秒数（缺省 {DEFAULT_LOAD_SEC}，生图案例值）")
    parser.add_argument("--per-item-sec", type=float, default=DEFAULT_PER_ITEM_SEC, help=f"估时：单项任务秒数（缺省 {DEFAULT_PER_ITEM_SEC}，生图案例值，非生图负载务必覆盖）")
    parser.add_argument("--no-precheck", action="store_true", help="跳过预检三连")
    parser.add_argument("--json", action="store_true", help="输出机器可解析 JSON（最终快照）")
    args = parser.parse_args()

    kernel_dir = Path(args.path)
    meta = load_metadata(kernel_dir)
    source_text = (kernel_dir / meta["code_file"]).read_text(encoding="utf-8")
    jobs, _ = extract_jobs(source_text)
    job_id = calc_job_id(source_text, jobs)
    ref = meta["id"]
    events = []

    def log(msg):
        events.append(msg)
        if not args.json:
            print(msg, flush=True)

    result = {"tool": SCRIPT_NAME, "ok": False, "ref": ref, "job_id": job_id, "status": None,
              "elapsed_min": None, "output_dir": None, "output_files": None, "smoke": args.smoke}

    log(f"[{SCRIPT_NAME}] kernel={ref} job_id={job_id} gpu={bool(meta.get('enable_gpu'))}")

    if not args.no_precheck and not args.watch_only:
        pre = precheck(jobs, args, log)
        result["precheck"] = pre

    push_dir_desc = str(kernel_dir) + ("（副本：注入 JOB_ID" + ("+冒烟裁剪" if args.smoke else "") + "）" if not args.watch_only else "")
    plan = [
        (f"kaggle kernels push -p {push_dir_desc}") if not args.watch_only else "(跳过 push，watch-only)",
        f"kaggle kernels status -k {ref}  # 轮询至终态，间隔 {args.interval_start}s→{args.interval_max}s，期间解析 PROGRESS 心跳",
        f"kaggle kernels output -k {ref} -p {kernel_dir / 'out' / f'<时间戳>_{job_id}'}",
    ]
    result["plan"] = plan
    if not args.json:
        for ln in plan:
            log("   " + ln)
    if args.dry_run:
        result["ok"] = True
        tail = f"[{SCRIPT_NAME}] OK: dry-run 校验通过（预检见上，job_id={job_id}）"
        result["summary"] = tail
        result["log"] = events
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(tail)
        return 0

    active_job_id = job_id
    tmp_dir = None
    t0 = time.time()
    if not args.watch_only:
        tmp_dir, push_meta, active_job_id = build_push_copy(kernel_dir, meta, job_id, args.smoke)
        push_ref = push_meta["id"]
        result["ref"] = push_ref
        ok, useful, combined = push_kernel(tmp_dir)
        if not ok:
            log(combined.strip()[-1500:])
            tail = f"[{SCRIPT_NAME}] FAIL: kaggle kernels push 退出码非 0（上方为原始输出）"
            result["error"] = tail
            result["summary"] = tail
            result["log"] = events
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(tail)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 1
        for ln in useful:
            log("  push | " + ln.strip())
        if args.smoke:
            log(f"  冒烟 kernel {push_ref} 已提交（job_id={active_job_id}）")
        log("  push 完成，等 20s 让 slug 生效（刚 push 的 status 可能暂时报 Permission denied）")
        time.sleep(20)
        ref = push_ref
    elif args.smoke:
        log("  --smoke 在 watch-only 下无效（不 push），按原 kernel 盯守")

    interval = args.interval_start
    status, consecutive_errors, last_printed, tick = "", 0, "", 0
    deadline = t0 + args.timeout_min * 60
    while time.time() < deadline:
        tick += 1
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
        progress_line = ""
        if status in ("RUNNING", "UNKNOWN") and tick % 2 == 0:
            prog = fetch_progress(ref, active_job_id)
            if prog:
                n, total, tag = prog
                remain = (total - n) * args.per_item_sec / 60
                progress_line = f" 进度 {n}/{total}{'[' + tag + ']' if tag else ''} 剩余≈{remain:.0f}min"
                result["progress"] = {"n": n, "total": total, "tag": tag}
        if status != last_printed or progress_line:
            log(f"  {time.strftime('%H:%M:%S')} status={status or '?'} elapsed={elapsed:.1f}min{progress_line}")
            last_printed = status
        if status == "COMPLETE":
            out_dir = out_dir_for(kernel_dir, active_job_id)
            ok, payload = fetch_output(ref, out_dir)
            result["status"] = status
            result["elapsed_min"] = round(elapsed, 1)
            result["output_dir"] = str(out_dir)
            if ok:
                result["ok"] = True
                result["output_files"] = payload
                tail = (f"[{SCRIPT_NAME}] OK: {ref} COMPLETE in {elapsed:.1f}min，"
                        f"output → {out_dir}（{len(payload)} 个文件，job_id={active_job_id}）")
            else:
                result["ok"] = True
                result["output_files"] = []
                tail = (f"[{SCRIPT_NAME}] OK: {ref} COMPLETE in {elapsed:.1f}min（output 拉取失败为 WARN：{payload}）")
            if args.smoke:
                tail += "——冒烟通过，去全量：去掉 --smoke 重跑"
            break
        if any(d in status for d in TERMINAL_DEAD):
            result["status"] = status
            result["elapsed_min"] = round(elapsed, 1)
            logs = fetch_logs_tail(ref)
            log("  ---- logs tail ----")
            for ln in logs:
                log("   " + ln)
            tail = f"[{SCRIPT_NAME}] FAIL: {ref} 终态 {status}（日志尾部见上，完整日志: kaggle kernels logs -k {ref}）"
            result["error"] = tail
            break
        if status not in ACTIVE:
            log(f"  未知状态 {status!r}，继续轮询")
        time.sleep(interval)
        interval = min(interval + 15, args.interval_max)
    else:
        tail = (f"[{SCRIPT_NAME}] FAIL: 守候超时（{args.timeout_min}min），最后状态 {status or '?'}"
                f"——可加 --timeout-min 或改用 --watch-only 续盯")
        result["status"] = status or None
        result["error"] = tail

    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if result["ok"]:
        bump_and_maybe_maintenance(kernel_dir, log, result)
    result["summary"] = tail
    result["log"] = events
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(tail)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
