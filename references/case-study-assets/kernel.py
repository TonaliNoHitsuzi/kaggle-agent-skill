# -*- coding: utf-8 -*-
"""t2i-runner: Kaggle GPU 文生图执行器 v0.1 (SDXL 首跑)
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
