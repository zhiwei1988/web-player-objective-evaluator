# syntax=docker/dockerfile:1.6
# Multi-stage portable evaluator image. The "builder" stage only COPIES
# pre-built host assets (third_party/install, .venv, .playwright, streams,
# reference). The "runtime" stage starts from ubuntu:24.04 and installs
# the Chromium runtime libraries Playwright needs. NO source compilation
# happens inside docker; build host scripts/setup.sh + scripts/build.sh +
# scripts/deploy.sh must have produced the inputs first.

FROM ubuntu:24.04 AS builder
WORKDIR /work
COPY third_party/install /work/third_party/install
COPY .venv               /work/.venv
COPY .playwright         /work/.playwright
COPY streams             /work/streams
COPY reference           /work/reference
COPY lib                 /work/lib
COPY rtsp_server         /work/rtsp_server
COPY scripts             /work/scripts
COPY runner.py analyzer.py scorer.py report.py requirements.txt /work/

FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/work/.playwright \
    TESSDATA_PREFIX=/work/third_party/install/share/tessdata
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        python3 \
        unzip lsof procps jq \
    && curl -fsSL https://dl-ssl.google.com/linux/linux_signing_key.pub \
         | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main' \
         > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
         google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY --from=builder /work/third_party/install /work/third_party/install
# Register source-built libraries with the dynamic linker cache so that
# ctypes.util.find_library() (used by pylibdmtx) can locate them. Setting
# LD_LIBRARY_PATH alone is insufficient because gcc/ld are not present in
# the runtime stage and find_library() falls back to ldconfig only.
RUN echo /work/third_party/install/lib > /etc/ld.so.conf.d/evaluator.conf && ldconfig
COPY --from=builder /work/.venv               /work/.venv
COPY --from=builder /work/.playwright         /work/.playwright
COPY --from=builder /work/streams             /work/streams
COPY --from=builder /work/reference           /work/reference
COPY --from=builder /work/lib                 /work/lib
COPY --from=builder /work/rtsp_server         /work/rtsp_server
# Allow non-root runtime users (via `docker run --user`) to drop mediamtx.pid
# and mediamtx.log inside /work/rtsp_server at startup.
RUN chmod a+w /work/rtsp_server
COPY --from=builder /work/scripts             /work/scripts
COPY --from=builder /work/runner.py /work/analyzer.py /work/scorer.py /work/report.py /work/requirements.txt /work/
ENTRYPOINT ["/work/scripts/evaluator.sh"]
