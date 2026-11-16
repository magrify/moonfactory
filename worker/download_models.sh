#!/usr/bin/env bash
# Download every model the Sinclip workflows reference into a ComfyUI
# models folder. Runs at image BUILD time (see Dockerfile), never per job.
#
# Usage: ./download_models.sh /comfyui/models
#
# Uses the Hugging Face CLI so interrupted 20 GB downloads resume instead
# of restarting. Source repo for all Wan 2.2 files: the Comfy-Org
# repackaged Wan 2.2 repo on Hugging Face.
#
# UNVERIFIED: exact in-repo file paths below follow the guide's filenames.
# Confirm each path against the live repo listing at build time. The 5B
# filename in particular (fp16 vs bf16 vs fp8 variant) is unconfirmed.

set -euo pipefail

MODELS_DIR="${1:?usage: download_models.sh <comfyui models dir>}"
REPO="Comfy-Org/Wan_2.2_ComfyUI_Repackaged"

mkdir -p "$MODELS_DIR/diffusion_models" \
         "$MODELS_DIR/text_encoders" \
         "$MODELS_DIR/vae" \
         "$MODELS_DIR/clip_vision" \
         "$MODELS_DIR/loras"

# Draft model: Wan 2.2 TI2V-5B (free tier clips + previews)
hf download "$REPO" \
  --include "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" \
  --local-dir "$MODELS_DIR/_stage"
# Note: repackaged repos mirror the models/ folder layout, so staged files
# land in $MODELS_DIR/_stage/diffusion_models/... and are moved into place.
mv "$MODELS_DIR/_stage/diffusion_models/"* "$MODELS_DIR/diffusion_models/" 2>/dev/null || true

# Shared text encoder (all Wan 2.x workflows)
hf download "$REPO" \
  --include "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
  --local-dir "$MODELS_DIR/_stage"
mv "$MODELS_DIR/_stage/text_encoders/"* "$MODELS_DIR/text_encoders/" 2>/dev/null || true

# VAEs: 2.2 VAE for the 5B, 2.1 VAE for the A14B pair
hf download "$REPO" \
  --include "vae/wan2.2_vae.safetensors" "vae/wan_2.1_vae.safetensors" \
  --local-dir "$MODELS_DIR/_stage"
mv "$MODELS_DIR/_stage/vae/"* "$MODELS_DIR/vae/" 2>/dev/null || true

# Production model: Wan 2.2 I2V A14B, high noise + low noise experts
hf download "$REPO" \
  --include "diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors" \
            "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors" \
  --local-dir "$MODELS_DIR/_stage"
mv "$MODELS_DIR/_stage/diffusion_models/"* "$MODELS_DIR/diffusion_models/" 2>/dev/null || true

# CLIP Vision for A14B image conditioning
# UNVERIFIED: clip_vision_h.safetensors may live in a different repo
# (Comfy-Org clip vision repack). Confirm source at build time.
hf download "$REPO" \
  --include "clip_vision/clip_vision_h.safetensors" \
  --local-dir "$MODELS_DIR/_stage"
mv "$MODELS_DIR/_stage/clip_vision/"* "$MODELS_DIR/clip_vision/" 2>/dev/null || true

rm -rf "$MODELS_DIR/_stage"

# TODO(audio): music (ACE-Step class) and TTS (Kokoro / F5-TTS class)
# model files join this list once the audio stage is wired. They are
# deliberately absent until their repos and filenames are verified.

echo "Models in place under $MODELS_DIR:"
find "$MODELS_DIR" -name "*.safetensors" -exec ls -lh {} \;
