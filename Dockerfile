# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# PLACEHOLDER service image — REPLACE this with your application's real build.
#
# It exists so that, on day one:
#   * `task build` and the OPT-IN CI build-image job (the library's
#     ci/build/docker-build.yml — see .gitlab-ci.yml + docs/CONSUMING.md) build
#     something real, and
#   * an upstream-image app can drop it cleanly with
#     `weisssrv-new-project prune image-build`.
#
# The placeholder serves a tiny static page on :8080 answering `/` with 200,
# which matches kubernetes/flux/deployment.yaml's containerPort + probe paths so
# a fresh deploy is at least reachable. Keep these properties when you swap in
# your app so it still satisfies the tenant namespace's Pod Security Admission
# baseline: runs NON-ROOT (UID 65532, matching the Deployment's securityContext)
# and needs NO writes to the root filesystem (readOnlyRootFilesystem: true).
#
# The base tag is a starting point — pin it to a digest for reproducible builds
# once you own this image.
# ---------------------------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

# A minimal landing page (written as root, before dropping privileges). Replace
# this and the CMD with your application's real build + entrypoint.
RUN printf '%s\n' \
    '<!doctype html>' \
    '<title>changeme-app</title>' \
    '<h1>changeme-app</h1>' \
    '<p>Placeholder image from weisssrv-app-template. Replace the Dockerfile with your app build.</p>' \
    > /app/index.html

# Drop to the same non-root UID the Deployment runs as.
USER 65532:65532

EXPOSE 8080

# Stdlib static server: no writes, no extra dependencies. Swap for your app's
# real entrypoint (a compiled binary, gunicorn, node, etc.).
CMD ["python", "-m", "http.server", "8080", "--directory", "/app"]
