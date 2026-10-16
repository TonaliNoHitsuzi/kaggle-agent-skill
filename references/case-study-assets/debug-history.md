# t2i-runner 三轮调试实录（QA 章节原料）

> 2026-10-16 实测，Kaggle kernel: tonarinohitsuji/t2i-runner v1→v3，环境：T4 x2 / Latest Container / Python 3.13

## v1：pip -U 依赖解析卡死（卡 20+ 分钟无输出）

- **现象**：kernel RUNNING 20 分钟，日志停在 `36.8s` 一条 pip 下载行，之后零输出，Output 0 B。
- **原因**：脚本写 `pip -q install -U diffusers transformers accelerate safetensors`，`-U` 强制升级触发 Kaggle 预装栈（torch 2.11+cu128 等）的依赖回溯解析，死循环级缓慢。
- **修复**：去掉 `-U`，改 `pip install -q "diffusers>=0.27" ...` + `subprocess.run(..., timeout=300)`。Kaggle Latest 镜像预装 diffusers 0.40 / transformers 5.16 已满足要求，秒过。
- **教训**：**在 Kaggle 上永远不要 -U 升级预装包**；先 print 版本确认，缺什么装什么。

## v2：from_single_file 报 Invalid URL（模型路径不存在）

- **现象**：`ValueError: Invalid 'pretrained_model_name_or_path' provided. Please set it to a valid URL.`
- **原因**：diffusers 的 `from_single_file` 对路径先做 `os.path.isfile` 判断，不存在时把路径当 HF repo id 解析然后报"要 URL"——报错文案极具误导性，本质是 `/kaggle/input/t2i-models/sd_xl_base_1.0.safetensors` 不在预期位置。
- **修复**：不硬编码挂载路径，运行时 `os.walk('/kaggle/input')` 打印全部文件 + `glob('**/*.safetensors')` 自动定位（kernel.py 的 `locate_model()`）。
- **教训**：**报错文案说 URL 时先怀疑文件路径不存在**；挂载点结构首次必打印。

## v3：成功

- 模型路径自动探测生效，SDXL 6 张 1024²/25步 全程 5 分钟（含加载），单张 22-28s。
- 产出：images/*.png + contact_sheet.png（本地评审拼图）+ metadata.json（prompt/seed/耗时全记录）。

## 其他实测事实（QA 可直接引用）

- 新建 dataset 后 2 分钟内 API 查询 403/NotFound 属正常暂态，等 2-5 分钟再查。
- `kaggle datasets create -p` 默认 **skip 子文件夹**（提示 use --dir-mode），平铺结构最省事。
- commit 模式 output 持久保存（上限 20GB），不存在"超时未取件丢数据"；会丢的只有交互式会话未保存内容。
- push 后立刻 RUNNING 不代表没排队——排队中 status 也可能显示 RUNNING？实测 18:0x push 即刻领到 GPU，高峰期会 QUEUED。
- `kaggle kernels status` 在 kernel 刚 push 后可能报 Permission denied（slug 尚未生效），等半分钟。
