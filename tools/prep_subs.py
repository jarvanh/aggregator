#!/usr/bin/env python3
"""Prepare raw public node lists for the aggregator pipeline.

Some public list files contain malformed share links (e.g. raw '=' in a trojan
password) that make subconverter reject the WHOLE file. This script downloads
each list, sanitizes known-bad patterns, converts it in chunks, and quarantines
any chunk that fails by recursively bisecting it, so a single poison line only
costs its own block. Surviving lines are re-packed as a base64 link list that
subconverter imports cleanly.
"""

import base64
import os
import subprocess
import sys
import urllib.request
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SUB_DIR = os.path.join(ROOT, "subconverter")
BIN = os.path.join(SUB_DIR, "subconverter-linux-amd")
OUT_DIR = os.environ.get("PREP_OUT_DIR", "/tmp/subs")
CHUNK = 1000
MIN_SLIVER = 30

SOURCES = {
    "barryfar": "https://raw.githubusercontent.com/barry-far/V2ray-Config/main/All_Configs_Sub.txt",
    "epodonios": "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_Sub.txt",
    "mahdibland": "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge.txt",
}

PROTOCOLS = {"vmess", "vless", "trojan", "ss", "ssr", "hysteria", "hysteria2", "hy2", "tuic", "anytls", "socks5"}
ENCODE_USERINFO = {"trojan", "vless", "hysteria", "hysteria2", "hy2", "tuic", "anytls", "socks5"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "clash.meta; mihomo"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf8", "ignore")


def sanitize(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        proto, sep, rest = line.partition("://")
        if not sep or proto.lower() not in PROTOCOLS:
            continue
        proto = proto.lower()
        if proto in ENCODE_USERINFO:
            userinfo, at, tail = rest.partition("@")
            if at and ("=" in userinfo or " " in userinfo):
                userinfo = urllib.parse.quote(userinfo, safe="")
                line = f"{proto}://{userinfo}@{tail}"
        lines.append(line)
    return lines


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
    ok = proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 50
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
    payload = base64.b64encode("\n".join(good).encode("utf8")).decode("ascii")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"{name}.txt"), "w", encoding="ascii") as f:
        f.write(payload)
    return total, len(good)


def main() -> None:
    summary = []
    for name, url in SOURCES.items():
        try:
            total, kept = prepare(name, url)
            summary.append(f"{name}: {total} lines -> {kept} kept")
        except Exception as exc:
            summary.append(f"{name}: FAILED {type(exc).__name__}: {exc}")
    print("\n".join(summary))
    if all("FAILED" not in line for line in summary):
        return
    sys.exit(1)


if __name__ == "__main__":
    main()
