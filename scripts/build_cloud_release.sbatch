#!/bin/bash
#SBATCH --partition=debug
#SBATCH --job-name=eco_cloud_release
#SBATCH --time=00:10:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --output=/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent/slurm-%j.out

set -euo pipefail

module load python/3.11.7-gcc-12.3.0
cd /lustre/home/acct-wendongwei/yejinquan/it_stu100_home/dry/eco_report_agent

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src

python scripts/build_cloud_release.py
