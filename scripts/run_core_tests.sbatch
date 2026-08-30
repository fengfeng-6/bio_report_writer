#!/bin/bash
#SBATCH --partition=debug
#SBATCH --job-name=eco_report_test
#SBATCH --time=00:20:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --output=/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent/slurm-%j.out

set -euo pipefail

module load python/3.11.7-gcc-12.3.0
cd /lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src
export ECO_REPORT_SOURCE_XLSX=/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent/private_inputs/source/4.1_修复策略匹配结果_FINAL\(1\)\(1\).xlsx

python --version
python -m unittest discover -v
python -m eco_report_agent.cli build \
  --input "$ECO_REPORT_SOURCE_XLSX" \
  --project coal \
  --fid 33 \
  --year 2025 \
  --output outputs/fid33-2025-report-ir.json
python -m eco_report_agent.cli render-markdown \
  --input "$ECO_REPORT_SOURCE_XLSX" \
  --project coal \
  --fid 33 \
  --year 2025 \
  --project-name 胜利矿 \
  --output outputs/fid33-2025-report.md
python -m eco_report_agent.cli validate-markdown \
  --input "$ECO_REPORT_SOURCE_XLSX" \
  --project coal \
  --fid 33 \
  --year 2025 \
  --project-name 胜利矿 \
  --report outputs/fid33-2025-report.md
python -m eco_report_agent.cli render-docx \
  --input "$ECO_REPORT_SOURCE_XLSX" \
  --project coal \
  --fid 33 \
  --year 2025 \
  --project-name 胜利矿 \
  --output outputs/fid33-2025-report.docx
python -m eco_report_agent.cli render-pdf \
  --input "$ECO_REPORT_SOURCE_XLSX" \
  --project coal \
  --fid 33 \
  --year 2025 \
  --project-name 胜利矿 \
  --output outputs/fid33-2025-report.pdf
mkdir -p outputs/qa/fid33-2025-pdf
gs -dSAFER -dBATCH -dNOPAUSE -sDEVICE=png16m -r144 \
  -sOutputFile=outputs/qa/fid33-2025-pdf/page-%03d.png \
  outputs/fid33-2025-report.pdf
python scripts/build_dify_dsl.py
python scripts/deploy_dify_dsl.py --base-url http://10.119.13.38
