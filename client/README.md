# mamemai-client

The Mamemai client — the half of [Mamemai](../README.md) that runs on your everyday machine
(Windows, macOS, or Linux). It captures microphone audio, detects when you're speaking,
streams it to the server, and plays back the synthesised reply. It is deliberately thin
and stateless: no GPU, no database, no model downloads.

In a split-host deployment it automatically opens an SSH tunnel to the server before
connecting — nothing but SSH is ever exposed on the network.

## Install

Follow the [installation guide](../docs/INSTALLATION.md). In short:

```bash
uv tool install "git+<repo-url>#subdirectory=client"
# create mamemai.toml in your platform config dir (see config/mamemai.example.toml)
mamemai-client
```

For development, from this directory:

```bash
uv sync
uv run mamemai-client
```

## Configuration

One file: `mamemai.toml` in your platform config directory
(`~/.config/mamemai/` on Linux/macOS, `%LOCALAPPDATA%\mamemai\` on Windows) — see
[config/mamemai.example.toml](config/mamemai.example.toml). `ssh_host` set means
split-host (tunnel to that machine); `ssh_host` omitted means the server is local.

See the [root README](../README.md) for what Mamemai is, and
[docs/INSTALLATION.md](../docs/INSTALLATION.md) for the full picture.
