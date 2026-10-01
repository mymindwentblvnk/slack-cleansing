# slack-delete

Delete all of your own messages in a single Slack channel, using browser session
tokens extracted from a logged-in Slack web session.

> **Warning**: This uses the unofficial `xoxc` token + `d` cookie flow. It is
> against Slack's Terms of Service. Use at your own risk on workspaces where
> you're allowed to do so.

## Requirements

- **Docker**
- **GNU Make** (ships with macOS and most Linux distros)

## Setup

```sh
cp .env.example .env            # then edit .env, fill in your values
make build
```

All subsequent commands go through `make`, which wraps `docker run` with the
right flags (env file, `$PWD` bind-mounted into the container, running as your
host user so written files are owned by you). Run `make help` for the full
list.

## Configuration via `.env`

The tool reads `.env` from the current directory. Supported keys:

| Key              | Required | Purpose                                             |
|------------------|----------|-----------------------------------------------------|
| `SLACK_WORKSPACE`| yes      | Workspace subdomain, e.g. `acme-corp`               |
| `SLACK_TOKEN`    | yes      | Browser `xoxc-...` token                            |
| `SLACK_COOKIE_D` | yes      | Value of the `d` cookie (`xoxd-...`)                |
| `SLACK_CHANNEL`  | no       | Default `CHANNEL=` for `make scan`                  |
| `SLACK_SINCE`    | no       | Default `SINCE=` for `make scan`                    |

Make variables (`CHANNEL=`, `SINCE=`, etc.) override the matching env var. See
`.env.example`.

## Get your Slack credentials

Log in to your workspace in Chrome/Firefox (e.g. `https://acme-corp.slack.com`
or `https://app.slack.com`), then open DevTools (`Cmd+Opt+I` / `F12`).

### 1. Token + workspace (Console)

Paste in the **Console** tab — it copies ready-to-run `export` lines to your
clipboard:

```js
copy(Object.values(JSON.parse(localStorage.localConfig_v2).teams)
  .map(t => `export SLACK_TOKEN='${t.token}'\nexport SLACK_WORKSPACE='${t.domain}'`)
  .join('\n\n'));
console.log('copied to clipboard');
```

Paste into your terminal (or into `.env` without the `export ` prefix). If
you're in multiple workspaces, keep only the block for the one you want.

### 2. Cookie `d` (manual)

The `d` cookie is `HttpOnly`, so JavaScript can't read it — you have to copy it
by hand:

DevTools → **Application** → **Storage** → **Cookies** →
`https://<workspace>.slack.com` (or `https://app.slack.com`) → click the row
named `d` → copy the **Value** (starts with `xoxd-`).

Put it into `.env`:

```
SLACK_COOKIE_D=xoxd-...paste...
```

Tokens rotate — if you get `invalid_auth` later, redo both steps from a fresh
Slack session.

## Quick start

Typical end-to-end workflow:

```sh
# 1. One-time setup
cp .env.example .env             # fill in SLACK_WORKSPACE, SLACK_TOKEN,
                                 # SLACK_COOKIE_D, SLACK_CHANNEL
make build

# 2. Collect your message IDs into my-channel.jsonl
make scan CHANNEL=my-channel

# 3. Preview what would be deleted (optionally skipping pictures/videos)
make dry-run FILE=my-channel.jsonl SKIP_MEDIA=1

# 4. Actually delete
make delete FILE=my-channel.jsonl SKIP_MEDIA=1
```

If `SLACK_CHANNEL` is set in `.env`, `make scan` picks it up and you can drop
the `CHANNEL=` arg.

## Usage

The tool has two subcommands: `scan` collects your message IDs into a local
JSONL file; `delete` reads that file and deletes them. Splitting the steps
means a crash mid-run doesn't force a re-scan, and you can review before
deleting.

The container's working directory is bind-mounted from your current directory,
so JSONL files appear right next to your project files. Full `make` reference:
`make help`.

### 1. Scan

```sh
make scan CHANNEL=my-channel
```

Writes `my-channel.jsonl` in the current directory. Each line looks like:

```json
{"channel":"C0123","ts":"1700000000.000100","text_preview":"hi","has_image":false,"has_video":false,"deleted":false}
```

Thread replies are included (with a `thread_ts` field).

Add `SINCE=` to only scan messages from a certain point onwards:

```sh
make scan CHANNEL=my-channel SINCE=2024-01-15
# or: SINCE=2024-01-15T10:00:00  or  SINCE=1700000000.000000
```

Dates/datetimes are interpreted in your local timezone; a raw Slack `ts` is
accepted as-is.

If the target file already exists, scan **resumes**: it reads the existing
records, finds the oldest main-timeline `ts`, and continues fetching older
messages from there. Records are deduped by `ts`.

### 2. Dry-run (recommended)

```sh
make dry-run FILE=my-channel.jsonl
```

Lists what would be deleted without touching Slack.

### 3. Delete

```sh
make delete FILE=my-channel.jsonl
```

Prompts for confirmation, then deletes each message and marks it
`deleted: true` in the JSONL file. The file is rewritten every 10 deletes so
progress survives a crash — re-running the same command resumes where it left
off.

Variables for `make delete` / `make dry-run`:

- `SKIP_MEDIA=1` — don't delete messages with a picture or video attachment
- `YES=1` — skip the confirmation prompt
- `DELAY=1.1-1.4` — seconds between deletes; single float (e.g. `4`) or
  `MIN-MAX` range for uniform random sleep (default `1.1-1.4`, ~45/min, just
  under Slack's Tier 3 limit; 429s are handled with backoff)

## Rate limits

`chat.delete` is a Tier 3 method (~50 requests/minute). The default delay is
tuned to stay under that. The client also honours `Retry-After` on HTTP 429 and
retries on `ratelimited` errors, so at worst a burst just slows down — you
won't get banned for hitting the limit.

For a large channel, expect roughly:

| Messages | Time at default delay |
|---------:|-----------------------|
|      100 | ~2 min                |
|    1 000 | ~22 min               |
|   10 000 | ~3.6 h                |

## Files

- `main.py` — the CLI
- `pyproject.toml` / `uv.lock` — Python dependency manifest (used only during
  the Docker image build)
- `Dockerfile`, `.dockerignore` — container setup
- `Makefile` — convenience wrapper around `docker run`
- `.env.example` — template for your `.env`
- `<channel>.jsonl` — generated by `scan`, consumed and updated by `delete`
  (written in the current working directory)

## Troubleshooting

- `channel #x not found (or no access)` — the account behind the token isn't a
  member of the channel, or the name is misspelled. For private channels you
  must be a member.
- `chat.delete failed: cant_delete_message` — the message isn't yours or is
  older than the workspace retention allows non-admins to delete.
- `invalid_auth` / `not_authed` — token or `d` cookie is stale; re-extract both
  from a fresh Slack session (they rotate).
