# 方法论：本地决策 + 云端执行的异步流水线

> Kaggle 免费 GPU 的正确打开方式不是"远程桌面"，而是**异步流水线**：你的电脑负责思考和决策，云端负责挥霍算力。本文是这套工作模式的说明书，也是 AI 助手替你执行任务时遵循的章法。

## 为什么是异步，不是交互式

（此处结论来自实测论证：我们也尝试过在 Kaggle 上跑 ComfyUI 交互式会话——排队+断线机制让它完全不可靠，放弃。）

| | 交互式（网页 notebook） | 异步流水线（本 skill） |
|---|---|---|
| 你盯着看 | 是 | 否，提交后走人 |
| 断网影响 | 会话丢失风险 | 零影响 |
| AI 助手可代劳 | 难（要操作浏览器） | 天然契合（全是命令行） |
| 复现性 | 靠手点 | 每次 push 都是一个版本，天然可复现 |

Kaggle commit 模式的三件套（无人值守、12h 上限、output 持久）就是为这种模式准备的。

## 四步循环

```text
┌─ ① 本地决策 ─┐   ┌─ ② 轻装推送 ─┐   ┌─ ③ 云端执行 ─┐   ┌─ ④ 拉回评审 ─┐
│ 定参数/写代码 │ → │ push KB 级    │ → │ 挂载 Dataset │ → │ output 落地   │
│ （AI 主战场） │   │ kernel 任务   │   │ 模型，GPU 跑 │   │ 入库/看图/复盘│
└──────────────┘   └──────────────┘   └──────────────┘   └──────┬───────┘
       ▲                                                           │
       └───────────────── 不满意，改参数再来一轮 ────────────────────┘
```

### ① 本地决策（零成本）

在本地确定：用什么模型、什么参数、跑几组、产物叫什么。这一步只消耗你的（或 AI 的）脑子，不消耗任何配额。**决策产物就是 kernel.py 里的一个配置字典**（见下方骨架）。

### ② 轻装推送（KB 级流量）

kernel 只装代码和参数，**绝不把大文件塞进 kernel 目录**。模型和数据提前放在 dataset 里（一次性上传，长期复用），kernel 用 `dataset_sources` 挂载。push 一个 3KB 的脚本几秒完成。

### ③ 云端执行（配额消耗区）

- `enable_gpu: true` 才占 30h 周配额；验链路、调语法用 CPU kernel 白嫖。
- 单次最长 12h，超了会 task split（拆任务分次跑）或精简流程。
- `enable_internet: true` 时可以在运行中 pip install（注意：**不要 `-U` 升级预装包**，会依赖解析卡死，见 qa.md）。

### ④ 拉回评审（AI 主战场）

kwatch 自动把 output 拉到本地。惯例是 kernel 末尾写一份 `report.json`（job_id+参数+耗时+产物清单），评审时 AI 读它即可重建全部上下文，不需要翻日志。

## 通用 kernel 骨架

照这个骨架填空，能覆盖大部分"参数批量实验"型任务：

```python
import json, time
from pathlib import Path

JOB_ID = globals().get("JOB_ID", "manual")  # kwatch 推送副本自动注入任务指纹；手动跑兜底 manual

JOBS = {
    "model": "your-model-file",     # 从挂载的 dataset 里挑
    "batch": [
        {"input": "...", "params": {"k": v}} for v in range(4)
    ],
}

OUT = Path("/kaggle/working")
TOTAL = len(JOBS["batch"])


def heartbeat(done, tag=""):
    print(f"PROGRESS {done}/{TOTAL} [{tag}] job={JOB_ID}", flush=True)


def locate_model():
    import glob
    cands = glob.glob("/kaggle/input/**/*.你的模型后缀", recursive=True)
    if not cands:
        raise SystemExit("model not found under /kaggle/input")
    print("USE MODEL:", cands[0])
    return cands[0]


def run_one(job):
    ...                              # 你的计算


def main():
    t0 = time.time()
    meta = []
    for i, job in enumerate(JOBS["batch"]):
        ts = time.time()
        result = run_one(job)
        meta.append({**job, "seconds": round(time.time() - ts)})
        heartbeat(len(meta), f"b{i}")
        print(f"[{len(meta)}] {time.time()-ts:.1f}s done")
    (OUT / "report.json").write_text(json.dumps(
        {"job_id": JOB_ID, "jobs": JOBS, "results": meta, "total_seconds": round(time.time()-t0, 1)},
        ensure_ascii=False, indent=2), encoding="utf-8")

main()
```

五个设计要点：

1. **任务清单前置**（`JOBS` 字典）：本地改实验 = 只改这个字典，其余不动。kwatch 的预检估时、`--smoke` 冒烟、任务指纹都识别这个结构。
2. **心跳行 `PROGRESS n/N [tag] job=<id>`**：每完成一个任务项打一行——kwatch 等待期解析它显示进度与剩余估算，`job=` 防止把上一版本日志误当本次进度。不打心跳也能正常跑，只是等待期盲等。
3. **任务项级粒度**：心跳打到"任务项"一级即可（单张 25s 的负载打更细没有价值）；细粒度进度交给 Kaggle 原生的 tqdm 输出。
4. **模型路径运行时探测**（`locate_model`）：挂载点路径可能和预想不同，glob 兜底 + 打印实际用到的路径。
5. **report.json 收尾**（首字段 job_id）：评审、对比、下一轮决策全靠它；job_id 让多次实验结果可对账。

## 节奏与配额纪律

- **预检三连**（kwatch push 前自动做）：①估时（按你的 JOBS 结构数 batch；缺省公式参数取自 SDXL@T4 生图实测，非生图负载用 `--load-sec/--per-item-sec` 覆盖）②查 GPU 周配额余量，预估超余量警告但不阻塞 ③冒烟先行——大任务先 `--smoke`（batch 缩至前 2 项跑通全链路）再全量。
- **跑前查**：`python scripts/quota.py`。余量 < 任务预估时长就改天或拆小。
- **CPU 先验**：新 kernel 第一次跑用 CPU 验证语法和 IO（`enable_gpu: false`），通了再开 GPU——冒烟同理，先小后大。
- **批量摊薄**：一次 push 跑一组批量任务（模型加载一次跑 N 个任务），比 N 次 push 省大量加载时间和排队时间。
- **长任务守边界**：预估接近 12h 的任务，拆两段，第二段从第一段的 output dataset 接力。

## 结果归档与维护

- kwatch 把每次结果拉到 `out/YYYYMMDD-HHMM_<jobid>/`：时间戳 + 任务指纹（job_id = JOBS 配置哈希，无 JOBS 退化源码哈希），多次实验天然不互相覆盖。
- job_id 由 kwatch push 时经**推送副本**静默注入 kernel（源码零污染），同时落在 report.json 首字段与目录名——上层流水线可依赖它做任务对账与结果校验，初学者零感知。
- out/ 会随使用累积：kwatch 每 10 次成功运行自动统计一次总量并提示（不自动删），确认不需要的旧目录手动清理即可。

## 与 AI 助手的分工（元模式）

这套流水线里 AI 助手的角色：

| 步骤 | 人类 | AI 助手 |
|---|---|---|
| ① 决策 | 提需求、拍板方向 | 设计参数矩阵、写 kernel.py |
| ② 推送 | —— | `kwatch push` + 守候 |
| ③ 执行 | ——（去干别的） | （无人值守，无需陪伴） |
| ④ 评审 | 看图/看结论 | 读 report.json 汇总对比、提下一轮参数建议 |

对 AI 的操作约束（skill 内置纪律）：凭据零经手；删除/覆盖云端资源前必须向人类复述目标并确认；配额敏感操作前主动报告余量。
