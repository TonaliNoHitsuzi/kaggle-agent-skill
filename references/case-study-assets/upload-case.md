# 6.5GB 大文件上传 Kaggle Dataset 实录（uploader 脚本的需求来源）

> 2026-10-16 实测，文件：sd_xl_base_1.0.safetensors（6,938,078,334 字节），本机 → Kaggle Dataset `t2i-models`

## 时间线

| 阶段 | 现象 | 结论 |
|------|------|------|
| 第 1 次 create（无代理） | `Starting upload for file ...` 后静默退出，exit 1，零报错 | GCS 上传域被墙，连接层直接死 |
| 第 2 次 create（带代理 7897） | 同样静默退出 | 不是（只是）网络问题——见下 |
| 小文件对照（43B ping.txt） | 成功，进度条正常 | 链路/认证/代理全通，问题锁定大文件路径 |
| python 直调 + traceback | 真实开始上传（CLI 入口某处吞了过程输出） | API 本身没问题 |
| 中断后再跑 | 进度条 total 只剩 1.09G——**已自动断点续传** | kaggle CLI 自带 GCS resumable 机制，中断不白传 |
| 收尾 | 剩余 1.09G @ ~8MB/s 两分钟传完 | 全程有效上传时间 ≈ 15 分钟 |

## 关键事实

1. **上传必须走代理**（大陆环境）：Kaggle API 域可直连，但 blob 上传走 `googleapis.com`（被墙）。kaggle CLI 读 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量。
2. **CLI 有断点续传底子**：resume 状态存 `%TEMP%\.kaggle\uploads\<file>.json`（含 GCS createUrl + token），GCS resumable session 有效期数天；重跑同命令自动续传，日志显示 `Already uploaded N bytes. Will resume upload at ...`。
3. **静默失败极难诊断**：CLI 入口吞异常（exit 1 无输出），308 探针（`PUT` + `Content-Range: bytes */<size>` 查 GCS 已收字节）是唯一可靠的进度真相源。
4. **测速参考**：上行经代理实测 ~1.5MB/s（httpbin），但 GCS 接收端 ~8MB/s——别用第三方测速推算上传时长。

## uploader.py 的行为需求（从本实录导出）

- 上传前：自检代理连通（GET storage.googleapis.com），不通直接报人话
- 上传中：308 探针轮询显示"已传 X / 总量 Y @ 速度 Z"
- 失败后：自动重试（读 resume json 续传），重试间隔退避
- 退出码：0 成功 / 1 失败，`--json` 输出结构化结果
