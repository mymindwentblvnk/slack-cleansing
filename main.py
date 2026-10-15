"""Delete all of your own messages in a Slack channel using browser session tokens.

Three subcommands:
  scan    - find all your message IDs in a channel (via Slack search, or the
            whole channel history with --full), save to a JSONL file; with
            --dm <user ID>, scan your direct messages with that person instead
  delete  - read the channel's JSONL file and delete each message, marking progress
  stats   - count your messages per channel / DM (via Slack search), top first

Uses the unofficial xoxc token + `d` cookie from a logged-in Slack web session.
This is against Slack's Terms of Service; use at your own risk.

Env:
    SLACK_TOKEN='xoxc-...'
    SLACK_COOKIE_D='xoxd-...'   # value of the `d` cookie (URL-decoded)

Example:
    uv run main.py scan   --workspace my-workspace --channel my-channel
    uv run main.py delete --workspace my-workspace --channel my-channel
    uv run main.py scan   --workspace my-workspace --dm U0123ABCD
    uv run main.py delete --workspace my-workspace --dm U0123ABCD

How to obtain the tokens (Chrome/Firefox):
  1. Open https://<workspace>.slack.com and log in.
  2. DevTools -> Application -> Cookies -> copy value of cookie `d` (xoxd-...).
  3. DevTools console:  JSON.parse(localStorage.localConfig_v2).teams
     Copy your team's `token` (xoxc-...).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import httpx

API_BASE_TEMPLATE = "https://{workspace}.slack.com/api"


class SlackError(RuntimeError):
    pass


# Per-method minimum seconds between requests (client-side throttle).
# Slack Tier 3 nominally allows ~50/min but sustained bursts get 429'd,
# especially on conversations.replies.
_METHOD_MIN_INTERVAL = {
    "conversations.replies": 1.5,
    "conversations.history": 1.5,
    "conversations.list": 1.5,
    # Tier 2 (~20/min).
    "search.messages": 3.0,
    "chat.delete": 1.3,
}
_DEFAULT_MIN_INTERVAL = 0.2


def _describe_user(user: dict[str, Any]) -> str:
    profile = user.get("profile") or {}
    real = profile.get("real_name") or user.get("real_name") or ""
    gone = ", deactivated" if user.get("deleted") else ""
    return f"{user.get('name')} ({real}, {user['id']}{gone})"


class SlackClient:
    def __init__(self, workspace: str, token: str, cookie_d: str) -> None:
        self.workspace = workspace
        self.token = token
        self.base_url = API_BASE_TEMPLATE.format(workspace=workspace)
        self.client = httpx.Client(
            timeout=30.0,
            cookies={"d": cookie_d},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
            },
        )
        self._last_call: dict[str, float] = {}
        self.search_capped = False

    def close(self) -> None:
        self.client.close()

    def _throttle(self, method: str) -> None:
        min_interval = _METHOD_MIN_INTERVAL.get(method, _DEFAULT_MIN_INTERVAL)
        last = self._last_call.get(method, 0.0)
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)

    def _post(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = {"token": self.token, **data}
        files = {k: (None, str(v)) for k, v in payload.items()}

        for attempt in range(8):
            self._throttle(method)
            resp = self.client.post(f"{self.base_url}/{method}", files=files)
            self._last_call[method] = time.monotonic()
            if resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", "1"))
                # Also back off future calls: bump the min interval a bit.
                _METHOD_MIN_INTERVAL[method] = min(
                    _METHOD_MIN_INTERVAL.get(method, _DEFAULT_MIN_INTERVAL) + 0.5,
                    5.0,
                )
                print(
                    f"  rate limited on {method}, sleeping {retry}s "
                    f"(interval now {_METHOD_MIN_INTERVAL[method]:.1f}s)",
                    file=sys.stderr,
                )
                time.sleep(retry)
                continue
            resp.raise_for_status()
            body = resp.json()
            if not body.get("ok"):
                err = body.get("error", "unknown_error")
                if err == "ratelimited":
                    time.sleep(1 + attempt)
                    continue
                raise SlackError(f"{method} failed: {err} ({body})")
            return body
        raise SlackError(f"{method} exhausted retries")

    def auth_test(self) -> dict[str, Any]:
        return self._post("auth.test", {})

    def find_channel_id(self, name: str) -> str:
        name = name.lstrip("#")
        cursor = ""
        while True:
            body = self._post(
                "conversations.list",
                {
                    "limit": 1000,
                    "cursor": cursor,
                    "types": "public_channel,private_channel",
                    "exclude_archived": "true",
                },
            )
            for ch in body.get("channels", []):
                if ch.get("name") == name:
                    return ch["id"]
            cursor = body.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                raise SlackError(f"channel #{name} not found (or no access)")

    def find_dm(self, user_id: str) -> tuple[str, str]:
        """Return (DM channel ID, description of the user) for your existing
        DM with `user_id`."""
        cursor = ""
        while True:
            body = self._post(
                "conversations.list",
                {"limit": 1000, "cursor": cursor, "types": "im"},
            )
            for ch in body.get("channels", []):
                if ch.get("user") == user_id:
                    user = self._post("users.info", {"user": user_id})["user"]
                    return ch["id"], _describe_user(user)
            cursor = body.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                raise SlackError(f"no existing DM with user {user_id}")

    def iter_messages(
        self, channel_id: str, latest: str | None = None,
        oldest: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        cursor = ""
        while True:
            params = {"channel": channel_id, "limit": 200, "cursor": cursor}
            if latest:
                # `latest` is exclusive when `inclusive=false` (default).
                params["latest"] = latest
            if oldest:
                params["oldest"] = oldest
            body = self._post("conversations.history", params)
            for msg in body.get("messages", []):
                yield msg
            if not body.get("has_more"):
                return
            cursor = body.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return

    def iter_thread_replies(
        self, channel_id: str, thread_ts: str
    ) -> Iterator[dict[str, Any]]:
        cursor = ""
        while True:
            body = self._post(
                "conversations.replies",
                {
                    "channel": channel_id,
                    "ts": thread_ts,
                    "limit": 200,
                    "cursor": cursor,
                },
            )
            for msg in body.get("messages", []):
                if msg.get("ts") == thread_ts:
                    continue
                yield msg
            if not body.get("has_more"):
                return
            cursor = body.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return

    def iter_search(
        self, query: str, warn_cap: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Yield `search.messages` matches, newest first. Sets
        `self.search_capped` if Slack's page limit cut the results short."""
        self.search_capped = False
        cursor = "*"
        page = 1
        while True:
            params: dict[str, Any] = {
                "query": query,
                "count": 100,
                "sort": "timestamp",
                "sort_dir": "desc",
            }
            if cursor:
                params["cursor"] = cursor
            else:
                params["page"] = page
            messages = self._post("search.messages", params).get("messages", {})
            matches = messages.get("matches", [])
            yield from matches
            if not matches:
                return
            paging = messages.get("paging", {})
            cursor = (
                messages.get("pagination", {}).get("next_cursor")
                or paging.get("next_cursor")
                or ""
            )
            if cursor:
                continue
            # No cursor in the response: fall back to page numbers, which Slack
            # caps at 100 pages.
            if page >= paging.get("pages", 1):
                return
            page += 1
            if page > 100:
                self.search_capped = True
                if not warn_cap:
                    return
                print(
                    "  WARNING: search results capped at 100 pages; re-run with "
                    "an older --since window or with --full to get the rest",
                    file=sys.stderr,
                )
                return

    def delete_message(self, channel_id: str, ts: str) -> None:
        self._post("chat.delete", {"channel": channel_id, "ts": ts})


# ---------- scan ----------

def _parse_time(spec: str, flag: str) -> str:
    """Return a Slack ts string ('seconds.microseconds') from a raw ts, a
    local-time date/datetime like '2024-01-15' or '2024-01-15T10:00:00', or a
    number of days ago like '30d'."""
    s = spec.strip()
    if s[:-1].isdigit() and s[-1:].lower() == "d":
        dt = datetime.now().astimezone() - timedelta(days=int(s[:-1]))
        return f"{dt.timestamp():.6f}"
    # Already a slack ts.
    if "." in s and s.replace(".", "").isdigit():
        return s
    if s.isdigit():
        return f"{s}.000000"
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).astimezone()
            return f"{dt.timestamp():.6f}"
        except ValueError:
            continue
    raise ValueError(
        f"{flag} {spec!r}: use a Slack ts (1700000000.000000), a date like "
        f"2024-01-15 / 2024-01-15T10:00:00, or a number of days ago like 30d"
    )


def _time_window(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Parse --since / --till into (since_ts, till_ts) and say what they mean."""
    since_ts = _parse_time(args.since, "--since") if args.since else None
    till_ts = _parse_time(args.till, "--till") if args.till else None
    if since_ts:
        print(f"Only messages from {_fmt_ts(since_ts)} on")
    if till_ts:
        print(f"Only messages before {_fmt_ts(till_ts)}")
    return since_ts, till_ts


def _fmt_ts(ts: str) -> str:
    return f"{datetime.fromtimestamp(float(ts)).astimezone():%Y-%m-%d %H:%M %Z}"


def _in_window(ts: str, since_ts: str | None, till_ts: str | None) -> bool:
    return (since_ts is None or float(ts) >= float(since_ts)) and (
        till_ts is None or float(ts) < float(till_ts)
    )


def _search_window(since_ts: str | None, till_ts: str | None) -> str:
    """`after:` / `before:` search modifiers covering the window. They are
    day-granular, exclusive and evaluated in Slack's idea of your timezone, so
    this searches a little wider; filter exactly with _in_window."""
    q = ""
    if since_ts:
        day = datetime.fromtimestamp(float(since_ts)) - timedelta(days=2)
        q += f" after:{day:%Y-%m-%d}"
    if till_ts:
        day = datetime.fromtimestamp(float(till_ts)) + timedelta(days=2)
        q += f" before:{day:%Y-%m-%d}"
    return q

def _msg_has_image(msg: dict[str, Any]) -> bool:
    for fobj in msg.get("files") or []:
        mt = (fobj.get("mimetype") or "").lower()
        if mt.startswith("image/"):
            return True
        if (fobj.get("filetype") or "").lower() in {"jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tiff"}:
            return True
    return False


def _msg_has_video(msg: dict[str, Any]) -> bool:
    for fobj in msg.get("files") or []:
        mt = (fobj.get("mimetype") or "").lower()
        if mt.startswith("video/"):
            return True
        if (fobj.get("filetype") or "").lower() in {"mp4", "mov", "webm", "mkv", "avi", "m4v"}:
            return True
    return False


def _search_thread_ts(match: dict[str, Any]) -> str | None:
    """Search matches don't carry `thread_ts`; it's only in the permalink."""
    qs = parse_qs(urlparse(match.get("permalink") or "").query)
    return (qs.get("thread_ts") or [None])[0]


def _scan_search(
    slack: SlackClient, channel_id: str, user_id: str, since_ts: str | None,
    till_ts: str | None, emit: Any,
) -> int:
    query = f"from:<@{user_id}> in:<#{channel_id}>"
    query += _search_window(since_ts, till_ts)
    print(f"Searching: {query}", flush=True)

    scanned = 0
    for match in slack.iter_search(query):
        scanned += 1
        ts = match.get("ts")
        if (
            ts
            and match.get("user") == user_id
            and (match.get("channel") or {}).get("id") == channel_id
            and _in_window(ts, since_ts, till_ts)
        ):
            rec = {"channel": channel_id, "ts": ts}
            thread_ts = _search_thread_ts(match)
            if thread_ts and thread_ts != ts:
                rec["thread_ts"] = thread_ts
            emit({
                **rec,
                "text_preview": (match.get("text", "") or "")[:120],
                "has_image": _msg_has_image(match),
                "has_video": _msg_has_video(match),
                "deleted": False,
            })
        if scanned % 100 == 0:
            print(f"  scanned={scanned}", flush=True)
    return scanned


def _default_file(args: argparse.Namespace) -> str:
    if args.dm:
        return f"dm-{args.dm}.jsonl"
    return f"{args.channel.lstrip('#')}.jsonl"


def cmd_scan(args: argparse.Namespace, slack: SlackClient) -> int:
    me = slack.auth_test()
    user_id = me["user_id"]
    print(f"Authenticated as {me.get('user')} ({user_id}) in {me.get('team')}")

    since_ts, till_ts = _time_window(args)

    if args.dm:
        channel_id, who = slack.find_dm(args.dm)
        print(f"DM with {who} -> {channel_id}")
        # Search may not support DMs reliably; read the whole conversation.
        args.full = True
    else:
        channel_id = slack.find_channel_id(args.channel)
        print(f"Channel #{args.channel.lstrip('#')} -> {channel_id}")

    out_path = Path(args.output or _default_file(args))

    # Resume support: infer the point from the existing JSONL.
    # `conversations.history` walks newest -> oldest, so the smallest main-timeline
    # ts already saved is the resume boundary. We pass it as `latest` (exclusive).
    existing_ts: set[str] = set()
    resume_latest: str | None = None
    mode = "w"
    if out_path.exists():
        if args.overwrite:
            pass  # will truncate
        else:
            oldest_main: str | None = None
            with out_path.open("r", encoding="utf-8") as rf:
                for line in rf:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("ts")
                    if not ts:
                        continue
                    existing_ts.add(ts)
                    # Only main-timeline messages (no thread_ts) drive the resume
                    # boundary, because conversations.history paginates that stream.
                    if "thread_ts" not in rec:
                        if oldest_main is None or float(ts) < float(oldest_main):
                            oldest_main = ts
            resume_latest = oldest_main
            mode = "a"
            if not args.full:
                # Search only returns your own messages, so a full re-run is cheap.
                resume_latest = None
                print(
                    f"{out_path} exists with {len(existing_ts)} records; "
                    f"appending new ones (dedup by ts)."
                )
            elif resume_latest:
                print(
                    f"Resuming: {len(existing_ts)} records in {out_path}, "
                    f"continuing older than ts={resume_latest}"
                )
            else:
                print(
                    f"{out_path} exists with {len(existing_ts)} records but no "
                    f"main-timeline entry to resume from; re-scanning from top "
                    f"(dedup by ts)."
                )

    count = 0
    scanned = 0
    with out_path.open(mode, encoding="utf-8") as f:
        def emit(rec: dict[str, Any]) -> None:
            nonlocal count
            if rec["ts"] in existing_ts:
                return
            existing_ts.add(rec["ts"])
            f.write(json.dumps(rec) + "\n")
            f.flush()
            count += 1

        if not args.full:
            scanned = _scan_search(slack, channel_id, user_id, since_ts, till_ts, emit)
            print(f"Saved {count} new message IDs to {out_path} (scanned {scanned} this run)")
            return 0

        print("Scanning main timeline...", flush=True)
        # `latest` is exclusive, so it works both as resume point and as --till.
        latest = min(
            (t for t in (resume_latest, till_ts) if t), key=float, default=None,
        )
        for msg in slack.iter_messages(channel_id, latest=latest, oldest=since_ts):
            scanned += 1
            if msg.get("user") == user_id:
                emit({
                    "channel": channel_id,
                    "ts": msg["ts"],
                    "text_preview": (msg.get("text", "") or "")[:120],
                    "has_image": _msg_has_image(msg),
                    "has_video": _msg_has_video(msg),
                    "deleted": False,
                })
            if msg.get("thread_ts") == msg.get("ts") and msg.get("reply_count", 0) > 0:
                for reply in slack.iter_thread_replies(channel_id, msg["ts"]):
                    scanned += 1
                    # Replies can be newer than their thread's first message.
                    if reply.get("user") == user_id and _in_window(
                        reply["ts"], since_ts, till_ts
                    ):
                        emit({
                            "channel": channel_id,
                            "ts": reply["ts"],
                            "thread_ts": msg["ts"],
                            "text_preview": (reply.get("text", "") or "")[:120],
                            "has_image": _msg_has_image(reply),
                            "has_video": _msg_has_video(reply),
                            "deleted": False,
                        })
            if scanned % 50 == 0:
                print(f"  scanned={scanned}  new_yours={count}", flush=True)

    print(f"Saved {count} new message IDs to {out_path} (scanned {scanned} this run)")
    return 0


# ---------- delete ----------

def _load_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _rewrite_records(path: Path, records: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    tmp.replace(path)


def _parse_delay(spec: str) -> tuple[float, float]:
    """Return (min, max) seconds. Accepts 'N' or 'MIN-MAX'."""
    s = spec.strip()
    if "-" in s:
        lo, hi = s.split("-", 1)
        lo_f, hi_f = float(lo), float(hi)
        if lo_f < 0 or hi_f < lo_f:
            raise ValueError(f"invalid delay range: {spec!r}")
        return lo_f, hi_f
    v = float(s)
    return v, v


def cmd_delete(args: argparse.Namespace, slack: SlackClient) -> int:
    path = Path(args.file or _default_file(args))
    if not path.exists():
        print(f"ERROR: {path} not found; run scan first", file=sys.stderr)
        return 2

    records = _load_records(path)
    pending = [r for r in records if not r.get("deleted")]

    skipped_new = 0
    if args.till:
        till_ts = _parse_time(args.till, "--till")
        print(f"Only deleting messages before {_fmt_ts(till_ts)}")
        kept = [r for r in pending if float(r["ts"]) < float(till_ts)]
        skipped_new = len(pending) - len(kept)
        pending = kept

    skipped_media = 0
    if args.skip_media:
        kept = []
        for r in pending:
            if r.get("has_image") or r.get("has_video"):
                skipped_media += 1
            else:
                kept.append(r)
        pending = kept

    # Delete oldest first (ts is a Slack "seconds.microseconds" string; float() sorts fine).
    pending.sort(key=lambda r: float(r["ts"]))
    already = len(records) - (len(pending) + skipped_media + skipped_new)
    print(
        f"{len(records)} total, {already} already deleted, "
        f"{skipped_media} skipped (media), "
        + (f"{skipped_new} skipped (not before --till), " if args.till else "")
        + f"{len(pending)} pending (oldest first)"
    )

    if args.dry_run or not pending:
        for r in pending:
            print(f"  would delete ts={r['ts']}  {r.get('text_preview','')!r}")
        return 0

    if not args.yes:
        reply = input(f"Delete {len(pending)} messages? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return 1

    delay_lo, delay_hi = _parse_delay(args.delay)
    print(
        f"Sleeping {delay_lo:g}s between deletes"
        if delay_lo == delay_hi
        else f"Sleeping random {delay_lo:g}-{delay_hi:g}s between deletes"
    )

    failed = 0
    for i, rec in enumerate(pending, 1):
        try:
            slack.delete_message(rec["channel"], rec["ts"])
            rec["deleted"] = True
            print(f"[{i}/{len(pending)}] deleted ts={rec['ts']}")
        except SlackError as e:
            failed += 1
            print(f"[{i}/{len(pending)}] FAILED ts={rec['ts']}: {e}", file=sys.stderr)
        # Persist progress every N deletes so a crash doesn't lose state.
        if i % 10 == 0:
            _rewrite_records(path, records)
        if i < len(pending):
            time.sleep(random.uniform(delay_lo, delay_hi))

    _rewrite_records(path, records)
    deleted = sum(1 for r in records if r.get("deleted"))
    print(f"Done. deleted_total={deleted} failed_this_run={failed}")
    return 0 if failed == 0 else 1


# ---------- stats ----------

def _conversation_label(ch: dict[str, Any]) -> str:
    if ch.get("is_im"):
        return f"DM {ch.get('name')}"  # a DM's search "name" is the user ID
    if ch.get("is_mpim"):
        return f"group DM {ch.get('name')}"
    prefix = "🔒" if ch.get("is_private") or ch.get("is_group") else "#"
    return f"{prefix}{ch.get('name')}"


def cmd_stats(args: argparse.Namespace, slack: SlackClient) -> int:
    me = slack.auth_test()
    user_id = me["user_id"]
    print(f"Authenticated as {me.get('user')} ({user_id}) in {me.get('team')}")

    since_ts, till_ts = _time_window(args)
    base = f"from:<@{user_id}>" + _search_window(since_ts, None)

    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    before: datetime | None = None
    if till_ts:
        before = datetime.fromtimestamp(float(till_ts)) + timedelta(days=2)
    # Slack stops returning results after ~100 pages, so whenever a pass is cut
    # short, search again for messages older than the oldest one seen. `before:` is
    # day-granular and timezone-fuzzy, so the windows overlap a little and
    # dedup handles that; a pass with nothing new moves back a day.
    while True:
        query = base + (f" before:{before:%Y-%m-%d}" if before else "")
        print(f"Searching: {query}", flush=True)
        new = 0
        hits = 0
        for match in slack.iter_search(query, warn_cap=False):
            hits += 1
            ch = match.get("channel") or {}
            ts = match.get("ts")
            if not ts or not ch.get("id") or match.get("user") != user_id:
                continue
            if not _in_window(ts, since_ts, till_ts):
                continue
            key = (ch["id"], ts)
            if key in seen:
                continue
            seen.add(key)
            new += 1
            counts[ch["id"]] = counts.get(ch["id"], 0) + 1
            labels.setdefault(ch["id"], _conversation_label(ch))
            if len(seen) % 1000 == 0:
                print(f"  counted={len(seen)}", flush=True)
        if not hits or (new and not slack.search_capped):
            break
        if new:
            oldest = min(float(ts) for _, ts in seen)
            before = datetime.fromtimestamp(oldest) + timedelta(days=2)
        elif before:
            before -= timedelta(days=1)
        else:
            break

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    shown = ranked[: args.top] if args.top else ranked
    print()
    print(
        f"{len(seen)} messages in {len(counts)} conversations"
        + (f" (top {len(shown)})" if len(shown) < len(ranked) else "")
    )
    width = len(str(shown[0][1])) if shown else 1
    for rank, (cid, n) in enumerate(shown, 1):
        print(f"{rank:>4}. {n:>{width}}  {labels[cid]}  ({cid})")
    return 0


# ---------- entrypoint ----------

def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no deps). Does not override already-set env vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (len(val) >= 2) and val[0] == val[-1] and val[0] in {'"', "'"}:
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> int:
    # Load .env from CWD before argparse so defaults can see the vars.
    _load_dotenv(Path(os.environ.get("DOTENV_PATH", ".env")))

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--workspace", default=os.environ.get("SLACK_WORKSPACE"),
        help="Workspace subdomain, e.g. my-workspace (env: SLACK_WORKSPACE)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="Collect message IDs to a JSONL file")
    ps.add_argument(
        "--channel", default=os.environ.get("SLACK_CHANNEL"),
        help="Channel name, e.g. my-channel (env: SLACK_CHANNEL)",
    )
    ps.add_argument(
        "--dm",
        help="Scan your DM with this user ID (e.g. U0123ABCD) instead of a "
             "channel; writes dm-<user ID>.jsonl and "
             "always reads the whole conversation",
    )
    ps.add_argument("-o", "--output", help="Output JSONL path (default <channel>.jsonl)")
    ps.add_argument("--overwrite", action="store_true")
    ps.add_argument(
        "--full", action="store_true",
        help="Read the whole channel history (and every thread) instead of "
             "finding your messages via Slack search (from:me in:channel). "
             "Much slower on big channels, but doesn't rely on Slack's search "
             "index.",
    )
    ps.add_argument(
        "--since", default=os.environ.get("SLACK_SINCE"),
        help="Only scan messages at or after this time. Accepts a Slack ts "
             "(e.g. 1700000000.000000) or a local date/datetime "
             "(2024-01-15 or 2024-01-15T10:00:00), or days ago (30d). "
             "(env: SLACK_SINCE)",
    )
    ps.add_argument(
        "--till", default=os.environ.get("SLACK_TILL"),
        help="Only messages before this time, e.g. 30d to leave the last 30 "
             "days alone. Same formats as --since. (env: SLACK_TILL)",
    )

    pd = sub.add_parser("delete", help="Delete messages listed in a JSONL file")
    pd.add_argument(
        "--channel", default=os.environ.get("SLACK_CHANNEL"),
        help="Channel name; deletes from <channel>.jsonl (env: SLACK_CHANNEL)",
    )
    pd.add_argument(
        "--dm",
        help="Delete from dm-<user ID>.jsonl (produced by scan --dm <user ID>)",
    )
    pd.add_argument(
        "--file",
        help="JSONL produced by scan, if it isn't <channel>.jsonl (scan -o)",
    )
    pd.add_argument(
        "--till", default=os.environ.get("SLACK_TILL"),
        help="Only messages before this time, e.g. 30d to leave the last 30 "
             "days alone. Same formats as --since. (env: SLACK_TILL)",
    )
    pd.add_argument("--dry-run", action="store_true")
    pd.add_argument("--yes", action="store_true")
    pd.add_argument(
        "--skip-media", action="store_true",
        help="Skip messages that contain a picture or video attachment",
    )
    pd.add_argument(
        "--delay", default="1.1-1.4",
        help="Seconds between deletes: single float (e.g. 4) or range 'MIN-MAX' "
             "for uniform random sleep (default '1.1-1.4', ~45/min)",
    )

    pt = sub.add_parser("stats", help="Count your messages per channel / DM")
    pt.add_argument(
        "--since", default=os.environ.get("SLACK_SINCE"),
        help="Only count messages at or after this time (same formats as "
             "scan --since). (env: SLACK_SINCE)",
    )
    pt.add_argument(
        "--till", default=os.environ.get("SLACK_TILL"),
        help="Only messages before this time, e.g. 30d to leave the last 30 "
             "days alone. Same formats as --since. (env: SLACK_TILL)",
    )
    pt.add_argument(
        "--top", type=int, default=30,
        help="How many conversations to list (default 30, 0 = all)",
    )

    args = p.parse_args()

    if not args.workspace:
        print("ERROR: --workspace or SLACK_WORKSPACE is required", file=sys.stderr)
        return 2
    if args.cmd == "scan" and not (args.channel or args.dm):
        print("ERROR: scan: --channel, --dm or SLACK_CHANNEL is required", file=sys.stderr)
        return 2
    if args.cmd == "delete" and not (args.channel or args.file or args.dm):
        print("ERROR: delete: --channel, --dm or SLACK_CHANNEL is required", file=sys.stderr)
        return 2

    if getattr(args, "dm", None) and not re.fullmatch(r"[UW][A-Z0-9]{2,}", args.dm):
        print(
            f"ERROR: --dm takes a Slack user ID like U0123ABCD, got {args.dm!r} "
            f"(profile -> ⋮ -> Copy member ID)",
            file=sys.stderr,
        )
        return 2

    token = os.environ.get("SLACK_TOKEN")
    cookie_d = os.environ.get("SLACK_COOKIE_D")
    if not token or not cookie_d:
        print("ERROR: set SLACK_TOKEN (xoxc-...) and SLACK_COOKIE_D (xoxd-...)", file=sys.stderr)
        return 2

    slack = SlackClient(args.workspace, token, cookie_d)
    try:
        if args.cmd == "scan":
            return cmd_scan(args, slack)
        if args.cmd == "delete":
            return cmd_delete(args, slack)
        if args.cmd == "stats":
            return cmd_stats(args, slack)
        return 2
    finally:
        slack.close()


if __name__ == "__main__":
    sys.exit(main())
