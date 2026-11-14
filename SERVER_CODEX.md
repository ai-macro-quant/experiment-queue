# 执行端 Codex 交接说明

你在训练虚拟机上工作。先读本文件与任务 manifest，再领取并下载对应的不可变源包。仅在用户授权后启动训练或队列。维护者明确授权启动/处理队列后，可按创建时间从早到晚处理所有 `queued` 任务；每项使用独立输出目录并遵守本文，不需逐任务再次询问。没有这项队列启动授权时，只处理明确指定的 task-id。

## 操作约束

- 使用任务提交时冻结的代码包、配置、命令参数和数据设置，不自行改模型、学习率、股票池、因子或区间。
- 在训练机本地准备 Python 环境与凭据；数据库 env 文件留在机上，不上传，不进入结果目录。
- `command` 是 argv 数组，不是 shell 字符串。将 `{python}`、`{env_file}`、`{output_dir}` 替换为本机绝对路径后，用 subprocess argv 执行，不使用 `shell=True`、`eval` 或拼接 shell 命令。
- 每个任务使用独立空输出目录。保留所有输出：权重、配置快照、日志、逐日和 epoch 指标、图表与诊断文件。不能只上传 PNG。
- 只有实际命令退出码为 0、`required_patterns` 全部存在、上传并下载校验成功后，才可报告成功。CLI 会按这些条件标记 completed；其他情况标记 failed 并保留诊断产物。
- 不要杀掉、覆盖或接管其他任务。已有 claim 或状态含糊时停止，向维护者报告。

## 命令流程

在 `实验任务中心` 目录运行 `experiment_queue.py claim`，保留 stdout JSON 收据里的 claim token。运行 `experiment_queue.py download` 到一个新目录。读取 manifest 中的 `command` 并完成上述占位符替换，然后在本机执行。最后无论成功或失败都运行 `experiment_queue.py complete` 上传整个输出目录，并传入真实退出码、原 worker id 和 claim token。完成后报告 task-id、实际退出码、结果 SHA256、必要产物检查和输出位置。

领取、下载和上传命令示例见 `README.md`。不得在没有定时执行器配置和监控的情况下宣称队列会自动调度。


## 执行机首次部署

```cmd
git clone https://github.com/ai-macro-quant/experiment-queue.git C:\ExperimentQueue
cd /d C:\ExperimentQueue
gh auth status
python experiment_queue.py list --repo ai-macro-quant/experiment-queue
```

先保证 git、gh 和正确的 Python 已可用、已登录，并有本仓库读写权限。任务源码和工作目录建议放在虚拟机本地；需要共享输出时核对当前账户可访问的 UNC 路径。计划任务不应依赖交互会话中的 Z: 映射盘。

本次只提供 GitHub 端与本地管理工具，没有安装执行机调度器。用户授权持续维护队列后，可配置定时或常驻调度；每次先回传已经完成的任务，再领下一项。默认单任务并发，不中断旧作业。领取锁不自动过期；故障后先核对原进程和结果，再由维护者决定恢复上传或用新 task_id 重新训练。

## 完成检查

通配符匹配只证明存在产物，不证明批量任务全数完成。检查任务声明的 expected_runs、summary.csv、每个子实验的状态和文件；28次实验必须逐项核实。真实失败或缺少产物，保留错误日志并用非零退出码归档；不要将部分成功标为整批完成。记录实际开始和结束时间，在 complete 命令中通过 --started-at、--finished-at 传入，重复回传使用相同的时间参数。

给用户的汇报必须包括 task_id、状态、训练／验证／测试 IC 与 RankIC（若该任务产生）、GitHub 结果链接。若是四组实验，同时记录普通测试和固定测试；只用任务规定的验证集选模。
