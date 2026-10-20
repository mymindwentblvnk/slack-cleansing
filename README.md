# slack-cleansing

Delete all of your own messages in a single Slack channel, or in your direct
messages with one person, using browser session tokens extracted from a
logged-in Slack web session.

## Quick start

```sh
# 1. One-time setup
cp .env.example .env             # fill in SLACK_WORKSPACE, SLACK_TOKEN, SLACK_COOKIE_D
make build

# 2. Collect your message IDs into my-channel.jsonl
make scan CHANNEL=my-channel

# 3. Preview what would be deleted
make dry-run CHANNEL=my-channel

# 4. Actually delete
make delete CHANNEL=my-channel
```

All commands:

| Command        | What it does                                                                 |
|----------------|------------------------------------------------------------------------------|
| `make build`   | Build the Docker image. Re-run after changing `main.py`.                     |
| `make scan`    | Find your messages in a channel and save their IDs to `<channel>.jsonl`.     |
| `make dry-run` | List what `delete` would remove from `<channel>.jsonl`, without touching Slack. |
| `make delete`  | Delete the messages listed in `<channel>.jsonl`, after a confirmation prompt. |
| `make stats`   | List the channels and DMs you've posted in, most messages first.            |
| `make shell`   | Open a shell inside the image, for debugging.                                |
| `make clean`   | Remove the Docker image.                                                     |
| `make help`    | Print the targets and variables.                                             |

Options, passed as `make <command> NAME=value`:

| Option         | Commands            | What it does                                                        |
|----------------|---------------------|---------------------------------------------------------------------|
| `CHANNEL=name` | scan, dry-run, delete | Channel to work on. Defaults to `SLACK_CHANNEL` from `.env`.      |
| `SINCE=date`   | scan, stats         | Only collect messages from this point on, e.g. `2024-01-15`.        |
| `TILL=date`    | scan, dry-run, delete, stats | Only messages before this point, e.g. `2026-09-01`, or `30d` for "keep the last 30 days". |
| `YES=1`        | delete              | Skip the confirmation prompt.                                       |
| `TOP=30`       | stats               | How many conversations to list; `0` lists all.                      |
| `DM=userID`    | scan, dry-run, delete | Work on your DM with this user (e.g. `U0123ABCD`) instead of a channel; uses `dm-<userID>.jsonl`. |
| `THREAD=ts`    | scan, dry-run, delete | Only one thread of `CHANNEL` / `DM`, by its timestamp; uses `<channel>-thread-<ts>.jsonl`. |

See [Get your Slack credentials](#get-your-slack-credentials) for the `.env`
values and [Usage](#usage) for details on each step.

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
| `SLACK_CHANNEL`  | no       | Default `CHANNEL=` for `make scan` / `make delete`  |
| `SLACK_SINCE`    | no       | Default `SINCE=` for `make scan`                    |
| `SLACK_TILL`     | no       | Default `TILL=` for scan, dry-run, delete and stats |

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

## Usage

The tool has two subcommands: `scan` collects your message IDs into a local
JSONL file; `delete` finds that file by channel name (`<channel>.jsonl`) and
deletes them. Splitting the steps
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
{"channel":"C0123","ts":"1700000000.000100","text_preview":"hi","deleted":false}
```

Thread replies are included (with a `thread_ts` field).

Add `SINCE=` to only scan messages from a certain point onwards:

```sh
make scan CHANNEL=my-channel SINCE=2024-01-15
# or: SINCE=2024-01-15T10:00:00  or  SINCE=1700000000.000000
```

Dates/datetimes are interpreted in your local timezone; a raw Slack `ts` is
accepted as-is.

By default scan asks Slack's search for just your messages
(`from:<you> in:<channel>`, 100 per request), so it stays fast on big channels.
This depends on Slack's search index: very recent messages may not be indexed
yet, and free workspaces only search the last 90 days.

If the target file already exists, new records are appended and deduped by
`ts`.

Add `TILL=` to only collect messages *before* a point in time — e.g. to keep
the last month:

```sh
make scan CHANNEL=my-channel TILL=30d
# or: TILL=2026-09-15  (that day's messages are kept)
```

`TILL` and `SINCE` take the same formats, including `<N>d` for "N days ago",
and can be combined.

### 2. Dry-run (recommended)

```sh
make dry-run CHANNEL=my-channel
```

Lists what would be deleted without touching Slack.

### 3. Delete

```sh
make delete CHANNEL=my-channel
```

Prompts for confirmation, then deletes each message and marks it
`deleted: true` in the JSONL file. The file is rewritten every 10 deletes so
progress survives a crash — re-running the same command resumes where it left
off.

If there's no `<channel>.jsonl` yet, `delete` (and `dry-run`) prints a warning
and runs a search scan first, honouring `TILL` and `SLACK_SINCE`. After that
scan, `delete` always asks before deleting, even with `YES=1`.

Variables for `make delete` / `make dry-run`:

- `TILL=30d` / `TILL=2026-09-15` — only delete messages before that point. This
  also applies to a file scanned without `TILL`, so it's a safe way to keep
  recent messages; pass the same value to `dry-run` and `delete`.
- `YES=1` — skip the confirmation prompt

### Direct messages with one person

Use `DM=` instead of `CHANNEL=` with the same commands:

```sh
make scan DM=U0123ABCD
make dry-run DM=U0123ABCD
make delete DM=U0123ABCD
```

`DM=` takes the person's Slack user ID (`U0123ABCD`), since names aren't
unique. To find it, open their profile in Slack → **⋮** → **Copy member ID**.
Only an existing DM is used, and the scan prints who it belongs to.

For a DM, `scan` always reads the whole conversation (including threads)
rather than using search, and writes `dm-<DM>.jsonl`; `dry-run` and `delete`
read that file. If the file already exists, scan **resumes**: it continues with
messages older than the oldest one saved. All other options work as for
channels.

### A single thread

Add `THREAD=` to `CHANNEL=` or `DM=` to work on just one thread:

```sh
make scan CHANNEL=my-channel THREAD=1700000000.000100
make dry-run CHANNEL=my-channel THREAD=1700000000.000100
make delete CHANNEL=my-channel THREAD=1700000000.000100
```

`THREAD=` takes the `ts` of the thread's first message. To find it:

1. Hover over the thread's **first message** (the one in the channel, not a
   reply) → **⋮** → **Copy link**.
2. The link looks like
   `https://acme-corp.slack.com/archives/C0123ABCD/p1700000000000100`. Pass
   the last part as-is: `THREAD=p1700000000000100` is the same as
   `THREAD=1700000000.000100`.

If you copied a reply's link instead, it ends in
`?thread_ts=1700000000.000100&cid=...`; the `thread_ts` value is the one to
use. Passing a reply's own ts makes `scan` stop and print the right `THREAD=`.

A timestamp only identifies a thread within one conversation, so `CHANNEL=` or
`DM=` is required.

`scan` reads the whole thread (no search) and keeps your messages in it,
including the first message if it's yours, and writes
`<channel>-thread-<ts>.jsonl` (`dm-<userID>-thread-<ts>.jsonl` for a DM).
Deleting a thread's first message leaves the replies in place, under a
"This message was deleted." placeholder. All other options work as usual.

### Where have I posted?

```sh
make stats                  # top 30
make stats TOP=0            # every conversation
make stats SINCE=2024-01-01
make stats TILL=30d         # what deleting all but the last 30 days would hit
```

Counts your messages per channel, private channel (🔒), group DM and DM via
Slack search (`from:<you>`), and prints them most-posted first:

```
   1. 4210  #general  (C0123ABCD)
   2.  873  DM UHKC0PXL5  (D0456EFGH)
```

DMs are shown by the other person's user ID, which is what `DM=` takes. Slack
caps one search at about 10 000 results; past that, `stats` searches again for
older messages, so it takes a few seconds per 100 messages. Like `scan`, it
only sees what Slack's search index has (free workspaces: the last 90 days).

## Rate limits

`chat.delete` is a Tier 3 method (~50 requests/minute). `delete` waits a
random 1.1–1.4 s between deletes (~45/minute) to stay under that. The client also honours `Retry-After` on HTTP 429 and
retries on `ratelimited` errors, so at worst a burst just slows down — you
won't get banned for hitting the limit.

For a large channel, expect roughly:

| Messages | Time                  |
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
- `dm-<userID>.jsonl` — the same for `DM=<userID>`

## Troubleshooting

- `channel #x not found (or no access)` — the account behind the token isn't a
  member of the channel, or the name is misspelled. For private channels you
  must be a member.
- `chat.delete failed: cant_delete_message` — the message isn't yours or is
  older than the workspace retention allows non-admins to delete.
- `invalid_auth` / `not_authed` — token or `d` cookie is stale; re-extract both
  from a fresh Slack session (they rotate).
