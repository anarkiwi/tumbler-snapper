# Multistage: resolve dependencies in an early layer so they cache when only
# source changes, then editable-install the full source in the final stage.
FROM python:3.12-slim AS deps
WORKDIR /app
# Metadata + minimal package stub: enough for the build backend to resolve and
# install all dependencies without the full source tree.
COPY pyproject.toml README.md ./
COPY src/tsnap/__init__.py src/tsnap/__init__.py
RUN pip install --no-cache-dir ".[dev,oracle]"

FROM deps AS final
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[dev,oracle]"
# Default: run the hermetic suite (no oracle/HVSC network or Docker deps).
CMD ["pytest", "-m", "not oracle and not hvsc", "-n", "auto"]
