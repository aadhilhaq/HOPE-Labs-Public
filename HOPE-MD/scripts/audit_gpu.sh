#!/bin/bash
# Run inside an interactive GPU job; Grace insists on --mem.
srun --partition=gpu --gres=gpu:a100:1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=00:10:00 --pty bash -l
nvidia-smi -L; nvidia-smi --query-gpu=driver_version --format=csv,noheader
module load WebProxy
export SINGULARITY_CACHEDIR=$TMPDIR/.singularity
singularity pull "$SCRATCH/hopemd-test.sif" docker://nvcr.io/hpc/gromacs:2024.2 && \
singularity exec --nv "$SCRATCH/hopemd-test.sif" gmx --version | grep -iE 'gpu support|cuda'
module load Anaconda3 2>/dev/null && python -c "import openmm as m,openmm.version as v;print('openmm',v.version,[m.Platform.getPlatform(i).getName() for i in range(m.Platform.getNumPlatforms())])" 2>&1 | tail -1
exit
