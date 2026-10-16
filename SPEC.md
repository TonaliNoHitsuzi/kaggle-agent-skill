# Spec: kaggle-agent-skill —— Kaggle 免费 GPU 额度的 AI 助手操作手册（开源 skill）

**Status:** ready-for-agent
**Date:** 2026-10-16
**Source:** 孵化仓主会话（opencode·Zzy）拷问对齐，13 条共识

---

## ⚠️ 并行协作声明（先读这个）

本仓由**两个并行会话**协作完成，分工如下，**请勿越界写文件**：

| 会话 | 职责 | 文件领地 |
|------|------|---------|
| **隔壁会话（你）** | 本仓全部内容创作：SKILL.md 正文、references 教程、四脚本、README（中+英简）、LICENSE、QA 章节 | 本仓根全部文件 |
| **孵化仓主会话（笔者）** | ①实战素材供给（t2i 流水线案例代码/数据/踩坑记录，见下文"素材供给区"）②本机 skill 库 `tool-kaggle` 的脚手架登记（等本仓内容成型后拷贝剥离）③不写本仓任何其他文件 | 仅追加写入 `references/case-study-assets/`（素材供给区）；本仓其余文件对笔者只读 |

冲突规避规则：笔者不会再动本仓正文；你若需要案例素材，从 `references/case-study-assets/` 取（笔者持续供给），不要等笔者。

## Problem Statement

Kaggle 提供每周 30h 免费 GPU（T4 x2），但大陆用户面对三重门槛：注册/验证/取 key 流程懵、命令行工具链陌生、大文件上传与网络代理坑多。想白嫖算力的 AI 玩家缺一份"让我的 AI 助手直接替我操作 Kaggle"的说明书兼工具箱。

## Solution

一个可独立运作的 Agent Skill（GitHub 开源，中文主），装进任何 Agent（opencode / claude code / 等）后，AI 助手即可引导用户完成注册→配 key→自检环境→两阶 hello world 跑通 GPU→此后用脚本四件套 + 原生 CLI 完成日常"提交任务-等算力-收结果"闭环。非 agent 用户也能把 references 当人类可读说明书使用。附生图部署完整案例证明可行性。

## User Stories

1. As a 没 Kaggle 账号的用户, I want AI 一步步引导我完成注册/手机验证/生成 API token, so that 我不用自己摸索官网。
2. As a 拿到 kaggle.json 的用户, I want doctor 脚本自检（CLI 装没装/key 放没放对/代理通不通/GPU 配额余量）, so that 环境问题一次暴露。
3. As a 新手, I want 两阶 hello world（①CPU 跑通 push→run→output 链路 ②GPU 自检打印显卡型号）, so that 不涉及任何具体应用就确认"卡在手上"。
4. As a 大陆用户, I want 代理配置说明（含上传必须走代理访问 googleapis 的事实）, so that 不被静默失败坑一下午。
5. As a 有大文件（模型/数据集）的用户, I want 上传器脚本（诊断静默失败原因 + 308 探针显示已传字节 + 自动断点重试）, so that 几 GB 文件上传不再玄学。
6. As a 日常使用者, I want kwatch 一键 push→轮询→拉结果, so that 提交任务后不用手动反复查状态。
7. As a 配额敏感用户, I want quota 脚本查本周 GPU 时长余量, so that 合理规划任务。
8. As a AI 助手本身, I want SKILL.md 里有一份"何时做什么"的操作手册 + CLI 速查 + 概念词典（kernel/dataset/output/commit vs interactive/12h/20GB）, so that 我能替主人执行全流程。
9. As a 生图玩家, I want 完整可复制的 SDXL 部署案例（ModelScope 下载→Dataset 上传→kernel.py→拉图）, so that 照抄就能跑出自己的第一批 AI 图。
10. As a 踩坑幸存者, I want QA 章节收录实测坑（pip -U 依赖解析卡死、大文件上传静默失败、新 dataset API 403 暂态、dataset 子目录默认 skip 等）, so that 后来人不用重蹈。
11. As a 开源用户, I want MIT LICENSE + ToS 友好声明（表述用"免费额度利用"）, so that 合规安心。
12. As a 英文读者, I want 英文简版 README, so that 快速判断这仓库有没有用。

## Implementation Decisions

（13 条拷问共识，已与用户逐条确认）

1. **双线定位**：主线 agent skill（刚需 opencode 等 agent 软件）；副线人类可读说明书（references 按"没有人帮我看时也能读懂"标准写）。
2. **key 自供原则**：注册/验证/API token 由人类完成，skill 提供 AI 引导话术；凭据零入库（kaggle.json 留用户主目录 `~/.kaggle/`）。
3. **脚本四件套**（scripts/，python，基于 kaggle 官方库做增值封装，不重复造轮子）：
   - `doctor.py`：环境自检（CLI/key 位置/代理连通/GPU 配额）
   - `quota.py`：周配额余量查询
   - `kwatch.py`：push→轮询 status→完成后自动 output 拉取
   - `uploader.py`：大文件上传增强（诊断静默失败 + GCS 308 探针进度 + 断点自动重试循环）
4. **hello world 两阶制**：CPU print 验链路 → GPU 自检打印显卡型号，到此为止；skill 不预设 GPU 用途。
5. **生图案例**：教程级全可复制，放 references/；踩坑不混入正文，独立 QA 章节。
6. **含方法论层**：references 收"本地决策 + 云端执行"异步流水线模式说明（本地 AI 规划参数 → push KB 级任务 → 云端挂载 Dataset 模型执行 → output 拉回评审入库）。
7. **双发行版**：本仓（开源主源）+ 本机 skill 库 `tool-kaggle`（tool-hub 隐身子件，登记进 shephitsuji 注册库）；两仓自由生长、人工对齐、互注源头。
8. **本机差异物承载**：本机版 config/ 三件套（settings.json 真值：代理端口等机器相关配置，gitignored；settings.example.json 即 schema；settings.md 写重建步骤）。开源仓的 config/ 只带 example，不带真值。
9. **仓库名**：kaggle-agent-skill；skill 的 frontmatter `name: kaggle-agent`；本机件名 `tool-kaggle`。
10. **语言与许可**：中文主 + 英文简版 README；MIT；ToS 友好表述（"免费额度利用 / Free GPU quota"）。
11. **目录结构**（仓根平铺 = clone 即 skill 文件夹）：
    ```
    kaggle-agent-skill/
    ├── SKILL.md          # agent 入口：When to Activate / Workflow / CLI 速查 / 概念词典
    ├── scripts/          # 四件套
    ├── references/       # 人类可读：新手教程 / 概念详解 / 流水线模式 / 生图案例 / QA
    │   └── case-study-assets/   # 主会话素材供给区（实战代码/数据/日志）
    ├── config/           # settings.example.json（schema）
    ├── feedback/         # ISSUES.jsonl（用户反馈）
    ├── README.md         # 中文主 + 英文简版
    └── LICENSE           # MIT
    ```
12. **frontmatter**：按 Agent Skills 通用标准（name/description/license/compatibility/metadata），兼容 opencode 与 claude code 等主流 agent 的 skills 目录机制；正文不依赖任何特定 agent 私有功能，保证独立运作。
13. **同步纪律**：本仓改动 → 成型后由孵化仓主会话拷贝登记为本机 `tool-kaggle`（剥离本机不需要的 README 后填 config 真值）；反向改动（本机侧小修）人工对齐回本仓。

## 素材供给区（孵化仓主会话 → 你）

以下素材由笔者持续放入 `references/case-study-assets/`，你直接取用：

- `kernel.py`：SDXL 生图 kernel 完整可用代码（含模型路径自动探测、contact sheet 拼图、metadata 记录）
- `metadata.json`：首跑实测性能数据（SDXL @ T4：22-28s/张 @1024²/25步；6 张含加载全程 5 分钟）
- `debug-history.md`：三轮调试实录（pip -U 卡死 → 路径探测 → 成功），QA 章节的原料
- `upload-case.md`：6.5GB 大文件上传实录（直连静默失败 → 代理 + 断点续传 8MB/s 收尾）

## 测试 Seam（唯一的公共接口边界）

**四脚本的 CLI 行为契约**：`python scripts/<name>.py` 的退出码（0=健康/成功，1=异常）、stdout 末行摘要格式（人类一眼可读）、以及 `--json` 开关输出机器可解析结果。spec 验收 = 四脚本在新装环境上按此契约行为一致。

## Out of Scope

- 不做网页版说明书/静态站点
- 不做 ComfyUI 交互式会话教程（排队+断线机制不适合，已在主会话论证）
- 不覆盖 TPU（生图/推理场景 GPU 为主，TPU 仅在概念词典一句话提及）
- 不做多账号/额度规避类内容（ToS 红线）
