# Sinclip worker image (SKETCH, not built or tested).
#
# Strategy: start from a maintained ComfyUI serverless base rather than
# assembling CUDA + PyTorch + ComfyUI by hand. RunPod's own worker-comfyui
# project (github.com/runpod-workers/worker-comfyui) is the reference
# layout: ComfyUI serving on 8188 inside the container, a handler talking
# to it over localhost. That is exactly the shape handler.py assumes.
#
# UNVERIFIED: the exact base image name and tag below. Confirm the current
# published tag at build time and pin it. Do not float on "latest".

FROM runpod/worker-comfyui:UNVERIFIED_TAG

# --- Custom nodes -------------------------------------------------------
# VideoHelperSuite is required by every workflow template (VHS_VideoCombine).
# KJNodes and GGUF come along for shared workflow compatibility and
# quantized model options. Manager snapshot discipline from the guide
# applies: pin commits at build time, do not auto update in production.
#
# UNVERIFIED: install paths assume the base image keeps ComfyUI at /comfyui.
RUN cd /comfyui/custom_nodes && \
    git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes && \
    git clone --depth 1 https://github.com/city96/ComfyUI-GGUF

# --- Models -------------------------------------------------------------
# Models are baked into the image for serverless (cold start pulls of
# 40+ GB per worker are not viable). download_models.sh holds the exact
# file list. It runs at BUILD time, never at request time.
COPY download_models.sh /download_models.sh
RUN bash /download_models.sh /comfyui/models

# --- Workflows + handler ------------------------------------------------
# Build context note: build from a staging folder that contains handler.py,
# requirements.txt, download_models.sh and a workflows/ folder copied from
# ../workflows. Paths below assume that staging layout.
COPY workflows/ /workflows/
COPY handler.py /handler.py
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# The base image's start script is expected to launch ComfyUI and then
# the handler. UNVERIFIED: how this base image chains its entrypoint.
# If it only starts one process, add a start.sh that backgrounds
# `python /comfyui/main.py --listen 127.0.0.1 --port 8188` and then
# runs `python /handler.py`, and point CMD at it.
CMD ["python", "-u", "/handler.py"]
