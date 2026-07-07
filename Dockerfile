# syntax=docker/dockerfile:1
###############################################################################
# ReconstructKit — self-contained matching-decompilation pipeline image
#
# Bundles, per https://macabeus.medium.com/can-llms-really-do-matching-
# decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288 :
#   - reconkit CLI (macabeus/reconkit)            built dist + Decomp Atlas / Report UIs
#   - m2c (matt-kempster/m2c)                   programmatic asm->C  (vendor/.venv)
#   - decomp-permuter (simonlindholm)           brute-force matcher  (vendor/.venv)
#   - objdiff-wasm                              byte-exact verifier  (npm dep)
#   - @anthropic-ai/claude-agent-sdk           AI matching runner   (npm dep)
#   - compiler toolchains                       gcc/clang, arm-none-eabi binutils
#
# The image is fully self-contained: it clones and builds everything at build
# time and needs no files from the build context. Pass ANTHROPIC_API_KEY at
# run time to enable the AI phase; the programmatic (m2c/permuter) phase needs
# no key.
###############################################################################
FROM node:22-bookworm

# Pin upstream for reproducibility.
ARG RECONKIT_REF=676241f5a49b3763b08d0826c0922020f2a591bb
ARG M2C_REF=master
ARG PERMUTER_REF=main

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=production \
    PYTHONUNBUFFERED=1

# --- system toolchain -------------------------------------------------------
# build-essential/clang: generic compile targets + decomp-permuter's compiler.
# binutils-arm-none-eabi: GBA (agbcc) assembler used by the ARM fixture path.
# binutils-mips-linux-gnu: provides mips-linux-gnu-{nm,objdump} that
#   decomp-permuter needs to score N64/MIPS targets.
# python3-venv/pip: m2c + decomp-permuter virtualenvs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
        python3 python3-venv python3-pip \
        build-essential clang \
        binutils-arm-none-eabi \
        binutils-mips-linux-gnu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- reconkit source (pinned) ------------------------------------------------
RUN git clone https://github.com/macabeus/reconkit.git . \
    && git checkout "${RECONKIT_REF}"

# --- vendored python tools (https, not the SSH submodule URLs) --------------
RUN rm -rf vendor/m2c vendor/decomp-permuter \
    && git clone --depth 1 --branch "${M2C_REF}" \
         https://github.com/matt-kempster/m2c.git vendor/m2c \
    && git clone --depth 1 --branch "${PERMUTER_REF}" \
         https://github.com/simonlindholm/decomp-permuter.git vendor/decomp-permuter

# m2c venv (graphviz) and decomp-permuter venv (pycparser<3 toml Levenshtein),
# mirroring scripts/setup-m2c.sh and scripts/setup-decomp-permuter.sh.
RUN python3 -m venv vendor/m2c/.venv \
    && vendor/m2c/.venv/bin/pip install --quiet --no-cache-dir "graphviz~=0.20.1" \
    && python3 -m venv vendor/decomp-permuter/.venv \
    && vendor/decomp-permuter/.venv/bin/pip install --quiet --no-cache-dir \
         'pycparser<3' toml Levenshtein

# --- node deps + build ------------------------------------------------------
# Full install (incl. dev) is required to build TS + the two Vite UIs and to
# run the verification test suite. --include=dev overrides NODE_ENV=production's
# omission of devDependencies (rollup, vite, typescript, vitest). Kept in the
# final image so the pipeline is self-contained and `npm test` can re-verify.
RUN npm install --include=dev --no-audit --no-fund \
    && npm run build \
    && npm run build:ui

# --- build-time verification gate ------------------------------------------
# Exercises the real compiler -> objdiff -> m2c -> permuter plugins against the
# committed ARM(agbcc)/MIPS(KMC gcc) fixtures. A broken pipeline fails the build.
RUN npm test

# --- runtime ----------------------------------------------------------------
# Decomp projects are mounted at /work; configs reference paths relative to the
# config file, so cwd is left at /app where the vendored tools live.
RUN mkdir -p /work
VOLUME ["/work"]

LABEL org.opencontainers.image.title="reconkit" \
      org.opencontainers.image.description="Self-contained matching-decompilation pipeline (reconkit + m2c + decomp-permuter + objdiff + Claude Agent SDK)" \
      org.opencontainers.image.source="https://github.com/macabeus/reconkit"

ENTRYPOINT ["node", "/app/dist/cli.js"]
CMD ["--help"]
