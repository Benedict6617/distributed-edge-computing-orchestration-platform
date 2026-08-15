# Zero third-party runtime dependencies: the control plane runs on the
# Python standard library alone, so the `pip install` below only installs
# this package itself (registers the `edge-orchestrator` console script)
# -- it never reaches out to PyPI for anything else. See README.md
# "Design Principles" for the rationale.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="edge-orchestrator" \
      org.opencontainers.image.description="Distributed edge computing orchestration platform" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY src/ /app/src/
COPY data/ /app/data/
COPY pyproject.toml README.md /app/

RUN pip install --no-cache-dir --no-build-isolation -e . \
    && useradd --create-home --uid 1000 orchestrator \
    && mkdir -p /data && chown orchestrator:orchestrator /data

USER orchestrator
ENV EDGE_ORCH_DB_PATH=/data/edge_orchestrator.db
ENV EDGE_ORCH_HOST=0.0.0.0
ENV EDGE_ORCH_PORT=8080

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health', timeout=2).status==200 else 1)"

ENTRYPOINT ["edge-orchestrator"]
CMD ["serve"]
