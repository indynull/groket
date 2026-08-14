# Key overlay examples

Copy a map to `~/.groket/keys.toml` (or point `GROKET_KEYS` at the file).
The file is diffs only: omitted ids keep catalog defaults.

```bash
mkdir -p ~/.groket
cp examples/keys/colemak.toml ~/.groket/keys.toml
uv run groket keys --check
```

`colemak.toml` is a home-row nav map plus leader verbs, not a full
layout emulator. Space stays select. `g` stays HUD Turns to Timeline.
The recommended leader is `;`. Product default is no leader.

A bad overlay is refused in full (`groket keys --check` exits 1) and
the catalog defaults stay active. The TUI and HUD both apply a valid
map to footer, help, and key dispatch.
