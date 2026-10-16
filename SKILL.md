---
name: kaggle-agent
description: >-
  Kaggle 免费 GPU 额度（T4 x2，每周 30h）的 AI 助手操作手册与工具箱：引导新用户注册/手机验证/配置
  API token，doctor 环境自检，两阶 hello world 跑通 GPU，此后用脚本四件套（doctor/quota/kwatch/uploader）
  + 原生 CLI 完成"提交任务-等算力-收结果"日常闭环。含大陆代理配置说明与 SDXL 生图部署完整案例。
  中文触发：kaggle、白嫖 GPU、免费算力、Kaggle 注册、kaggle.json、GPU 配额、kernel 提交、
  kaggle 上传、拉结果、远程跑模型、生图部署、SDXL Kaggle。
license: MIT
compatibility:
  - opencode
  - claude-code
  - 任何支持 Agent Skills 目录机制的 agent（正文零私有依赖，独立运作）
metadata:
  version: 0.1.0
  kaggle-cli-tested: 2.2.4（兼容 1.6+，quota 子命令需 2.x）
  language: zh-CN
  human-docs: references/（非 agent 用户可直接当说明书读）
---

# kaggle-agent：让 AI 助手替你操作 Kaggle 免费 GPU

Kaggle 每周给每个账号 30 小时免费 GPU（T4 x2）。本 skill 让 AI 助手直接引导并执行全流程：
**注册配 key → 环境自检 → hello world 确认"卡在手上" → 日常"提交任务-等算力-收结果"闭环**。
人类可读的完整教程在 `references/`，本文件是 agent 的操作手册。

## When to Activate

| 用户处境 | 你该做什么 |
|---|---|
| 没有 Kaggle 账号 / 卡在注册验证 | 走【阶段 0】陪跑注册，生成 kaggle.json |
| 拿到 kaggle.json，不确定环境行不行 | 跑 `python scripts/doctor.py`，一次暴露全部问题 |
| 想先确认链路和 GPU 可用 | 走【阶段 2】两阶 hello world，到此为止，别预设用途 |
| 要提交/已有任务在跑 | `python scripts/kwatch.py -p <kernel目录>` 一键 push→轮询→拉结果 |
| 想知道这周还剩多少 GPU 时长 | `python scripts/quota.py` |
| 要传大文件（模型/数据集，GB 级） | `python scripts/uploader.py -p <dataset目录>`，先看【大陆网络】一节 |
| 要跑生图/推理等具体应用 | 读 `references/t2i-case-study.md` 照抄改造 |
| 问"kernel/dataset/output/commit 是什么" | 查下方概念速览；追问再给 `references/concepts.md` |

## 30 秒概念速览

- **Kernel**：Kaggle 云端执行单元（script 或 notebook）。本地写 `kernel.py` + `kernel-metadata.json`，`kaggle kernels push` 上去跑。
- **Dataset**：云端文件仓。本地目录 + `dataset-metadata.json`，`kaggle datasets create/version` 上传。kernel 里挂载后从 `/kaggle/input/<slug>/` 读。
- **Output**：kernel 运行产物，落 `/kaggle/working/`。commit 模式的 output 持久保存（上限 20GB），`kaggle kernels output` 拉回本地。
- **commit vs interactive**：push 触发的是 commit 运行（无人值守、12h 上限、output 持久）；网页里手点的是 interactive 会话（断线即丢未保存内容）。本 skill 全程用 commit 模式。
- **配额**：GPU 每周 30h（T4 x2 或 P100），`kaggle quota`（CLI 2.x）实时查余量；跑 CPU kernel 不占 GPU 配额。
- **排队**：高峰期 kernel push 后停在 QUEUED 属正常；status 显示 RUNNING 才开始计时。
- **TPU**：也存在配额体系，本 skill 不覆盖（生图/推理场景 GPU 为主）。

## 操作手册（Workflow）

### 阶段 0：陪跑注册（人类动手，AI 引导）

注册/手机验证/生成 token 必须人类完成，你的职责是逐步引导 + 话术安抚，**绝不经手凭据文件**：

1. 指引用户开 https://www.kaggle.com/account/login 用 Google 账号注册（或邮箱注册）。
2. 手机验证：Settings → Phone Verification。大陆 +86 手机号可收验证码；若收不到，建议换 Google 账号绑定境外号或稍后重试——此步过不了就没有 GPU 权限。
3. 生成 API token：Settings → API → **Create New Token**，浏览器下载 `kaggle.json`。
4. 指引用户把 `kaggle.json` 放到主目录 `~/.kaggle/kaggle.json`（Windows 即 `C:\Users\<用户名>\.kaggle\kaggle.json`），**不要放进本仓库/任何 git 目录**。类 Unix 系统补一句 `chmod 600 ~/.kaggle/kaggle.json`。
5. 备选路径（CLI 2.x 已登录用户）：`kaggle auth login` 生成 `~/.kaggle/access_token`，doctor 能识别；但**跨工具通用性以 kaggle.json 为准**，主推前者。
6. 完成后跑阶段 1 验证。

### 阶段 1：环境自检

```bash
python scripts/doctor.py          # 逐项体检，末行摘要，问题项 FAIL 退出码 1
python scripts/doctor.py --json   # 机器可读结果
```

检查项：Python 版本 / kaggle CLI 装没装 / 凭据位置与形态 / 代理连通（kaggle.com 与 storage.googleapis.com 分开测）/ GPU 配额。按 FAIL 项逐个修复再重跑，全绿才进阶段 2。

### 阶段 2：两阶 hello world（确认"卡在手上"，到此为止）

**第一阶 CPU 验链路**（不占 GPU 配额）：新建目录，放两个文件——

`kernel.py`：
```python
print("hello from kaggle cpu")
import sys; print(sys.version)
```

`kernel-metadata.json`（`your-username` 换成实际用户名）：
```json
{
  "id": "your-username/hello-cpu",
  "title": "hello-cpu",
  "code_file": "kernel.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": false,
  "enable_internet": false
}
```

跑 `python scripts/kwatch.py -p hello-cpu/`，看到拉回的 output 里有两行打印 = push→run→output 全链路通。

**第二阶 GPU 自检**：把 metadata 里 `id`/`title` 换成 `hello-gpu`、`enable_gpu` 改 `true`，kernel.py 换成：

```python
import torch
print("cuda:", torch.cuda.is_available())
print("device:", torch.cuda.get_device_name(0))
```

再次 kwatch，output 出现 `cuda: True` 和 `NVIDIA Tesla T4` = 免费卡已到手。**skill 不预设 GPU 用途**，用户想干什么去 references 找对应教程。

### 阶段 3：日常闭环（提交-等算力-收结果）

```bash
python scripts/kwatch.py -p <kernel目录>              # push→轮询→完成后自动拉 output 到 <kernel目录>/output/
python scripts/kwatch.py -p <kernel目录> --watch-only # 已 push 过，只盯状态和拉结果
python scripts/quota.py                               # 提交前查这周还剩几小时
python scripts/uploader.py -p <dataset目录>           # 传大文件：代理自检+308进度+断点重试
```

节奏建议：本地决策好参数 → push KB 级 kernel（模型走 Dataset 挂载，不随 kernel 传）→ 干别的去 → kwatch 通知完成 → 拉 output 评审。完整方法论见 `references/pipeline-pattern.md`。

## CLI 速查（kaggle 原生命令，2.x 实测）

```bash
kaggle config view                          # 看当前配置
kaggle auth login                           # 2.x OAuth 登录（备选凭据路径）
kaggle quota                                # 本周 GPU/TPU 配额余量（需 2.x）
kaggle kernels init -p <dir>                # 生成 kernel 模板（metadata 字段以此为准）
kaggle kernels push -p <dir>                # 提交并触发 commit 运行
kaggle kernels status -k <user>/<slug>      # 查最近一次运行状态
kaggle kernels logs -k <user>/<slug>        # 看运行日志
kaggle kernels output -k <user>/<slug> -p <outdir>   # 拉取 output 产物
kaggle kernels list --mine                  # 我的 kernel 列表
kaggle kernels delete -k <user>/<slug>      # 删除
kaggle datasets init -p <dir>               # 生成 dataset 模板
kaggle datasets create -p <dir>             # 首次上传（平铺结构最省事，见 QA 子目录坑）
kaggle datasets version -p <dir> -m "msg"   # 追加版本（自带断点续传底子）
kaggle datasets status -k <user>/<slug>     # 上传/处理状态
kaggle datasets files -k <user>/<slug>      # 列云端文件
kaggle datasets delete -k <user>/<slug>     # 删除
```

kernel-metadata.json 关键字段：`id`（`用户名/slug`）/ `code_file` / `kernel_type`（script|notebook）/ `enable_gpu` / `enable_internet` / `dataset_sources`（挂载自己的 dataset，填 `["用户名/dataset-slug"]`）。以 `kaggle kernels init` 生成的模板字段为准。

## 大陆网络特别说明（坑最多的一节）

1. **API 域可直连，上传域被墙**：`www.kaggle.com` 的 API 请求大多能直连，但大文件 blob 上传走 `storage.googleapis.com`——**必须代理**，否则静默失败（exit 1 零报错，能坑一下午）。
2. **配置优先级**：环境变量 `HTTPS_PROXY`/`HTTP_PROXY` > 本仓 `config/settings.json` 的 `proxy` 字段（模板见 `settings.example.json`，真值已 gitignore）。kaggle CLI 只认环境变量，脚本四件套会代读 settings.json 注入。
3. **上传速度别用第三方测速推算**：代理上行测速 ~1.5MB/s 时 GCS 接收端实测 ~8MB/s，以 uploader 的 308 探针实时读数为准。
4. 上传前先跑 `python scripts/uploader.py --probe-only -p <dir>`：代理不通会直接说人话，不进玄学环节。

## 红线与纪律

- **凭据零入库**：kaggle.json / access_token 只存在于用户主目录 `~/.kaggle/`；任何情况下不复制进仓库、不写进日志、不 echo 内容。
- **ToS 友好**：表述统一用"免费额度利用 / free GPU quota"；不做多账号、不写额度规避类内容，用户问就说明这是 Kaggle 服务条款红线。
- **不改云端别人的东西**：delete/overwrite 操作前必须向用户复述目标 slug 并确认。
- 脚本契约（四件套统一）：退出码 0=健康/成功、1=异常；stdout 末行 `[脚本名] OK/FAIL: 摘要`；`--json` 输出机器可解析结果。

## references 索引（人类可读，按需分发）

| 文件 | 内容 | 何时给用户 |
|---|---|---|
| `references/getting-started.md` | 注册到两阶 hello world 全程图文教程 | 新用户 / 首次配环境 |
| `references/concepts.md` | 概念详解：kernel/dataset/output/commit vs interactive/12h/20GB/排队 | 概念追问 |
| `references/pipeline-pattern.md` | "本地决策+云端执行"异步流水线方法论 | 想建自己的常态化任务流 |
| `references/t2i-case-study.md` | SDXL 生图完整案例（ModelScope 下载→上传→kernel→拉图） | 生图/推理实战 |
| `references/qa.md` | 实测踩坑合集（pip -U 卡死/上传静默失败/403 暂态/子目录 skip 等） | 报错对号入座 |

用户反馈按 `feedback/ISSUES.jsonl` 格式追加（date/type/severity/note 四字段，一行一条 JSON）。
