# 生态修复报告生成 Agent

本项目将生态修复报告生成拆成可审计的确定性核心和轻量 Dify 编排层。

部署分为两条互不调度的独立链路：云主机只运行 Dify 1.16.1 与合成数据 smoke 服务，
用于证明端到端报告生成能够实现；开发、完整测试和真实项目生产全部在上海交大超算的
Slurm Job 中完成。真实工作簿不会上传云主机，云主机也不会向超算提交任务。

当前阶段目标：

1. 从标准 XLSX 中读取项目年度指标；
2. 生成不带未经批准阈值判断的时序证据；
3. 将数据源已有的风险类型映射到指标证据；
4. 对修复措施进行能源类型冲突过滤和去重；
5. 输出稳定的 `ReportIR` JSON，供后续 LLM 撰写、事实校验和文档渲染使用。

## ReportIR 2.0 MVP 审核链

当前人工审核阶段只在超算运行，不扩展或部署 Dify。新链路为：

```text
XLSX → IndicatorFact → ProblemDiagnosis → PriorityAssessment
     → MeasureRecommendation → ReportIR 2.0-mvp → Writer → DOCX/PDF
```

配置文件集中在 `config/`：趋势阈值、问题规则、措施知识、ReportIR Schema 和报告样式
均不硬编码在 Writer 中。Writer 的唯一事实输入是通过 Schema 与业务规则双重校验的
ReportIR；模板后端是确定性保底，LLM 后端只能润色，校验失败时自动回退模板版本。

胜利矿审核包由 Slurm Job 调用：

```bash
PYTHONPATH=src python scripts/build_mvp_review.py \
  --input private_inputs/source/input.xlsx --project coal --fid 33 --year 2025 \
  --project-name 胜利矿 --output-dir outputs/jobs/<request-id>
```

输出包含报告三种格式、五类中间 JSON、自动校验、结构对比、审核清单和产物清单。
P1/P2 进入重点正文，P3 简述，P0/数据不足/不适用集中表格展示；P0 不生成措施。
图表通过 XeLaTeX/TikZ 和 Ghostscript 在计算节点生成 300 dpi PNG，并嵌入 DOCX/PDF。

## 参考资料接入边界

`eco-report catalog-sources` 将 Markdown、DOCX、PDF 和 XLSX 附件登记为带角色的来源，
生成不含正文的 `manifest.json` 与仅保存在私有输出目录的 `knowledge.jsonl`。所有附件的
`instruction_authority` 固定为 `reference_only`：附件内容不能覆盖用户请求，也不能被
当作系统指令。角色策略进一步限制用途：旧案例只能用于结构、风格和回归参考，不能提供
项目事实、数值阈值或直接复制结论；指标方法文档只能提供定义、公式和单位；结构化策略
工作簿才可作为项目观测和候选措施来源。

来源目录模块使用 Python 标准库解析 Markdown、DOCX 和 XLSX。PDF 在超算 Slurm Job 中
通过 Ghostscript `txtwrite` 提取文本，不需要在登录节点或云主机安装额外 Python 包。
调用形式如下，`--source` 可以重复：

```bash
PYTHONPATH=src python -m eco_report_agent.cli catalog-sources \
  --source prompt_spec=/private/path/spec.md \
  --source indicator_method=/private/path/method.docx \
  --source strategy_mapping=/private/path/mapping.xlsx \
  --source legacy_example=/private/path/example.docx \
  --manifest outputs/source-catalog/manifest.json \
  --knowledge-pack outputs/source-catalog/knowledge.jsonl
```

## 设计边界

- 指标计算、趋势数值、规则判断和措施过滤必须由代码完成；
- LLM 只基于 `ReportIR` 撰写，不直接读取原始工作簿；
- 旧案例报告只作为 `legacy_example` 回归样例，不提供正式阈值；
- 原始数据、报告原文和运行输出不得提交；
- Dify 目标版本固定为 `1.16.1`，DSL 固定为 `0.7.0`。

`dify/eco_report_workflow.template.yml` 是代码仓库中的工作流模板，使用
`scripts/build_dify_dsl.py` 生成可导入的 `dify/eco_report_workflow.yml`。
`scripts/deploy_dify_dsl.py` 默认只执行脱敏 dry-run；只有显式传入 `--apply`
并通过环境变量提供短期 `DIFY_CONSOLE_ACCESS_TOKEN` 和
`DIFY_CONSOLE_CSRF_TOKEN` 时才会调用 Console 导入接口。Dify 1.16.1 会同时校验
`X-CSRF-Token` 请求头和 `csrf_token` Cookie，部署模块不会把任一令牌写入参数或日志。

`eco-report serve` 提供 `/health`，以及受令牌保护的 `/v1/capabilities`、
`/v1/report-ir`、`/v1/report-package`、`/v1/report-markdown`、
`/v1/report-docx` 和 `/v1/report-pdf`。服务只依赖
Python 标准库，适合在资源有限的云主机以单容器运行；原始工作簿通过只读卷挂载，
不会打入镜像。

`/v1/report-docx` 默认可用，响应为带 `Content-Disposition: attachment` 的二进制
OOXML 文件。`/v1/report-pdf` 默认关闭：只有渲染节点安装了 XeLaTeX 且设置
`ECO_REPORT_ENABLE_PDF_RENDERER=1` 时才启用，否则返回明确的 HTTP 503，避免 Dify
云主机意外承担排版负载。所有受保护接口只接受 `X-Report-Service-Token` 请求头，
令牌不放入 URL、日志或工作流输出。

`eco-report render-markdown` 可直接生成固定目录报告，`validate-markdown` 对禁用章节、
未支持风险、被拒措施、夸张用语、无依据因果句式和虚构实施参数执行生成后校验。

`eco-report render-all` 是超算生产入口：在一个 Slurm Job 内只构建一次 ReportIR，随后
生成并校验 Markdown、DOCX、PDF，最后写入不含报告正文的 SHA-256 产物清单。
`scripts/submit_report_job.py` 默认仅 dry-run，只有显式 `--apply` 才调用 `sbatch`，每个
请求使用独立且不可覆盖的 `outputs/jobs/<request_id>/` 目录。

`eco-report render-docx` 使用 Python 标准库直接生成确定性 OOXML 文档，采用
`narrative_proposal` 排版参数、真实 Word 标题/列表样式、固定 DXA 表格几何以及页眉页脚，
无需在资源有限的云主机安装 `python-docx`。`eco-report render-pdf` 是可选的独立渲染模块，
通过 XeLaTeX 和服务器本地中文字体生成 PDF；建议与在线轻量 API 分开部署，以避免让
Dify 所在云主机承担排版计算。

示例：

```bash
PYTHONPATH=src python -m eco_report_agent.cli render-docx \
  --input private_inputs/source/input.xlsx --project coal --fid 33 --year 2025 \
  --project-name 胜利矿 --output outputs/report.docx

PYTHONPATH=src python -m eco_report_agent.cli render-pdf \
  --input private_inputs/source/input.xlsx --project coal --fid 33 --year 2025 \
  --project-name 胜利矿 --output outputs/report.pdf
```

DOCX 在生成前复用 Markdown 事实校验，生成后执行 OOXML 包、样式、真实编号、页面参数和
表格几何审计。PDF 在生成后校验文件头、EOF 标记和最小有效体积，并可通过 Ghostscript
渲染为逐页 PNG 进行视觉检查。

`deploy/Dockerfile` 用于最终云主机镜像，`deploy/eco-report-agent.def` 用于在超算
通过 Apptainer 预先构建和执行同一轻量服务核心。

## 计划中的命令

```bash
PYTHONPATH=src python -m eco_report_agent.cli build \
  --input private_inputs/source/4.1_修复策略匹配结果_FINAL\(1\)\(1\).xlsx \
  --project coal --fid 33 --year 2025
```

所有 Python 与测试命令只允许在已有的 Slurm 计算节点中执行。
