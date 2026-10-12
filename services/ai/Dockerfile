FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium, and only Chromium: a Mermaid diagram is laid out in a browser
# because laying out a graph is the problem Mermaid already solved. Nothing
# else in this service needs one - composition and export are native - so the
# blast radius of a browser failure is one figure, not a deck.
RUN playwright install --with-deps chromium

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .

RUN mkdir -p /app/uploads /app/templates /app/output /app/previews

# The service does not run as root.
#
# It used to, and that was the assumption the sandbox could not be built on:
# `edit_deck_with_code` runs agent-written Python in a child of this process,
# and every containment the runner applies - dropped privileges, resource
# limits, a scrubbed environment - is weaker when the process it starts from
# is uid 0. The AST gate stands whatever the uid is; this is what makes the
# layers underneath it worth having.
#
# Chromium is installed above as root because Playwright puts its browsers in
# the invoking user's home, so the directory is moved somewhere the service
# user can actually read.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin thinkslides \
    && mv /root/.cache/ms-playwright /opt/ms-playwright \
    && chmod -R a+rX /opt/ms-playwright \
    && chown -R thinkslides:thinkslides /app
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
USER thinkslides

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]