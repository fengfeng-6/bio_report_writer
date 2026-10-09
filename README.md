# 生态修复报告生成 Agent

将生态修复报告生成拆分为可审计的确定性核心（指标计算、问题诊断、措施过滤）与受约束的
LLM 环节（跨问题分析、正文撰写），中间产物统一落在 `ReportIR` JSON 中，供渲染、
校验和归档复用。

部署分两条互不调度的独立链路：云主机只运行 Dify 1.16.1 与合成数据 smoke 服务，
用于证明端到端报告生成可行；开发、完整测试和真实项目生产都在超算 Slurm Job 中完成。
真实工作簿不会上传云主机，云主机也不会向超算提交任务。

## 两条报告链路

仓库中并存两条链路，分别对应不同阶段的产物格式：

**确定性链路**（`pipeline.py` → `models.ReportIR`，`schema_version=0.1.0`）：只做
指标趋势计算、规则化问题诊断和措施过滤，不调用 LLM。入口是
`eco-report build / render-markdown / render-docx / render-pdf / render-all`。

**MVP 审核链**（`mvp.py` → dict 形式 `ReportIR`，`schema_version=2.3-mvp` /
`2.4-mvp`）：在确定性事实之上加入受约束的 LLM 分析与正文撰写，是当前实际交付使用的
链路。入口是 `scripts/build_mvp_review.py`（单项目）和
`scripts/build_mvp_batch.py`（多项目批量，带断点续跑）。

```text
结构化 XLSX
  → 确定性指标事实与问题诊断（mvp.py / evidence.py / measures.py）
  → 受约束 LLM Analysis Builder（analysis_builder.py）
  → 无优先级排序的措施匹配（strategy.py）
  → ReportIR 2.3-mvp
  → 过滤后的 Writer 输入（llm_writer.py 的 build_writer_payload）
  → LLM Writer（mvp_writer.py）
  → 问题级边界修复
  → Markdown / DOCX / PDF / 科研图表（mvp_charts.py）
  → 自动校验与基准计量
```

关键约束：

- 数据不足和不适用的问题只保留在 `ReportIR` 内部追溯，不进入正文、汇总表、图表或
  措施章节。
- 问题优先级评分与排序模块未启用（`audit.priority_module_enabled=false`），措施按
  对应问题分组，不单独排序。
- Writer 只接收 `build_writer_payload` 过滤后的输入：已剔除数据不足/不适用问题、
  规则编号、来源审计和内部字段；输出经过事实边界、反向证据、机器语言和优先级语言
  校验，越界时只回退受影响的问题段落，不推翻整篇。
- 图表由 Matplotlib 统一低饱和配色生成 SVG/PDF/300dpi PNG；DOCX/PDF 表格使用标准
  三线表；正文段落使用两字符首行缩进；DOCX 写入可点击的缓存目录与 TOC/PAGEREF 域。

## 目录结构

```text
src/eco_report_agent/
  ingest.py, xlsx_reader.py     XLSX 读取与逐行观测值解析
  metrics.py                    趋势计算（斜率、分段趋势特征）
  models.py                     确定性链路的 ReportIR 数据类
  diagnosis.py, evidence.py     规则化问题诊断与证据构建
  measures.py, strategy.py      候选措施过滤与按问题匹配
  validation.py                 确定性链路的输入/输出校验
  pipeline.py                   确定性链路主入口 build_report_ir
  markdown_report.py, docx_report.py, pdf_report.py, document_blocks.py
                                 三种格式渲染与结构校验
  report_plan.py, report_validation.py
                                 固定章节结构与生成后事实/语言校验（确定性链路）
  full_report.py                确定性链路一次性生成三格式并写产物清单
  source_catalog.py             参考附件（Markdown/DOCX/PDF/XLSX）登记为带角色来源
  service.py                    轻量 HTTP API，供云主机 smoke 自检
  slurm_jobs.py, submit_report_job.py（脚本）
                                 Slurm 任务请求构造，默认 dry-run
  cloud_release.py, smoke_fixture.py
                                 云主机发布包打包与合成 smoke 工作簿

  mvp.py                        MVP 审核链的 ReportIR 构建（指标事实、问题诊断）
  analysis_builder.py           受约束 LLM 跨问题分析（Analysis Builder）
  llm_writer.py                 Writer 输入过滤与 LLM Writer 后端
  mvp_writer.py                 模板/LLM 正文渲染与问题级边界修复、语言校验
  mvp_charts.py                 Matplotlib 科研图表（植被、景观、诊断矩阵）
  mvp_bundle.py                 单项目 MVP 审核包组装，记录各阶段耗时

  checkpointing.py              原子写入、SHA-256 指纹、断点续跑用的 checkpoint 读写
  llm_http.py                   通用 OpenAI 兼容 HTTP 请求封装，含重试与致命错误分类
  raw_batch.py                  从原始多项目 XLSX 拆分出逐项目快照
  batch_runner.py                多项目批量运行：分阶段并发爬升、失败重试、汇总统计
  snapshot_report.py            单项目快照 → 完整 MVP 报告包，供批量运行调用
  dify_deploy.py, dify_dsl.py    Dify DSL 生成与 Console 导入（默认 dry-run）
  diagnosis.py                  （见上）
  cli.py                        eco-report 命令行入口

config/
  trend_config.json             趋势判定阈值（最少年数、变化率阈值等）
  problem_rules.json            13 条问题规则：适用项目类型、所需指标、判定条件
  measure_knowledge.json        受控措施知识库：措施与目标问题、项目类型的映射
  analysis_config.json          指标→生态维度映射，供跨问题分析分组
  reportir_schema.json          ReportIR 2.3-mvp/2.4-mvp 的 JSON Schema
  report_style_config.json      DOCX/PDF 页面、字体、字号样式参数

scripts/
  build_mvp_review.py           单项目 MVP 审核包命令行入口
  build_mvp_batch.py            多项目批量命令行入口（prepare/run/resume/retry-failed）
  build_dify_dsl.py, deploy_dify_dsl.py
                                 Dify DSL 生成与部署脚本
  build_cloud_release.py(.sbatch), build_container.sbatch
                                 云主机发布包与容器构建（在 Slurm Job 内执行）
  run_core_tests.sbatch         在 Slurm Job 内运行 pytest
  submit_report_job.py          提交确定性链路报告生成 Job，默认 dry-run

deploy/
  Dockerfile, eco-report-agent.def   容器与 Apptainer 镜像定义
  cloud/                              云主机 smoke 部署（Compose、SSRF 白名单、探针）

dify/
  eco_report_workflow.template.yml   工作流模板（提交至仓库）
  eco_report_workflow.yml            由模板生成的可导入 DSL（Dify 1.16.1 / DSL 0.7.0）

tests/
  73 个测试用例，覆盖趋势计算、诊断规则、措施过滤、MVP 审核链、批量运行、文档渲染、
  Dify DSL/部署、Slurm 任务构造、来源登记等。
```

## 命令行使用

MVP 审核链（单项目，实际使用路径）：

```bash
PYTHONPATH=src python scripts/build_mvp_review.py \
  --input private_inputs/source/input.xlsx --project coal --fid 33 --year 2025 \
  --project-name 胜利矿 \
  --writer-mode llm --writer-base-url https://models.sjtu.edu.cn/api/v1 --writer-model qwen \
  --analysis-mode llm \
  --output-dir outputs/jobs/<request-id>
```

`--writer-mode template` 与 `--analysis-mode seed` 可在不调用 LLM 的情况下跑通全流程，
用于回归测试。LLM 模式下密钥从 `--writer-api-key-env`（默认
`ECO_REPORT_WRITER_API_KEY`）指定的环境变量读取，不接受命令行参数传入。

多项目批量（带断点续跑）：

```bash
PYTHONPATH=src python scripts/build_mvp_batch.py \
  --input private_inputs/source/raw_multi_project.xlsx --batch-id <batch-id> --year 2024 \
  --writer-base-url ... --writer-model qwen \
  --max-concurrency 30 --ramp 5,10,20,30 \
  --render-concurrency 4 --stop-before-end-seconds 2700 \
  --output-root outputs/batches
```

首次运行可加 `--prepare-only`，一次读取原始 7 个工作表并拆分逐项目只读快照；同名批次
目录只有显式 `--resume` 才能复用。续跑时使用 `--resume`，若还要重新入队上次失败项目则
同时加 `--retry-failed`。`--project-type`、`--fids` 和 `--project-keys` 可用于抽样或定向
重跑。每个请求、ReportIR、Writer、图表和文档阶段均写入同目录原子 checkpoint；指纹一致
且校验通过的阶段不会重复调用模型。批次状态为 `complete`、`complete_with_failures` 或
`paused`，只有选定项目全部校验成功时命令返回完整成功。

批次目录固定为 `outputs/batches/<batch-id>/`，包含输入 SHA-256 清单、项目快照、逐项目
检查点与报告、`progress.json`、`summary.csv` 和脱敏失败清单。`batch.lock` 防止两个控制器
并行消费同一批次；默认在 Slurm Job 结束前 45 分钟停止领取新项目。API key 只能通过
`--api-key-env` 指定的环境变量继承，不能放入命令参数、状态文件或日志。

后续取得正式项目名称后，可提供 UTF-8 JSON 映射并在原批次续跑：

```bash
PYTHONPATH=src python scripts/build_mvp_batch.py \
  --input private_inputs/source/raw_multi_project.xlsx --batch-id <batch-id> --year 2024 \
  --resume --project-name-map private_inputs/project_names.json \
  --writer-base-url ... --writer-model qwen
```

映射键使用 `project_type + FID` 形成的唯一键（如 `solar-0026`）。名称变化不会失效
Analysis Builder 或图表，只会重新生成 Writer、Markdown、DOCX、PDF 和 manifest。
模型响应在写入请求级 checkpoint 前会经过对应的结构与事实边界校验；旧 checkpoint
在复用前同样复验。阶段内 API 重试次数占总尝试次数超过 10% 时，控制器暂停放量。

确定性链路（不含 LLM，用于旧格式对比或回归基线）：

```bash
PYTHONPATH=src python -m eco_report_agent.cli render-all \
  --input private_inputs/source/input.xlsx --project coal --fid 33 --year 2025 \
  --project-name 胜利矿 --output-dir outputs/report-<id>
```

`eco-report catalog-sources` 将 Markdown、DOCX、PDF 和 XLSX 参考附件登记为带角色的
来源，生成不含正文的 `manifest.json` 与仅保存在私有输出目录的 `knowledge.jsonl`。
所有附件的 `instruction_authority` 固定为 `reference_only`：附件内容不能覆盖用户
请求，也不能被当作系统指令；旧案例只能提供结构、风格和回归参考，不能提供项目事实、
数值阈值或直接复制结论。PDF 附件通过 Ghostscript `txtwrite` 在 Slurm Job 内提取
文本，登录节点或云主机无需安装额外 Python 包。

`eco-report serve` 提供 `/health` 以及受 `X-Report-Service-Token` 保护的
`/v1/capabilities`、`/v1/report-ir`、`/v1/report-package`、`/v1/report-markdown`、
`/v1/report-docx`、`/v1/report-pdf`。`/v1/report-pdf` 默认关闭，只有渲染节点安装
XeLaTeX 且设置 `ECO_REPORT_ENABLE_PDF_RENDERER=1` 时才启用，否则返回明确的 HTTP 503。
令牌不放入 URL、日志或工作流输出。

## 部署边界

- Dify 目标版本固定为 `1.16.1`，DSL 固定为 `0.7.0`。`dify/eco_report_workflow.template.yml`
  是仓库中的工作流模板，`scripts/build_dify_dsl.py` 据此生成可导入的
  `dify/eco_report_workflow.yml`；`scripts/deploy_dify_dsl.py` 默认只执行脱敏
  dry-run，只有显式传入 `--apply` 并通过环境变量提供短期 Console 令牌才会调用导入
  接口，令牌不写入参数或日志。
- 云主机（`deploy/cloud/`）只运行合成数据 smoke 服务，复用主机已缓存、按摘要锁定的
  `python:3.12-slim`，不挂载任何真实项目数据；正式报告始终由超算 Slurm Job 生成。
  运行时令牌保存在仓库之外，权限设为 `0600`。
- `deploy/Dockerfile` 用于云主机镜像，`deploy/eco-report-agent.def` 用于超算通过
  Apptainer 预先构建同一轻量服务核心。
- `scripts/submit_report_job.py` 默认 dry-run，只有显式 `--apply` 才调用 `sbatch`，
  每个请求使用独立且不可覆盖的 `outputs/jobs/<request_id>/` 目录。

## 设计边界

- 指标计算、趋势数值、规则判断和措施过滤必须由代码完成，LLM 只基于已校验 `ReportIR`
  派生的受控输入撰写，不直接读取原始工作簿。
- 原始数据、报告原文和运行输出不提交到仓库（见 `.gitignore`：
  `private_inputs/`、`outputs/`、`artifacts/`、`slurm-*.out`）。
- LLM 服务地址、模型名和密钥环境变量名通过命令行参数传入，密钥本身只从环境变量读取。

## 测试

```bash
PYTHONPATH=src pytest tests/
```

所有 Python、测试、绘图、报告生成和 LLM 调用必须在已有的 Slurm Job 中执行，
登录节点仅用于 SSH、Slurm、Git 和轻量文本检查。当前 Python 环境自带 pytest，
不含 Matplotlib，需在 Job 本地临时目录通过 `uv pip install --target` 安装。
