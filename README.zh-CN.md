# Autonomous Research Orchestrator

面向实证研究的 Codex skill：Brain 决定科学里程碑，Luna 执行和调试，以证据价值决定是否继续。[English](README.md) · [Skill](SKILL.md) · [更新记录](CHANGELOG.md)

- 主文档只保留稳定目标/约束及上一阶段与当前/下一阶段有关的信息。
- `research_history.md` 维护完整方法、实际工作、结果、失败修复、决策关系和原始证据链接，并记录用户/agent 的实际贡献，支持溯源和 CV 撰写。
- 每个工作项收尾时自动维护，不等用户提醒；正常恢复不全文加载历史。
- 原生 Luna 与 CLI Luna 使用同一科学合同；保留平台正常检查和实验模型边界。
- 修复属于原实验；worker、交接和文档维护不增加科学阶段数。
- 执行完成、证据有效、科学成功分开记录。
- 用户暂停、单一 owner、累计预算跨 Brain 保持；低信息价值循环触发复盘。

## 使用

将目录置于个人 Codex skills 文件夹，然后调用：
```text
使用 $autonomous-research-orchestrator 推进下一个关键科学里程碑，
自动维护短主文档和完整研究履历，继承现有授权、预算和停止指令。
```

修改 skill、维护文档或准备 CV，不等于恢复实验。旧实验结果、no-replay 身份和 protected 数据不会被本次规则追溯修改。

## 实现与验证

- [文档自动维护及 packet 格式](references/research-records.md)
- [worker 合同](references/brain-luna-contract.md)、[执行通道](references/luna-cli-runner.md)
- [运行与交接](references/runtime-and-rollover.md)
- [授权与停止](references/authorization-and-stopping.md)
- [初始化与既有项目接入](references/migration.md)

```powershell
python -m unittest discover -s scripts -p "test_*.py"
python scripts/simulate_autonomous_loop.py
```

测试使用本地临时项目和 mock，不调用付费 API、不运行真实科研 episode。脚本参数以各自 `--help` 为准；运行与记录工具只依赖 Python 标准库。
