"""Scaffold user extensions under ``~/.groket/`` (tasks)."""

from __future__ import annotations

from pathlib import Path

from ..paths import (
    ensure_user_extension_dirs,
    user_tasks_dir,
)


def write_tasks_file(path: Path | None = None, *, force: bool = False) -> Path:
    """Create an empty tasks YAML (for ``groket batch --tasks``)."""
    ensure_user_extension_dirs()
    out = Path(path).expanduser() if path else user_tasks_dir() / "example_tasks.yaml"
    if out.exists() and not force:
        raise FileExistsError(f"already exists: {out} (use --force to overwrite)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        """# yaml-language-server: $schema=https://indynull.github.io/groket/schemas/tasks.schema.json
# Run: groket batch validate <this-file>
#      groket batch run -t <this-file> -m <model-id>

schema_version: 1

tasks:
  - task_id: example-hello
    category: regular
    # repo_url: https://github.com/org/repo.git
    # repo_branch: main
    # Or live host tree (bind-mounted as /workspace; single model only):
    # repo_path: ~/src/my-project
    prompt: >
      Say hello from the workspace and list the top-level files.
    # initial_commands: |
    #   echo ready
""",
        encoding="utf-8",
    )
    return out
