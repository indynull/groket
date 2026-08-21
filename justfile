# Local development recipes (uv + cargo).

# List recipes.
default:
    @just --list

# Project venv for lint/test. Product install: uv tool install --editable .
install:
    uv sync --group test --group dev

# ruff + format check + mypy + fluent + typing policy.
lint:
    uv run ruff check --select I groket tests
    uv run ruff check groket tests
    uv run ruff format --check groket tests
    uv run mypy groket
    uv run python scripts/check_fluent.py
    uv run python scripts/check_typing_policy.py

# Autofix ruff + format + mypy.
lint-fix:
    uv run ruff check --select I --fix groket tests
    uv run ruff check --fix groket tests
    uv run ruff format groket tests
    uv run mypy groket

# Size-limit report only (AGENTS §4.6). Not part of just lint / CI.
lint-complexity:
    uv run ruff check --preview \
        --select PLR0911,PLR0912,PLR0913,PLR0915,PLR0904 \
        groket

# Regenerate schemas/*.schema.json from Pydantic.
schema:
    uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; emit_tasks_schema(Path('schemas/tasks.schema.json'))"
    uv run python -c "from pathlib import Path; from groket.config import emit_config_schema; emit_config_schema(Path('schemas/config.schema.json'))"
    uv run python -c "from pathlib import Path; from groket.integrations.control_contract import emit_control_schema, emit_control_doc; emit_control_schema(Path('schemas/control.schema.json')); emit_control_doc(Path('docs/control.md'))"

# Fail if committed schemas are out of date.
schema-check:
    #!/usr/bin/env bash
    set -euo pipefail
    check() {
      local emit="$1" dest="$2"
      local tmp
      tmp=$(mktemp)
      uv run python -c "$emit" "$tmp"
      if ! diff -q "$tmp" "$dest" >/dev/null; then
        echo "$dest is stale — run just schema and commit" >&2
        rm -f "$tmp"
        exit 1
      fi
      rm -f "$tmp"
    }
    check 'from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; import sys; emit_tasks_schema(Path(sys.argv[1]))' schemas/tasks.schema.json
    check 'from pathlib import Path; from groket.config import emit_config_schema; import sys; emit_config_schema(Path(sys.argv[1]))' schemas/config.schema.json
    check 'from pathlib import Path; from groket.integrations.control_contract import emit_control_schema; import sys; emit_control_schema(Path(sys.argv[1]))' schemas/control.schema.json
    check 'from pathlib import Path; from groket.integrations.control_contract import emit_control_doc; import sys; emit_control_doc(Path(sys.argv[1]))' docs/control.md

# Validate examples/ packs.
examples-check:
    uv run python scripts/check_examples.py

# Regenerate desktop/assets/textual-themes.json.
hud-themes:
    uv run python scripts/gen_textual_themes.py

# Fail if the HUD Textual theme map is stale.
hud-themes-check:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp=$(mktemp)
    uv run python scripts/gen_textual_themes.py "$tmp"
    if ! diff -q "$tmp" desktop/assets/textual-themes.json >/dev/null; then
      echo "desktop/assets/textual-themes.json is stale — run just hud-themes and commit" >&2
      rm -f "$tmp"
      exit 1
    fi
    rm -f "$tmp"

# Theme map + rustfmt + clippy + HUD cargo test (+ cov if installed).
hud-check: hud-themes-check
    cargo fmt --check --manifest-path desktop/Cargo.toml
    CARGO_INCREMENTAL=0 cargo clippy --manifest-path desktop/Cargo.toml --all-targets -- -D warnings
    CARGO_INCREMENTAL=0 cargo test --manifest-path desktop/Cargo.toml
    just hud-cov

# cargo llvm-cov: lcov for Codecov, then fail-under on non-paint HUD logic.
hud-cov:
    #!/usr/bin/env bash
    set -euo pipefail
    if cargo llvm-cov --version >/dev/null 2>&1; then
      cleanup() { rm -rf target/llvm-cov-target target/llvm-cov; }
      trap cleanup EXIT
      CARGO_INCREMENTAL=0 cargo llvm-cov --manifest-path desktop/Cargo.toml --lib --no-report
      CARGO_INCREMENTAL=0 cargo llvm-cov report --manifest-path desktop/Cargo.toml \
        --lcov --output-path desktop/lcov.info
      CARGO_INCREMENTAL=0 cargo llvm-cov report --manifest-path desktop/Cargo.toml \
        --fail-under-lines 70 \
        --ignore-filename-regex 'src/(app|view|typo|main|x11focus|control)\.rs'
    else
      echo "hud-cov: cargo-llvm-cov not installed; skip fail-under (fmt/clippy/test already ran)"
    fi

# Rebuild brand/ from the geometric mark.
brand:
    uv run --group brand python brand/build.py

# cargo test the scan crate (walk + updates filter; no Python link).
scan-check:
    cargo test --manifest-path scan/Cargo.toml

# pytest (no coverage flag).
test:
    uv run pytest tests/ -q --tb=short

# pytest with coverage report (`fail_under` applies).
test-cov:
    uv run pytest tests/ --cov=groket --cov-report=term-missing --cov-report=html

# Set the product version in pyproject, __init__, and both crates.
bump version:
    uv run python scripts/bump_version.py {{version}}

# This-platform wheel into dist/ (needs Rust).
wheel:
    uv build --wheel

# cibuildwheel for this host (Linux needs Docker).
wheels:
    uvx cibuildwheel

# Source distribution into dist/.
sdist:
    uv build --sdist

# lint + schema-check + hud-check + examples-check + test.
ci: lint schema-check hud-check examples-check test

# Python caches plus Cargo workspace target.
clean:
    rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ \
        htmlcov/ .coverage coverage.json desktop/lcov.info docs/_build/
    find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
    cargo clean || true
