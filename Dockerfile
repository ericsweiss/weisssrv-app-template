# syntax=docker/dockerfile:1
# PLACEHOLDER service image — replace with your application's real build, or run
# `weisssrv-new-project prune image-build` if your app uses an upstream image.
# Two invariants a replacement must keep, because deployment.yaml depends on
# them: run NON-ROOT as UID 65532, and need no writes to the root filesystem
# (`readOnlyRootFilesystem: true`). See docs/CONSUMING.md.
FROM python:3.11-slim

WORKDIR /app

RUN printf '%s\n' \
    '<!doctype html>' \
    '<title>changeme-app</title>' \
    '<h1>changeme-app</h1>' \
    '<p>Placeholder image from weisssrv-app-template. Replace the Dockerfile with your app build.</p>' \
    > /app/index.html

USER 65532:65532

# :8080 answering / with 200 — deployment.yaml's containerPort and probe paths.
EXPOSE 8080

CMD ["python", "-m", "http.server", "8080", "--directory", "/app"]
