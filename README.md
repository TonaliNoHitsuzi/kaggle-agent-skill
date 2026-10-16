# kaggle-agent-skill

**让 AI 助手直接替你操作 Kaggle 免费 GPU 的 skill + 工具箱。**

Kaggle 给每个账号每周 30 小时免费 GPU（Tesla T4 x2）。大陆玩家面对三重门槛：注册/验证/取 key 流程懵、命令行工具链陌生、大文件上传与网络代理坑多。本项目把全部流程写成 **Agent Skill**——装进任意 AI 助手（opencode / claude code / 任何支持 skills 目录机制的 agent），AI 即可引导你完成：**注册配 key → 环境自检 → 两阶 hello world 跑通 GPU → 日常"提交任务-等算力-收结果"闭环**。没有 AI 助手也能用：`references/` 是纯人类可读说明书。

内置 SDXL 生图完整案例（ModelScope 下载 → 6.5GB Dataset 上传 → kernel 部署 → 拉图，单张 1024² 约 25 秒），照抄就能跑出你的第一批 AI 图。

## 快速开始

### 方式 A：装进 AI 助手（推荐）

把本仓库 clone 到你的 agent skills 目录（或直接引用本仓库路径）：

```bash
git clone https://github.com/<你的地址>/kaggle-agent-skill.git
```

之后对 AI 说"kaggle 白嫖 GPU"之类的需求即可，AI 会按 `SKILL.md` 的操作手册路由。

### 方式 B：当人类说明书用

从 [references/getting-started.md](references/getting-started.md) 开始读，20 分钟从零到"免费 GPU 在手"。

## 脚本四件套（scripts/）

| 脚本 | 一句话 | 用法 |
|---|---|---|
| `doctor.py` | 环境自检：CLI/凭据/代理/GCS/配额六项一次暴露 | `python scripts/doctor.py` |
| `quota.py` | 查本周 GPU/TPU 余量 | `python scripts/quota.py` |
| `kwatch.py` | 预检三连(估时/查余额/冒烟) → push → 心跳轮询 → 自动拉 output 到时间戳目录 | `python scripts/kwatch.py -p <kernel目录>` |
| `uploader.py` | 大文件上传增强：代理自检 + GCS 308 进度探针 + 断点重试 | `python scripts/uploader.py -p <dataset目录>` |

统一契约：退出码 0/1（成功/异常）；stdout 末行 `[脚本名] OK/FAIL: 摘要`；加 `--json` 输出机器可解析结果。依赖：Python 3.8+、`pip install kaggle`（2.x 为佳）。

## 仓库结构

```text
kaggle-agent-skill/
├── SKILL.md                      # agent 入口：操作手册/CLI速查/概念词典
├── scripts/                      # 四件套
├── references/                   # 人类可读说明书
│   ├── getting-started.md        #   新手：注册→key→hello world
│   ├── concepts.md               #   概念：kernel/dataset/output/配额
│   ├── pipeline-pattern.md       #   方法论：本地决策+云端执行
│   ├── t2i-case-study.md         #   实战：SDXL 生图全流程
│   ├── qa.md                     #   踩坑合集（对号入座）
│   └── case-study-assets/        #   案例原始档案（代码/数据/调试实录）
├── config/settings.example.json  # 本机配置模板（代理端口等；真值 settings.json 已 gitignore）
├── feedback/ISSUES.jsonl         # 用户反馈（date/type/severity/note，一行一条）
├── README.md
└── LICENSE                       # MIT
```

## 安全与合规

- **凭据零入库**：`kaggle.json` / access_token 只存于你本机 `~/.kaggle/`，本仓库不碰、不存、不传。
- **ToS 友好**：本项目是 Kaggle 官方免费额度的正常利用（free GPU quota utilization），不含多账号/额度规避内容——那是服务条款红线。
- 上传内容注意版权：私有自用一般无碍，公开发布受各自 license 约束。

## 反馈

往 `feedback/ISSUES.jsonl` 追加一行：

```json
{"date": "2026-10-16", "type": "bug|doc|idea", "severity": "P1|P2|P3", "note": "一句话描述"}
```

---

## English (brief)

**kaggle-agent-skill** turns your AI assistant (opencode / claude-code / any agent with a skills directory) into a Kaggle free-GPU operator. Kaggle grants every account 30 GPU-hours/week (T4 x2); this skill walks users through registration & API-token setup, environment self-checks, a two-stage GPU hello-world, and a daily `push → poll → pull` loop via four helper scripts (`doctor` / `quota` / `kwatch` / `uploader`). Includes mainland-China proxy guidance (uploads go through googleapis.com and silently fail without a proxy) and a fully reproducible SDXL text-to-image case study (6.5GB dataset upload → kernel deploy → ~25s per 1024² image on T4). All docs are Chinese-first and human-readable without any agent. MIT licensed. Never touches your credentials: `kaggle.json` stays in `~/.kaggle/`. ToS-friendly: plain free-quota utilization, no multi-account evasion.

Quick start: `git clone` into your agent's skills folder, or read [`references/getting-started.md`](references/getting-started.md) directly. Scripts: `python scripts/doctor.py` → `python scripts/kwatch.py -p <kernel-dir>`.
