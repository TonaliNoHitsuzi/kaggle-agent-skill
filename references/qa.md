# QA：实测踩坑合集（对号入座）

> 全部条目来自 2026-10 真实调试实录（原始记录见 [case-study-assets/](case-study-assets/)）。按"现象 → 原因 → 解法"组织，报错先 Ctrl+F 现象关键词。

## 网络类（大陆用户重灾区）

### 1. 大文件上传"静默失败"：exit 1、零报错、零进度

- **现象**：`kaggle datasets create` 打印 `Starting upload for file ...` 后直接退出，exit 1，没有任何报错信息。换代理软件重试依旧。
- **原因**：上传走 `storage.googleapis.com`（GCS，被墙）。Kaggle API 域（www.kaggle.com）可直连所以一切看起来正常，但连接 GCS 失败时 CLI 入口吞掉了异常。**小文件（几十 KB 的 ping.txt）能传成功会加重误导**——因为小文件走的链路/时序不同，不代表大文件也通。
- **解法**：开代理软件 + 设置 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量（或本仓 `config/settings.json`），然后用 `python scripts/uploader.py -p <目录>`：上传前先探测 GCS 连通性，不通直接说人话；上传中用 308 探针显示真实已传字节。
- **附**：诊断期可用 python 直调 kaggle 库代替 CLI 入口，能看到真实 traceback。

### 2. 用第三方测速估算上传时长，越等越绝望

- **现象**：代理上行测速 ~1.5MB/s，以为 6.5GB 要传 70+ 分钟。
- **原因**：第三方测速端点瓶颈 ≠ GCS 接收瓶颈。实测 GCS 接收 ~8MB/s。
- **解法**：以 uploader 的 308 探针实时读数为准，别用测速网站推算。

### 3. 上传中断，白传了吗？

- **现象**：上传几 GB 后断网/中断，心态爆炸。
- **原因**：其实没白传——kaggle CLI 自带 GCS resumable 断点续传，resume 状态存 `%TEMP%/.kaggle/uploads/<file>.json`（含 GCS 会话 URL 和 token），会话有效期数天。
- **解法**：网络恢复后重跑同一条命令（或 uploader 脚本），日志出现 `Already uploaded N bytes. Will resume upload at ...`，从中断处续传。实测 6.5GB 传到 5.4GB 时中断，重跑只补了剩余 1.09GB。

## Kernel 运行类

### 4. kernel 卡住 20+ 分钟零输出，最后啥也没有

- **现象**：kernel RUNNING 20 分钟，日志停在一条 pip 下载行后再无输出，Output 0 B。
- **原因**：代码里写了 `pip install -U diffusers transformers ...`——`-U` 强制升级触发 Kaggle 预装栈（torch 2.x+cu 等）的依赖回溯解析，死循环级缓慢。
- **解法**：**Kaggle 上永远不要 `-U` 升级预装包**。先 `print(版本号)` 确认预装情况，缺什么装什么：`pip install -q "diffusers>=0.27"`。Kaggle Latest 镜像预装 diffusers/transformers 较新，多数情况秒过。

### 5. diffusers 报 `ValueError: Invalid 'pretrained_model_name_or_path' provided. Please set it to a valid URL.`

- **现象**：`from_single_file("/kaggle/input/t2i-models/xxx.safetensors")` 报"要 URL"。
- **原因**：报错文案极具误导性——`from_single_file` 对路径先做 `os.path.isfile` 判断，**文件不存在时把路径当 HF repo id 解析然后报"要 URL"**。本质是挂载路径和你写的不一致（比如 dataset 里套了层子目录）。
- **解法**：报"URL"先怀疑文件路径不存在。用 `os.walk("/kaggle/input")` 打印全部实际文件 + `glob("**/*.safetensors")` 自动定位（见 t2i-case-study.md 的 `locate_model()`）。

### 6. 刚 push 完查 status 报 Permission denied / Not Found

- **现象**：push 成功后立刻 `kaggle kernels status` 报权限/不存在错误。
- **原因**：kernel slug 尚未全局生效，暂态。
- **解法**：等半分钟再查；kwatch 脚本已内置容忍（连续 8 次失败才判定放弃）。

### 7. status 显示 RUNNING 但感觉没在跑

- **现象**：push 后立刻显示 RUNNING，但日志长时间不动。
- **原因**：状态观察有滞后，且 RUNNING 初期是环境准备（装依赖、挂载数据集），大 dataset 挂载本身要时间。排队高峰也可能 QUEUED 数分钟。
- **解法**：耐心 + 看日志而非猜（`kaggle kernels logs -k <ref>`）；配额只从实际运行起算，QUEUED 不计时。

## Dataset 类

### 8. 新建 dataset 后立刻查询报 403 / NotFound

- **现象**：`kaggle datasets create` 成功，2 分钟内 `kaggle datasets status/files` 报 403 或 NotFound。
- **原因**：新建 dataset 的元数据全球生效有延迟，属正常暂态。
- **解法**：等 2-5 分钟再查。403 ≠ 权限配置错误，别急着折腾凭据。

### 9. 上传的 dataset 缺文件：子文件夹全没了

- **现象**：`kaggle datasets create -p 目录` 成功，但云端只有根目录文件，子文件夹全部被跳过。
- **原因**：CLI 默认 skip 子文件夹（提示 use `--dir-mode`），不是 bug 是默认行为。
- **解法**：最省事是**平铺结构**（所有文件放 dataset 根目录）；确需目录结构用 `--dir-mode zip`（子目录打包成 zip 上传，云端 kernel 里解包）或拆成多个 dataset。

## 数据与配额类

### 10. 担心"超时没取件，output 丢了"

- **现象**：kernel 跑完没及时拉 output，担心数据没了。
- **原因**：混淆了 commit 与 interactive。**commit 运行的 output 持久保存（上限 20GB）**，不存在"超时未取件丢数据"；会丢的只有交互式会话里没 Save Version 的内容。
- **解法**：放心，任何时候都能 `kaggle kernels output -k <ref> -p <目录>` 拉取。

### 11. 凭据与权限：kaggle.json 放哪、能给别人吗

- `kaggle.json` = 账号 API 钥匙，只放 `~/.kaggle/`（Windows: `C:\Users\<你>\.kaggle\`）；类 Unix 记得 `chmod 600`。
- 不放 git 仓库、不进聊天记录、不借人。怀疑泄露立刻去 Kaggle Settings → API → Expire Token 作废重发。
- 新版 CLI（2.x）另有 `kaggle auth login` 生成的 `access_token`，doctor 脚本两种形态都识别。

## 一分钟自检表（万能起手式）

遇到任何 Kaggle 问题，先跑：

```bash
python scripts/doctor.py
```

六项全绿再谈别的——九成"玄学问题"（上传失败、403、认证错）都能在这一步定位。
