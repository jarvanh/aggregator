#!/usr/bin/env python3
"""Prepare raw public node lists for the aggregator pipeline.

Some public list files contain malformed share links (e.g. raw '=' in a trojan
password) that make subconverter reject the WHOLE file. This script downloads
each list, sanitizes known-bad patterns, converts it in chunks, and quarantines
any chunk that fails by recursively bisecting it, so a single poison line only
costs its own block. Surviving lines are re-packed as a base64 link list that
subconverter imports cleanly.

Upstream lists die and get deleted all the time, so a single unavailable source
is a warning, not a failure: the run continues with whatever is still live and
only aborts when no source at all produced output (see main()).
"""

import base64
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SUB_DIR = os.path.join(ROOT, "subconverter")
BIN = os.path.join(SUB_DIR, "subconverter-linux-amd")
# airport.py resolves `file:///` subs by stripping 8 chars and treating the rest
# as a path relative to the process cwd (the repo root), so the files must be
# written to <repo>/tmp/subs for `file:///tmp/subs/x.txt` to resolve.
OUT_DIR = os.environ.get("PREP_OUT_DIR", os.path.join(ROOT, "tmp", "subs"))
CHUNK = 1000
# bisect down to single lines: one poison link should cost its own line, not a
# 30-line block. subconverter starts fast enough that this stays cheap.
MIN_SLIVER = 1

SOURCES = {
    "barryfar": "https://raw.githubusercontent.com/barry-far/V2ray-Config/main/All_Configs_Sub.txt",
    # Epodonios/v2ray-configs was deleted upstream (404 since 2026-04) and its
    # forks are stale, so the live equivalent is MatinGhanbari's list.
    "matin": "https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/v2ray/all_sub.txt",
    "mahdibland": "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge.txt",
}

# these codes mean the source is gone for good: retrying only wastes time
DEAD_CODES = {404, 410, 451}

PROTOCOLS = {"vmess", "vless", "trojan", "ss", "ssr", "hysteria", "hysteria2", "hy2", "tuic", "anytls", "socks5"}
ENCODE_USERINFO = {"trojan", "vless", "hysteria", "hysteria2", "hy2", "tuic", "anytls", "socks5"}
# clash/mihomo builtin policies: a proxy sharing one of these names is rejected
# at config parse time and kills the whole batch
BUILTIN_NAMES = {"direct", "reject", "reject-drop", "pass", "global", "compatible"}


def node_name(line: str) -> str:
    name = line.rsplit("#", 1)[1] if "#" in line else ""
    return urllib.parse.unquote(name).strip().lower()


def decode_list(body: bytes) -> str:
    """Plaintext lists pass through; base64 exports get unwrapped, so a source
    that switches format does not silently yield zero usable nodes."""
    text = body.decode("utf8", "ignore")
    if "://" in text:
        return text
    compact = "".join(text.split())
    if len(compact) < 16:
        return text
    try:
        decoded = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf8", "ignore")
    except Exception:
        return text
    return decoded if "://" in decoded else text


def fetch(url: str, attempts: int = 3) -> str:
    """Download a list, retrying transient failures but giving up fast on 404."""
    headers = {"User-Agent": "clash.meta; mihomo"}
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                return decode_list(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in DEAD_CODES:
                raise
            last = exc
        except OSError as exc:
            last = exc
        if i + 1 < attempts:
            time.sleep(2**i)
    raise last if last else RuntimeError("fetch failed")


def sanitize(text: str) -> list[str]:
    lines = []
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        proto, sep, rest = line.partition("://")
        if not sep or proto.lower() not in PROTOCOLS:
            continue
        if node_name(line) in BUILTIN_NAMES:
            continue
        proto = proto.lower()
        if proto in ENCODE_USERINFO:
            userinfo, at, tail = rest.partition("@")
            if at and ("=" in userinfo or " " in userinfo):
                userinfo = urllib.parse.quote(userinfo, safe="")
                line = f"{proto}://{userinfo}@{tail}"
        lines.append(line)
    return lines


def yaml_ok(path: str) -> bool:
    """True when the converted clash config actually parses.

    subconverter happily exits 0 while emitting unquoted IPv6 hosts
    (`server: 2a03:90c0:382:1::38f`) or other broken flow mappings, and the
    aggregator later loads this file with PyYAML: one bad line and the whole
    source is dropped at import time. Mirror airport.py's loader (SafeLoader,
    then a permissive `str` constructor) and reject what it would reject.
    """
    for permissive in (False, True):
        try:
            with open(path, "r", encoding="utf8", errors="ignore") as f:
                if permissive:
                    yaml.add_multi_constructor(
                        "str",
                        lambda loader, suffix, node: str(node.value),
                        Loader=yaml.SafeLoader,
                    )
                yaml.safe_load(f)
            return True
        except Exception:
            continue
    return False


def convert(lines: list[str], artifact: str) -> bool:
    if not os.access(BIN, os.X_OK):
        os.chmod(BIN, 0o755)
    ini = os.path.join(SUB_DIR, "generate.ini")
    if os.path.exists(ini):
        os.remove(ini)
    source = f"{artifact}.txt"
    # subconverter's link import only accepts base64-encoded lists
    payload = base64.b64encode("\n".join(lines).encode("utf8")).decode("ascii")
    with open(os.path.join(SUB_DIR, source), "w", encoding="ascii") as f:
        f.write(payload)
    with open(ini, "w", encoding="utf8") as f:
        f.write(
            f"[{artifact}]\npath={artifact}.yaml\nurl={source}\n"
            f"expand=false\ntarget=clash\nlist=true\nadd_emoji=false\n\n"
        )
    proc = subprocess.run([BIN, "-g", "--artifact", artifact], cwd=ROOT, capture_output=True, timeout=300)
    out = os.path.join(SUB_DIR, f"{artifact}.yaml")
    ok = (
        proc.returncode == 0
        and os.path.exists(out)
        and os.path.getsize(out) > 50
        and yaml_ok(out)
    )
    if os.path.exists(out):
        os.remove(out)
    if os.path.exists(os.path.join(SUB_DIR, source)):
        os.remove(os.path.join(SUB_DIR, source))
    return ok


def quarantine(lines: list[str], tag: str) -> list[str]:
    if len(lines) <= MIN_SLIVER:
        return []
    mid = len(lines) // 2
    kept = []
    for i, part in enumerate((lines[:mid], lines[mid:])):
        if convert(part, f"{tag}q{i}"):
            kept.extend(part)
        else:
            kept.extend(quarantine(part, f"{tag}q{i}"))
    return kept


def prepare(name: str, url: str) -> tuple[int, int]:
    lines = sanitize(fetch(url))
    total = len(lines)
    good = []
    for i in range(0, len(lines), CHUNK):
        chunk = lines[i : i + CHUNK]
        tag = f"{name}{i // CHUNK}"
        if convert(chunk, tag):
            good.extend(chunk)
        else:
            good.extend(quarantine(chunk, tag))
    if not good:
        raise RuntimeError(f"no usable nodes in {total} lines")
    # chunks can each convert cleanly yet still clash when merged (renames,
    # duplicates, aggregate size); the pipeline converts the merged payload as
    # one file, so verify exactly that before publishing it.
    if not convert(good, f"{name}-all"):
        good = quarantine(good, f"{name}-all")
        if not good:
            raise RuntimeError("merged payload is still unparsable after quarantine")
    payload = base64.b64encode("\n".join(good).encode("utf8")).decode("ascii")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"{name}.txt"), "w", encoding="ascii") as f:
        f.write(payload)
    return total, len(good)


def main() -> None:
    summary, failed, ready = [], [], 0
    for name, url in SOURCES.items():
        try:
            total, kept = prepare(name, url)
            summary.append(f"{name}: {total} lines -> {kept} kept")
            ready += 1
        except Exception as exc:
            summary.append(f"{name}: FAILED {type(exc).__name__}: {exc}")
            failed.append(name)
            # a stale payload from an earlier run must never reach the pipeline
            stale = os.path.join(OUT_DIR, f"{name}.txt")
            if os.path.exists(stale):
                os.remove(stale)

    print("\n".join(summary))
    if ready == 0:
        print("::error::every upstream source failed, aborting instead of publishing an empty build")
        sys.exit(1)
    for name in failed:
        print(f"::warning::source '{name}' is unavailable, continuing with the remaining {ready} source(s)")


if __name__ == "__main__":
    main()
