# 新手入门：从零到"免费 GPU 在手"

> 本文假设你完全没有 Kaggle 经验，也不需要任何人帮你——照着做就行。全部流程在 2026-10 实测通过（Windows 11 + 大陆网络）。
>
> 如果你是由 AI 助手（opencode / claude code 等）陪伴阅读本文，直接把本文交给它，让它逐步引导你。

## 0. 你将得到什么

Kaggle（Google 旗下数据科学平台）给每个通过手机验证的账号**每周 30 小时免费 GPU**（Tesla T4 x2 或 P100，12GB 显存/卡），够跑 SDXL 生图、小模型训练、推理批处理。本文带你走完全部前置流程，并用两个 hello world 确认"卡真的在手上"。

**成本：0 元。风险：仅一个 Google 账号 + 手机号验证。**

## 1. 注册账号（5 分钟）

1. 打开 <https://www.kaggle.com/account/login> ，点 **Register with Google**（推荐，用现成 Google 账号）或邮箱注册。
2. 填昵称、选感兴趣领域（随便选，不影响功能）。

## 2. 手机验证（关键门槛，不过这关没有 GPU）

1. 登录后进入 **Settings**（右上角头像 → Settings）。
2. 找到 **Phone Verification**，输入手机号。大陆 **+86 号码可以收到验证码**，直接验证。
3. 若反复收不到验证码：换个时段再试，或换 Google 账号绑定其他号码。此步无解则后续全部免谈。

验证成功的标志：在网页 Notebook 里能选中 GPU 加速器（Settings 侧栏 **Accelerator** 出现 GPU T4 x2 / GPU P100 选项）。

## 3. 生成 API token（拿到 kaggle.json）

我们的工作方式是**命令行驱动**：本地写代码 → 命令推送到 Kaggle 云端跑 → 命令拉回结果。这需要一把"钥匙"：

1. 仍在 **Settings** 页，找到 **API** 区块，点 **Create New Token**。
2. 浏览器自动下载一个 `kaggle.json` 文件。**这个文件就是钥匙，等同密码，不要发给任何人、不要放进任何 git 仓库。**
3. 把它放到你主目录的 `.kaggle` 文件夹：

| 系统 | 目标路径 | 操作 |
|---|---|---|
| Windows | `C:\Users\<你的用户名>\.kaggle\kaggle.json` | 资源管理器地址栏输入 `%USERPROFILE%\.kaggle` 回车（没有文件夹就新建），把 kaggle.json 拖进去 |
| macOS / Linux | `~/.kaggle/kaggle.json` | `mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/ && chmod 600 ~/.kaggle/kaggle.json` |

> 备选：新版 kaggle CLI（2.x）支持 `kaggle auth login` 浏览器授权，生成 `~/.kaggle/access_token`，效果等同。但 kaggle.json 是跨版本通用的经典格式，本文以此为主。

## 4. 安装工具（2 分钟）

需要 Python 3.8+ 和 kaggle 命令行工具：

```bash
pip install kaggle
```

## 5. 环境自检（1 分钟）

在本仓库（skill 文件夹）根目录跑：

```bash
python scripts/doctor.py
```

预期输出类似：

```text
[PASS] python: 3.12.4
[PASS] kaggle-cli: kaggle CLI 2.2
[PASS] credentials: kaggle.json（C:\Users\you\.kaggle）
[PASS] api-connect: www.kaggle.com 可达（HTTP 200）
[PASS] gcs-connect: storage.googleapis.com 可达（HTTP 400，代理: env）
[PASS] gpu-quota: GPU 余量 30.00h/30.00h，refreshAt ...
[doctor] OK: 6/6 项通过
```

哪项 FAIL 就修哪项：

- **kaggle-cli FAIL** → `pip install kaggle`
- **credentials FAIL** → 回到第 3 步检查文件位置和文件名
- **api-connect / gcs-connect FAIL** → 大陆网络问题，见下框

> **大陆用户必读**：Kaggle 网页和 API（www.kaggle.com）大多可以直连，但**大文件上传走 storage.googleapis.com（被墙）**。不传大文件可以先不管；要传模型/数据集（GB 级）时必须开代理软件，并设置环境变量 `HTTPS_PROXY=http://127.0.0.1:<代理端口>`（端口看你自己的代理软件，常见 7890/7897/10809），或复制 `config/settings.example.json` 为 `config/settings.json` 填入代理。上传器脚本 `scripts/uploader.py` 会自动读取。更多细节见 [qa.md](qa.md)。

## 6. 两阶 hello world

### 第一阶：CPU 跑通链路（不花 GPU 配额）

这阶只验证一件事：本地写的东西能推上去、云端能跑、结果能拉回来。

**新建文件夹 `hello-cpu`**，放两个文件：

`hello-cpu/kernel.py`：

```python
print("hello from kaggle cpu")
import sys
print(sys.version)
```

`hello-cpu/kernel-metadata.json`（**把 `your-username` 换成你的 Kaggle 用户名**，就是网页右上角头像处的那个 ID）：

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

提交并等结果（一键脚本会自动：预检 → 推送 → 轮询状态 → 完成后把结果拉到 `hello-cpu/out/时间戳_任务指纹/`）：

```bash
python scripts/kwatch.py -p hello-cpu
```

第一次 push 会创建这个 kernel，约 1-3 分钟跑完。看到末行 `[kwatch] OK: ... COMPLETE ...` 且 `hello-cpu/out/` 里出现带时间戳的结果目录，链路即通。

> 手动等价命令（了解即可）：`kaggle kernels push -p hello-cpu` → `kaggle kernels status -k your-username/hello-cpu` → `kaggle kernels output -k your-username/hello-cpu -p hello-cpu/output`

### 第二阶：GPU 自检（确认卡在手上）

复制 `hello-cpu` 为 `hello-gpu`，改两处：

- `kernel-metadata.json`：`id` 和 `title` 里的 `hello-cpu` 都改成 `hello-gpu`，`enable_gpu` 改为 `true`
- `kernel.py` 换成：

```python
import torch
print("cuda:", torch.cuda.is_available())
print("device:", torch.cuda.get_device_name(0))
```

```bash
python scripts/kwatch.py -p hello-gpu
```

output 里出现 `cuda: True` 和 `NVIDIA Tesla T4`——**恭喜，每周 30 小时的免费算力已经在你手里了。**

> GPU kernel 从 push 到 COMPLETE 一般 3-6 分钟（含排队和环境准备）。之后每次改代码重跑，`kaggle kernels push` 同一个 `id` 就是新版本。
>
> kwatch 每次提交前会自动"预检"（估时、查 GPU 余量），hello world 这种小任务直接忽略即可；跑大任务时它会帮你把关。

## 7. 下一步去哪

| 你想… | 去读 |
|---|---|
| 弄懂 kernel/dataset/output 这些概念 | [concepts.md](concepts.md) |
| 传自己的大模型/数据上去跑 | [t2i-case-study.md](t2i-case-study.md)（含完整上传实战） |
| 建立"本地决策+云端执行"的常态化工作流 | [pipeline-pattern.md](pipeline-pattern.md) |
| 报错了，对号入座 | [qa.md](qa.md) |
| 查这周还剩几小时 GPU | `python scripts/quota.py` |
