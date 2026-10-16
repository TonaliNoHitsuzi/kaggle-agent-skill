# 完整案例：在 Kaggle 免费 GPU 上部署 SDXL 生图

> 全可复制教程：从零到本地文件夹里出现你自己生成的第一批 AI 图。2026-10-16 全程实测（Windows 11 + 大陆网络 + T4 x2），单张 1024² 约 22-28 秒，6 张含模型加载全程 5 分钟。
>
> 三轮调试实录与原始数据见 [case-study-assets/](case-study-assets/)（debug-history.md / metadata.json / upload-case.md），本文是提炼后的可复制版。

## 成果预览（实测数据）

| 指标 | 数值 |
|---|---|
| 硬件 | Tesla T4 x2（开 1 卡跑） |
| 模型 | SDXL base 1.0（6.5GB safetensors） |
| 单张耗时 | 22-28s @ 1024×1024 / 25 步 |
| 首跑全程 | 5 分钟（含模型加载 + 6 张生成 + 拼图） |
| 配额消耗 | 约 0.1h |

产物：6 张 PNG + `contact_sheet.png`（6 宫格拼图，方便一眼评审）+ `metadata.json`（prompt/seed/耗时全记录）。

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
"""t2i-runner: Kaggle GPU 文生图执行器（SDXL）
挂载 t2i-models dataset -> from_single_file 加载 SDXL -> 批量抽卡 -> 产物落 /kaggle/working
"""
import json, os, time, glob, subprocess, sys
from pathlib import Path

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

MODEL_PATH = None  # 运行时探测
OUT = Path("/kaggle/working")
IMG = OUT / "images"
IMG.mkdir(parents=True, exist_ok=True)


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
    for job in JOBS["batch"]:
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
            print(f"[{len(meta)}] seed={seed} {dt:.1f}s -> {name}")

    files = sorted(glob.glob(str(IMG / "*.png")))
    thumbs = [Image.open(f).resize((384, 384)) for f in files]
    cols = 3
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 384, rows * 384), "black")
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * 384, (i // cols) * 384))
    sheet.save(OUT / "contact_sheet.png")

    (OUT / "metadata.json").write_text(
        json.dumps({"jobs": JOBS, "results": meta, "total_seconds": round(time.time() - t0, 1)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE {len(meta)} images in {(time.time() - t0) / 60:.1f} min")


main()
```

代码要点（为什么这样写，改前先懂）：

- **pip 装 diffusers 不带 `-U`**：`-U` 会触发 Kaggle 预装栈依赖回溯解析，卡死 20+ 分钟无输出；预装版本已满足要求时秒过。这是本案例踩过的最大坑（完整实录见 [case-study-assets/debug-history.md](case-study-assets/debug-history.md)）。
- **`locate_model()` 运行时探测**：挂载点实际路径可能与预想不同；`from_single_file` 遇到不存在的路径会报极具误导性的 "Invalid URL"，实际是路径不存在。先 `os.walk` 打印全部输入文件再 glob 定位，一次根治。
- **contact_sheet 拼图**：一次评审几十张图时，看拼图比一张张点快得多。
- **metadata.json 收尾**：哪个 prompt、哪个 seed、耗时多少，全记录，复现与筛图靠它。

## 第四步：提交并收图

```bash
python scripts/kwatch.py -p t2i-runner
```

约 5 分钟后（首次排队另计），`t2i-runner/output/` 里出现：

```text
images/00_s42.png ... 05_s43.png
contact_sheet.png
metadata.json
```

打开 `contact_sheet.png`——你的第一批免费 GPU 生图出炉。

## 改造指南

| 想要 | 改哪 |
|---|---|
| 换 prompt / 加组数 | `JOBS["batch"]`（prompt + seeds 列表） |
| 提速（牺牲精度） | `size` 降到 768、`steps` 降到 20 |
| 换模型 | dataset 里放新 safetensors → `JOBS["model"]` 改文件名（支持 SD1.5/SDXL 系单文件；flux 需改 pipe 类） |
| 固定可复现 | seeds 固定值（本例 42/43 即为可复现抽卡） |
| 批量更大 | 加 batch 条目即可，注意 12h 与 output 20GB 上限 |

## 消耗了多少免费额度？

`python scripts/quota.py` 实查。本案例约 0.1h——每周 30h 等于每周约 300 轮这样的 6 图批量（1 图 25s 计）。额度对个人玩家非常充裕，真正的瓶颈是你的 prompt 创造力。
