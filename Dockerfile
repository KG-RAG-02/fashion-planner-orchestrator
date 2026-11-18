FROM python:3.12-slim

# Conventions section 2: uid/gid 10001, read-only root filesystem (compose), HOME=/tmp,
# writable state only on the /app/.local volume. Code is root-owned and read-only for the app user.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOME=/tmp \
    ORCHESTRATOR_HOST=0.0.0.0 \
    ORCHESTRATOR_PORT=8765 \
    ORCHESTRATOR_ALLOW_REMOTE_BIND=1 \
    ORCHESTRATOR_RUNTIME=container \
    ORCHESTRATOR_WORKFLOW_STORE_DIR=/app/.local/workflow

WORKDIR /app

RUN groupadd --system --gid 10001 vogue \
 && useradd --system --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin vogue

# Korean glyphs for the 기획서 PNG (src/fashion_planner_orchestrator/planning_sheet.py): NanumBarunGothic from
# fonts-nanum (SIL OFL 1.1). Latin display text uses the self-hosted Jost in web/fonts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-nanum \
 && rm -rf /var/lib/apt/lists/*

# Dependencies only (the package runs from /app/src via PYTHONPATH): the query, workflow and LLM extras.
COPY pyproject.toml ./pyproject.toml
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; extras = p['optional-dependencies']; print('\n'.join(p.get('dependencies', []) + [r for name in ('query', 'workflow', 'llm-openai', 'llm-anthropic') for r in extras[name]]))" > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY app.py ./app.py
COPY src/fashion_planner_orchestrator ./src/fashion_planner_orchestrator
COPY web ./web
COPY configs ./configs
COPY prompts ./prompts
COPY contracts ./contracts
# Offline query structuring (ORCHESTRATOR_QUERY_STRUCTURING_MODE=synthetic_fixture) reads these fixtures.
COPY examples/fixtures/query ./examples/fixtures/query

# Some contract files are 0600 in the checkout (atomic writers use mkstemp): make the code world-readable,
# then pre-create the private store so a fresh /app/.local volume is owned by the app user.
RUN chmod -R u=rwX,go=rX /app \
 && mkdir -p /app/.local/workflow/blobs \
 && chown -R 10001:10001 /app/.local \
 && chmod -R 0700 /app/.local

USER 10001:10001

EXPOSE 8765

# Liveness only (no external calls); GET /api/health is never gated.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('ORCHESTRATOR_PORT', '8765'), timeout=2).read()"]

CMD ["python", "app.py"]
