# 概念详解：kernel / dataset / output / 配额与限制

> 读完本文你就能看懂 Kaggle 命令行工作流的全部名词。配合 [getting-started.md](getting-started.md) 食用。

## 一张图看懂数据流

```text
你的电脑                                Kaggle 云端
─────────                              ──────────────────────────────
hello-gpu/kernel.py        ──push──▶   kernel（脚本沙箱）
hello-gpu/kernel-metadata.json           │  挂载读取 /kaggle/input/<dataset-slug>/
                                        │  运行（CPU 或 GPU，最长 12h）
大文件目录/ ──datasets create/version──▶ dataset（云端文件仓，≤20GB）
                                        │  产物写入 /kaggle/working/
hello-gpu/output/          ◀──output──  output（持久保存，≤20GB）
```

**核心思想：代码很轻（KB 级）用 push 传；数据很重（GB 级）走 dataset；两者在云端汇合，结果从 output 拉回。**

## Kernel：云端执行单元

Kernel 是 Kaggle 云端的一个 Python 沙箱（也支持 R 等），分两种形态：

- **script**：纯 `.py` 脚本，从上到下跑完即止——**命令行工作流用这个**
- **notebook**：网页版 Jupyter，交互式——配合网页使用

本地用两个文件描述一个 kernel：

| 文件 | 作用 |
|---|---|
| `kernel.py` | 代码本体 |
| `kernel-metadata.json` | 配置：身份、开关、挂载来源 |

`kernel-metadata.json` 关键字段（以 `kaggle kernels init -p <目录>` 生成的模板为准）：

| 字段 | 说明 |
|---|---|
| `id` | `"用户名/kernel-slug"`，全局唯一身份。**同 id 重复 push = 提交新版本** |
| `title` | 显示名（需与 slug 对应，一般保持一致） |
| `code_file` | 代码文件名（老版 CLI 字段名可能是 `file_name`） |
| `kernel_type` | `script` 或 `notebook` |
| `is_private` | 是否私有。个人工作流保持 `true` |
| `enable_gpu` | 是否开 GPU（占 GPU 配额；`false` 跑 CPU 不占） |
| `enable_internet` | 运行中是否允许出网访问（装 pip 包需要 `true`） |
| `dataset_sources` | 挂载自己的 dataset：`["用户名/dataset-slug"]`，运行时出现在 `/kaggle/input/dataset-slug/` |
| `competition_sources` / `kernel_sources` | 挂载比赛数据 / 其他 kernel 的 output |

## Dataset：云端文件仓

用途：存放模型权重、训练数据等大文件，供 kernel 挂载读取。

- 本地一个文件夹 + `dataset-metadata.json`（`kaggle datasets init` 生成），`create` 首传、`version -m "说明"` 追加版本。
- **总量上限 20GB**。
- **结构限制：默认平铺**——`create` 命令默认跳过子文件夹（提示需要 `--dir-mode`），最省事的做法是数据全部平铺在根目录。
- kernel 里通过 `dataset_sources` 挂载后，路径为 `/kaggle/input/<dataset-slug>/...`。
- 上传走 Google Cloud Storage（googleapis.com），**大陆网络必须配代理**，详见 [qa.md](qa.md) 与 `scripts/uploader.py`。

## Output：运行产物

- kernel 里写入 `/kaggle/working/` 的一切都是 output：图片、JSON、模型文件……
- **commit 运行的 output 持久保存（上限 20GB）**，随时可拉：`kaggle kernels output -k 用户名/slug -p 本地目录`
- 想拿到结构化结果，习惯是 kernel 末尾写一个 `metadata.json`（记录参数、耗时、产物清单），配合图片/数据文件一起拉回。

## commit vs interactive：两种运行方式

| | commit 运行（本 skill 全程使用） | interactive 会话（网页手点） |
|---|---|---|
| 触发 | `kaggle kernels push` | 网页里点 Run |
| 无人值守 | 是，关机断网照跑 | 否，依赖浏览器会话 |
| 时长上限 | 12 小时 | 12 小时 |
| output | 持久保存，随时拉取 | 只保存你手动 Save Version 的内容，断线丢未保存的 |
| 状态查询 | `kaggle kernels status` | 网页看 |

结论：**凡是"提交后可以走人"的任务，一律 commit 模式**。这也正是 kwatch 脚本存在的意义。

## 配额与限制速查

| 资源 | 限额 | 备注 |
|---|---|---|
| GPU（T4 x2 / P100） | 每周 30 小时 | 按账号计；`python scripts/quota.py` 或 `kaggle quota`（CLI 2.x）实时查 |
| TPU | 每周 20 小时 | 本 skill 不覆盖 TPU 工作流 |
| 单次 kernel 运行 | 12 小时 | 超时自动杀 |
| output / dataset | 各 20GB | 大产物考虑压缩/抽样 |
| CPU kernel | 无周配额概念 | 验证链路用 CPU kernel，省 GPU 配额 |

注意：配额按"GPU 内核实际运行时间"计——排队（QUEUED）不计时，从真正 RUNNING 开始算。

## 排队与状态机

push 之后 kernel 走这个状态流：

```text
QUEUED（排队等卡）→ RUNNING（运行中）→ COMPLETE（成功）
                                  └→ ERROR / CANCEL（失败/取消）
```

- 高峰期 QUEUED 可能耗几分钟到几十分钟；kwatch 会自动轮询，你该干嘛干嘛。
- 刚 push 完的半分钟内，`kaggle kernels status` 可能报 Permission denied / NotFound——slug 尚未全球生效，等一下再查（kwatch 已内置容忍）。
- 状态里的 `UNKNOWN` 一般是暂态观察不到，继续等即可。

## 可见性：public / private

- kernel 和 dataset 都有公开/私有之分（metadata 里 `is_private`）。
- 公开的会出现在你的主页，可被搜索。个人工作流默认私有。
- **上传内容前想一想版权**：私有用途放模型权重一般没问题；公开发布受各自 license 约束。

## 惯用目录结构（本地）

```text
我的kaggle项目/
├── datasets/
│   └── t2i-models/              # 大文件 + dataset-metadata.json（只上传一次，之后 version）
├── kernels/
│   ├── hello-gpu/               # 轻量 kernel：kernel.py + kernel-metadata.json
│   │   └── output/              # kwatch 自动拉回的结果落这里
│   └── t2i-runner/
│       ├── kernel.py
│       ├── kernel-metadata.json
│       └── output/
└── config/settings.json         # 本机代理等配置（gitignore，别提交）
```
