#!/bin/bash
#SBATCH --partition=debug
#SBATCH --job-name=eco_report_image
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --output=/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent/slurm-%j.out

set -euo pipefail

cd /lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent
mkdir -p artifacts outputs

apptainer build --force artifacts/eco-report-agent-0.1.0.sif deploy/eco-report-agent.def
apptainer run artifacts/eco-report-agent-0.1.0.sif build \
  --input private_inputs/source/4.1_修复策略匹配结果_FINAL\(1\)\(1\).xlsx \
  --project coal \
  --fid 33 \
  --year 2025 \
  --output outputs/container-fid33-2025-report-ir.json
