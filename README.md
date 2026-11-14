# 量化实验任务与结果中心

- **[任务列表](tasks/README.md)**：实验名称、待运行／运行中／完成／失败状态、完整源码与配置包。
- **[结果列表](results/README.md)**：每项实验的完整训练结果下载。
- **[给服务器 Codex 的说明](SERVER_CODEX.md)**：领取、运行、回传的操作规则。

平时由提交端 AI 把完整项目打包成一个任务；执行端 AI 下载运行，将模型参数、日志、指标和图一起回传。两端通过 task_id 对应。

这是基于私有 GitHub 仓库 `ai-macro-quant/experiment-queue` 的轻量任务队列。Python 3.10+ 标准库实现，GitHub CLI (`gh`) 负责身份认证与上传。当前仅提供提交、领取、下载、结果归档、列表和索引刷新；不会自动运行任务，也不会自行启动训练。队列初始为空。

## 准备

在提交端和执行端安装并登录 `gh`，确认对私有仓库有读写权限。工作目录是 `实验任务中心`。`tasks/README.md` 和 `results/README.md` 是可刷新的索引；具体 manifest、状态和结果记录分别存于 `tasks/`, `states/`, `results/`。

任务源包会包含完整项目中的文本源码、配置、文档、依赖说明和测试。排除 `.env*`、`.git`、虚拟环境、缓存、训练输出目录、报告目录和常见二进制模型/数据文件；发现疑似硬编码凭据时会中止并指出文件。建议在独立临时副本中提交源码，仔细核对排除目录列表，确保训练所需源码没有放在被排除目录。

## 提交任务

`--command-json` 是 JSON argv 数组，不经过 shell。它必须引用 `{python}`、`{env_file}`、`{output_dir}` 三个占位符，供执行端分别替换成本机解释器、数据库凭据文件和该任务独立结果目录。不要把密码写入命令或项目包。

```bash
python3 experiment_queue.py submit \
  --repo ai-macro-quant/experiment-queue \
  --project /path/to/full-training-project \
  --task-id example-20261114-a \
  --title "实验简述与数据区间" \
  --command-json '["{python}", "-u", "train.py", "--config", "config.yaml", "--env-file", "{env_file}", "--output-dir", "{output_dir}"]' \
  --required-json '["metrics.json", "best.pt", "epoch_ic.png", "full_cumulative_ic_rankic.png"]'
```

`required-json` 使用结果目录内的相对路径 glob，缺省 `[]`。使用唯一 task-id；提交后任务源包和 manifest 不可覆盖。开始前确保 `tasks/`、`states/`、`results/` 目录已经存在于远端仓库。

## 查看、下载和归档

```bash
python3 experiment_queue.py list --repo ai-macro-quant/experiment-queue
python3 experiment_queue.py refresh-index --repo ai-macro-quant/experiment-queue
python3 experiment_queue.py claim --repo ai-macro-quant/experiment-queue --task-id example-20261114-a --worker training-vm-01
python3 experiment_queue.py download --repo ai-macro-quant/experiment-queue --task-id example-20261114-a --destination /path/to/new/task-dir
python3 experiment_queue.py complete --repo ai-macro-quant/experiment-queue --task-id example-20261114-a --worker training-vm-01 --claim-token TOKEN_FROM_CLAIM --results /path/to/task-output --exit-code 0
```

领取成功会输出含 `claim_token` 的 JSON 收据。把收据保存在执行机本地的独立位置，归档时使用；不要混入训练输出或日志。claim Git ref 是并发互斥依据，状态 JSON 仅供查看。已有 claim 不会自动过期或接管。任务代码在本地下载后仍需由维护者/执行端 AI 按 `SERVER_CODEX.md` 操作。

结果包包含整个输出目录（包括模型、图、CSV、日志和 JSON），并带 SHA256 清单；超过约 1.8 GiB 时按分片上传。上传后 CLI 会下载远端 asset 再校验，之后才写结果记录和状态。失败退出码或缺少必要产物会标记 `failed`，但诊断文件仍会上传。不得因图片生成成功就声称训练成功。

## 状态与限制

状态流转为 `queued -> running -> completed|failed`。包和结果 release tag 使用 `task-<id>`、`result-<id>`。代码不会执行任务命令，也不运行定时器。上传后遇到网络中断时，先查看远端资产、状态与 claim，不要另建冲突任务；结果归档重试必须保持原输出内容。本机已完成 GitHub 合成任务的提交、互斥领取、下载、回传和重复上传校验，详情见 [验证记录](verification/2026-11-14.json)。执行端仍需核对自己的 Python、网络和数据库环境。

结果回传重试时应保持文件、文件时间戳、开始／结束时间和真实退出码一致。`--started-at`、`--finished-at` 可填写实际 ISO 时间，不填则记录 unknown。源包上传后中断或任务状态不明确时，先核查远端，不要覆盖旧 task_id。

[当前阶段2任务示例](examples/phase2_master_28.json) 是一个不排队的说明模板。批量实验还需要检查每个子实验完成情况，不能只看文件 glob 有一次匹配；例如该示例必须确认 summary.csv 中28项全部完成。

本地检查：`python3 -m unittest -v test_experiment_queue.py test_queue_protocol.py`。

