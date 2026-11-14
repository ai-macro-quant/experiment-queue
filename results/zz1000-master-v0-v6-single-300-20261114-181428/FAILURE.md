# 失败原因

The launcher created the output directory before training. The training script expected to create it and exited with FileExistsError (WinError 183).

- 退出码 1

## 缺少的必需产物

- `summary.csv`
- `summary.md`
- `plan.json`
- `data_config.json`
- `dataset_snapshot.json`
- `trials/V0_MASTER/attempt_*/best.pt`
- `trials/V0_MASTER/attempt_*/last.pt`
- `trials/V0_MASTER/attempt_*/config.json`
- `trials/V0_MASTER/attempt_*/metrics.json`
- `trials/V0_MASTER/attempt_*/history.json`
- `trials/V0_MASTER/attempt_*/epoch_metrics.csv`
- `trials/V0_MASTER/attempt_*/epoch_ic.png`
- `trials/V0_MASTER/attempt_*/full_cumulative_ic_rankic.png`
- `trials/V0_MASTER/attempt_*/train_daily_metrics.csv`
- `trials/V0_MASTER/attempt_*/valid_daily_metrics.csv`
- `trials/V0_MASTER/attempt_*/test_daily_metrics.csv`
- `trials/V6_MASTER/attempt_*/best.pt`
- `trials/V6_MASTER/attempt_*/last.pt`
- `trials/V6_MASTER/attempt_*/config.json`
- `trials/V6_MASTER/attempt_*/metrics.json`
- `trials/V6_MASTER/attempt_*/history.json`
- `trials/V6_MASTER/attempt_*/epoch_metrics.csv`
- `trials/V6_MASTER/attempt_*/epoch_ic.png`
- `trials/V6_MASTER/attempt_*/full_cumulative_ic_rankic.png`
- `trials/V6_MASTER/attempt_*/train_daily_metrics.csv`
- `trials/V6_MASTER/attempt_*/valid_daily_metrics.csv`
- `trials/V6_MASTER/attempt_*/test_daily_metrics.csv`

[完整归档和诊断日志](https://github.com/ai-macro-quant/experiment-queue/releases/tag/result-zz1000-master-v0-v6-single-300-20261114-181428)
