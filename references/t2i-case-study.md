# 完整案例：在 Kaggle 免费 GPU 上部署 SDXL 生图

> 全可复制教程：从零到本地文件夹里出现你自己生成的第一批 AI 图。2026-10-16 全程实测（Windows 11 + 大陆网络 + T4 x2）。
>
> 原始档案见 [case-study-assets/](case-study-assets/)：首轮调试 [debug-history.md](case-study-assets/debug-history.md)、上传实录 [upload-case.md](case-study-assets/upload-case.md)、边界测试与性能公式 [bench-v4-report.md](case-study-assets/bench-v4-report.md)。

## 成果预览（实测数据）

| 指标 | 数值 |
|---|---|
| 硬件 | Tesla T4 x2（开 1 卡跑） |
| 模型 | SDXL base 1.0（6.5GB safetensors） |
| 单张耗时 | 22.9-25.1s @ 1024×1024 / 25 步（纯净计时 mean 24.2s，σ 0.86s） |
| 边界测试 | 22 张 / 10 分钟（含 77s 模型加载） |
| 首跑全程 | 5 分钟（6 张含加载+拼图） |
| 配额消耗 | 约 0.1h / 22 张轮 |

产物：PNG 若干 + `contact_sheet.png`（宫格拼图，一眼评审）+ `report.json`（job_id/prompt/seed/耗时全记录），自动落入本地 `out/时间戳_任务指纹/` 目录。

**耗时可预测**（T4 / 1024² / 25 步 / CFG7.5 实测）：

> 任务总时长 ≈ **77s 模型加载 + N × 24.5s/张**（复杂 prompt 不影响速度；img2img 按 strength 折减步数）

kwatch 的预检估时用的就是这个公式（非生图负载记得用 `--load-sec/--per-item-sec` 覆盖）。

## 第一步：本地下载 SDXL 模型（走 ModelScope，国内快）

模型文件 `sd_xl_base_1.0.safetensors`（6.5GB）。HuggingFace 直连慢，用国内镜像 ModelScope：

```bash
pip install modelscope
modelscope download --model '<在 modelscope.cn 搜索 sd_xl_base_1.0，选下载量高的镜像 repo id>' sd_xl_base_1.0.safetensors --local_dir ./t2i-models
```

（也可网页直接下：modelscope.cn 搜 `sd_xl_base_1.0`，下载 safetensors 文件放进 `t2i-models/` 文件夹。）

## 第二步：上传为 Kaggle Dataset（一次性，此后长期复用）

6.5GB 大文件，上传走 googleapis.com——**大陆网络必须开代理**（详见 qa.md 第 1 条）。

`t2i-models/dataset-metadata.json`：

```json
{
  "title": "t2i-models",
  "id": "你的用户名/t2i-models",
  "licenses": [{"name": "MIT"}]
}
```

目录保持**平铺**（safetensors 直接在根目录，别套子文件夹——create 默认跳过子目录）。

用上传器（代理自检 + 308 进度探针 + 失败自动续传重试）：

```bash
python scripts/uploader.py -p t2i-models
```

先不放心可以 `python scripts/uploader.py -p t2i-models --probe-only` 只做诊断。

> 实测参考：代理上行第三方测速 ~1.5MB/s，但 GCS 接收端实际 ~8MB/s——6.5GB 全程有效上传约 15 分钟。中途断网不慌，kaggle CLI 自带断点续传（状态存在 `%TEMP%/.kaggle/uploads/`），重跑 uploader 自动续。完整实录见 [case-study-assets/upload-case.md](case-study-assets/upload-case.md)。

## 第三步：写生图 kernel

新建 `t2i-runner/`，两个文件。

`t2i-runner/kernel-metadata.json`：

```json
{
  "id": "你的用户名/t2i-runner",
  "title": "t2i-runner",
  "code_file": "kernel.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": true,
  "dataset_sources": ["你的用户名/t2i-models"]
}
```

（`enable_internet: true` 是为了运行中 pip 装 diffusers；`dataset_sources` 挂载上一步的模型仓。）

`t2i-runner/kernel.py`（完整可用，改 `JOBS` 即可定制）：

```python
# -*- coding: utf-8 -*-
"""t2i-runner v4: Kaggle GPU 文生图执行器（SDXL）
挂载 t2i-models dataset -> from_single_file 加载 SDXL -> 批量抽卡 -> 产物落 /kaggle/working
"""
import json, os, time, glob, subprocess, sys
from pathlib import Path

JOB_ID = globals().get("JOB_ID", "manual")  # kwatch 推送副本自动注入；手动跑则为 manual

subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "diffusers>=0.27", "transformers>=4.36", "accelerate", "safetensors"],
               check=False, timeout=300)

JOBS = {
    "model": "sd_xl_base_1.0.safetensors",
    "size": 1024,
    "steps": 25,
    "cfg": 7.5,
    "negative": "lowres, bad anatomy, bad hands, watermark, blurry, jpeg artifacts",
    "batch": [
        {"prompt": "A serene mountain lake at sunrise, mist over water, pine forest, golden light, ultra detailed, professional landscape photography", "seeds": [42, 43]},
        {"prompt": "Cute robot barista making coffee in a cozy cafe, warm lighting, pixar style, 3d render, highly detailed", "seeds": [42, 43]},
        {"prompt": "Isometric illustration of a futuristic data center, clean minimal style, soft colors, vector art", "seeds": [42, 43]},
    ],
}

OUT = Path("/kaggle/working")
IMG = OUT / "images"
IMG.mkdir(parents=True, exist_ok=True)
TOTAL = sum(len(j["seeds"]) for j in JOBS["batch"])


def locate_model():
    for root, dirs, files in os.walk("/kaggle/input"):
        for f in files:
            print("INPUT:", os.path.join(root, f))
    cands = glob.glob("/kaggle/input/**/*.safetensors", recursive=True)
    cands = [c for c in cands if JOBS["model"] in c] or cands
    if not cands:
        raise SystemExit("no safetensors found under /kaggle/input")
    print("USE MODEL:", cands[0])
    return cands[0]


def heartbeat(done, tag):
    print(f"PROGRESS {done}/{TOTAL} [{tag}] job={JOB_ID}", flush=True)


def main():
    t0 = time.time()
    import torch
    import diffusers, transformers
    print("diffusers", diffusers.__version__, "| transformers", transformers.__version__)
    from diffusers import StableDiffusionXLPipeline
    from PIL import Image

    print("torch", torch.__version__, "| cuda", torch.cuda.is_available(),
          "|", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-")

    pipe = StableDiffusionXLPipeline.from_single_file(locate_model(), torch_dtype=torch.float16, use_safetensors=True)
    pipe = pipe.to("cuda")
    pipe.vae.enable_slicing()

    meta = []
    for bi, job in enumerate(JOBS["batch"]):
        for seed in job["seeds"]:
            g = torch.Generator("cuda").manual_seed(seed)
            ts = time.time()
            img = pipe(
                prompt=job["prompt"], negative_prompt=JOBS["negative"],
                width=JOBS["size"], height=JOBS["size"],
                num_inference_steps=JOBS["steps"], guidance_scale=JOBS["cfg"],
                generator=g,
            ).images[0]
            dt = time.time() - ts
            name = f"{len(meta):02d}_s{seed}.png"
            img.save(IMG / name)
            meta.append({"file": f"images/{name}", "prompt": job["prompt"], "seed": seed,
                         "steps": JOBS["steps"], "cfg": JOBS["cfg"], "seconds": round(dt, 1)})
            heartbeat(len(meta), f"b{bi}")
            print(f"[{len(meta)}] seed={seed} {dt:.1f}s -> {name}")

    files = sorted(glob.glob(str(IMG / "*.png")))
    thumbs = [Image.open(f).resize((384, 384)) for f in files]
    cols = 3
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 384, rows * 384), "black")
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * 384, (i // cols) * 384))
    sheet.save(OUT / "contact_sheet.png")

    (OUT / "report.json").write_text(
        json.dumps({"job_id": JOB_ID, "jobs": JOBS, "results": meta,
                    "total_seconds": round(time.time() - t0, 1)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE {len(meta)} images in {(time.time() - t0) / 60:.1f} min")


main()
```

代码要点（为什么这样写，改前先懂）：

- **心跳行 `PROGRESS n/N [tag] job=<id>`**：kwatch 轮询时解析它显示进度与剩余估算；`job=` 用于区分当前运行与上一版本日志（防误报）。不跑 kwatch 也不影响——它只是一行普通打印。
- **`JOB_ID` 兜底行**：kwatch push 时通过推送副本静默注入任务指纹（源码零污染）；手动/网页跑时兜底为 `manual`。
- **report.json 收尾**（首字段 job_id）：任务配置、每张 prompt/seed/耗时全记录，复现、筛图、下一轮决策全靠它。
- **pip 装 diffusers 不带 `-U`**：`-U` 会触发 Kaggle 预装栈依赖回溯解析，卡死 20+ 分钟无输出；预装版本已满足要求时秒过。这是本案例踩过的最大坑（完整实录见 [debug-history.md](case-study-assets/debug-history.md)）。
- **`locate_model()` 运行时探测**：挂载点实际路径可能与预想不同；`from_single_file` 遇到不存在的路径会报极具误导性的 "Invalid URL"，实际是路径不存在。先 `os.walk` 打印全部输入文件再 glob 定位，一次根治。
- **contact_sheet 拼图**：一次评审几十张图时，看拼图比一张张点快得多。

## 第四步：提交并收图

先冒烟再全量（预检三连会自动估时、查余额）：

```bash
python scripts/kwatch.py -p t2i-runner --smoke     # 冒烟：batch 前 2 项，临时 -smoke kernel，约 2 分钟
python scripts/kwatch.py -p t2i-runner             # 全量（约 77s + N×24.5s，预检会报估算）
```

等待期 kwatch 会显示心跳进度（`PROGRESS 3/6 [b1] job=ab12cd34` → `进度 3/6 剩余≈1min`）。完成后结果自动落入：

```text
t2i-runner/out/20261016-1830_ab12cd34/
├── images/00_s42.png ...
├── contact_sheet.png
└── report.json
```

目录名 = 时间戳 + 任务指纹（job_id = 任务配置的哈希，配置变则指纹变）——多次实验天然归档不互相覆盖。打开 `contact_sheet.png`，你的第一批免费 GPU 生图出炉。

## 改造指南

| 想要 | 改哪 |
|---|---|
| 换 prompt / 加组数 | `JOBS["batch"]`（prompt + seeds 列表） |
| 提速（牺牲精度） | `size` 降到 768、`steps` 降到 20 |
| 换模型 | dataset 里放新 safetensors → `JOBS["model"]` 改文件名（支持 SD1.5/SDXL 系单文件；flux 需改 pipe 类） |
| 固定可复现 | seeds 固定值（本例 42/43 即为可复现抽卡） |
| img2img 改图 | 加 init 图到 dataset，`StableDiffusionXLImg2ImgPipeline`，strength 见下表 |
| 批量更大 | 加 batch 条目即可，注意 12h 与 output 20GB 上限 |

## 边界实测：SDXL 能干什么、不能干什么

> 22 张边界测试轮的结论（v4 kernel，原始报告见 [bench-v4-report.md](case-study-assets/bench-v4-report.md)）。

### 场景可用性

| 场景 | 可用性 |
|---|---|
| 基础素材生成（简单物象、静物、氛围图） | ✅ 可用 |
| 手部特写 / 人像 / 多人互动 / 招牌短文字（COFFEE 级）/ 复杂街景 | ✅ 实测通过（0/6 崩坏） |
| 人物全身 / 动物 / 多元素交互 | ❌ 基本不可用，需手段规避（负面词强化、抽卡 N 选 1、ControlNet） |
| 科研架构图 / 精确流程图 / 公式 / 电路化学结构式 | ❌ 完全不可用——退回 Mermaid/D2 代码渲染，无悬念 |
| 科研概念示意图 / 管线信息图 / 3D 结构剖面 / 拓扑可视化 | ✅ 可用（概念级，非精确） |

### img2img 增量修改（strength 权衡）

| strength | 构图保持 | 指令执行 |
|---|---|---|
| 0.3 | 全保 | 差（改不动） |
| 0.6 | 主体保，元素会丢（实测猫消失） | 好 |
| 0.85 | 大变 | 完全实现 |

结论：适合光照/氛围/风格类修改；物体级保持不可靠——精确元素编辑需 Kontext 类指令编辑模型。

### ⚠️ 人工验收终审（比数据更重要的教训）

边界测试中 AI 视觉评审报告了 **0/6 崩坏率，被人工看图推翻**：动物与人物全身存在多处结构问题（含一张狗首尾分离），架构图完全不能看。

**方法论教训：AI 视觉评审存在系统性乐观偏差（384px 缩略图漏检 + 模型倾向好评），只能当初筛；终审必须是人工看图。任何"AI 评审通过"的结论都不可直接采信。**

同理：AI 助手帮你跑完生图任务后，`contact_sheet.png` 请自己过目——抽卡结果的好坏，最后一眼必须是人眼。

## 消耗了多少免费额度？

`python scripts/quota.py` 实查。按公式 77s + N×24.5s：22 张轮约 0.15h，每周 30h ≈ 每周 4000 张 1024² 生图。额度对个人玩家非常充裕，真正的瓶颈是你的 prompt 创造力。
