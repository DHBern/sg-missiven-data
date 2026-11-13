#!/bin/bash
# Read the St. Gallen Missiven page images with a fine-tuned VLM on one H100.
#   sbatch --export=ALL,MODEL=olmocr,SHARD=0,SHARDS=4 ubelix/recognize.sbatch
#   sbatch --array=0-3 --export=ALL,MODEL=olmocr,SHARDS=4 ubelix/recognize.sbatch
# Resumable: a job that is preempted or runs out of time is started again with
# the same MODEL/SHARD and skips every page already in its output file.
#SBATCH --job-name=sgm-read
#SBATCH --account=gratis
#SBATCH --partition=gpu
#SBATCH --qos=job_gratis
#SBATCH --gres=gpu:h100:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/network/users/%u/sgm/logs/%x-%A_%a.out
set -euo pipefail
S=/scratch/network/users/$USER
R=/storage/research/wbkolleg_dh_1
CODE=${SGM_CODE:-$S/sgm/code}
SIF=${SIF:-$S/apptainer/vllm-0.29.0.sif}
SHARD=${SHARD:-${SLURM_ARRAY_TASK_ID:-0}}
SHARDS=${SHARDS:-1}
: "${MODEL:?MODEL=olmocr or MODEL=qwen-page}"

case "$MODEL" in
  olmocr)
    ID=olmocr2-7b-german-kurrent-xvi-xix-blb-page-px4m-native-v1
    BASE=allenai/olmOCR-2-7B-1025
    ADAPTER=$R/Textrecognition_Training/training_folder/trained/$ID
    IMAGES=${IMAGES:-$R/Data_SG-Missiven/12_in-process}    # 3000 px originals: its training resolution
    EXTRA=(--max-pixels 4194304 --strip-front-matter --max-num-seqs 32)
    PROMPT=$CODE/ubelix/prompts/olmocr2.txt ;;
  qwen-blb)
    ID=qwen3.5-4b-german-kurrent-xvi-xix-blb-page-v1
    BASE=Qwen/Qwen3.5-4B
    ADAPTER=$R/Textrecognition_Training/training_folder/trained/$ID
    IMAGES=${IMAGES:-$R/tei/datasets/sg-missiven-images/pages}
    EXTRA=(--max-pixels 2097152 --max-num-seqs 64)
    PROMPT=$CODE/ubelix/prompts/transcribe.txt ;;
  *) echo "unknown MODEL=$MODEL" >&2; exit 2 ;;
esac
OUT=$S/sgm/readings/$ID/shard-$SHARD-of-$SHARDS.jsonl
echo "== $ID shard $SHARD/$SHARDS on $(hostname) $(date -Is); images $IMAGES"
apptainer exec --nv \
  --bind /storage/research:/storage/research --bind /scratch:/scratch --bind /rs_scratch:/rs_scratch \
  --env HF_HOME=$R/Textrecognition_Training/hf_hub --env HF_HUB_OFFLINE=1 \
  --env VLLM_NO_USAGE_STATS=1 --env DO_NOT_TRACK=1 \
  "$SIF" python3 "$CODE/ubelix/recognize.py" \
    --images "$IMAGES" --out "$OUT" --base "$BASE" --adapter "$ADAPTER" \
    --model-id "$ID" --prompt-file "$PROMPT" --shard "$SHARD" --shards "$SHARDS" \
    "${EXTRA[@]}" ${LIMIT:+--limit $LIMIT}
echo "== done $(date -Is)"
