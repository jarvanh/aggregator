"""Resolve the add/add conflicts produced by merging wzdnzd/aggregator:main
into this fork, keeping upstream's implementation and re-applying the fork's
own patches on top.

Usage: resolve_conflicts.py <repo-root>
"""

import os
import re
import subprocess
import sys

RESERVED = (
    '    reserved = {"direct", "reject", "reject-drop", "pass", "global", '
    '"compatible", "automatic", "🌐 proxy"}'
)
GUARD_HEAD = "\n".join(
    [
        "    # [fork-patch] 与内置策略或本配置代理分组同名的节点会导致 mihomo 拒绝加载整个配置",
        RESERVED,
        "",
    ]
)
GUARD_BODY = "\n".join(
    [
        '        if str(item.get("name", "")).strip().lower() in reserved:',
        '            item["name"] = f"proxy-{str(item.get(\'name\', \'node\')).strip()}"',
    ]
)
LOOP_HEAD = "    for item in proxies:"
LOOP_BODY_ANCHOR = "        if not proxy_exists(item, hosts):"

# (file, upstream-side marker) pairs that must be resolved in the fork's favour
# because the whole file is maintained by the fork.
FORK_WINS = (".github/workflows/process.yaml",)

def run(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True)


def split_conflicts(lines):
    """Split a conflicted file into an ordered list of ('text'|'conflict', payload)."""
    parts = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("<<<<<<<"):
            if line.startswith("=======") or line.startswith(">>>>>>>"):
                raise SystemExit(f"unbalanced conflict marker: {line!r}")
            parts.append(("text", [line]))
            i += 1
            continue

        i += 1
        ours = []
        while not lines[i].startswith("======="):
            ours.append(lines[i])
            i += 1
        i += 1
        theirs = []
        while not lines[i].startswith(">>>>>>>"):
            theirs.append(lines[i])
            i += 1
        i += 1
        parts.append(("conflict", (ours, theirs)))
    return parts


def take_side(text, keep="ours"):
    """Collapse every conflict hunk in <text> down to one side."""
    index = 0 if keep == "ours" else 1
    out = []
    for kind, payload in split_conflicts(text.split("\n")):
        out.extend(payload if kind == "text" else payload[index])
    return "\n".join(out)


def take_ours(text):
    return take_side(text, "ours")


def take_theirs(text):
    return take_side(text, "theirs")


def strip_existing_guard(text):
    """Remove any partial reserved-name guard left behind by a merge.

    The guard is three pieces: the [fork-patch] comment, the `reserved = ...`
    set and the `if` that uses it. A merge can keep any subset of them, so drop
    all three and let resolve_clash() rebuild the block in one piece.
    """
    lines = text.split("\n")
    kept = []
    skip_reserved = False
    for line in lines:
        stripped = line.strip()
        if "[fork-patch]" in stripped:
            skip_reserved = True
            continue
        if skip_reserved and stripped == "":
            skip_reserved = False
            continue
        if skip_reserved and stripped.startswith("reserved = {"):
            skip_reserved = False
            continue
        skip_reserved = False
        if stripped.startswith("if str(item.get(") and "reserved:" in stripped:
            continue
        if stripped.startswith('item["name"] = f"proxy-{str(item.get('):
            continue
        kept.append(line)
    return "\n".join(kept)


def resolve_clash(text):
    """Keep upstream's clash.py and re-apply the reserved-name guard."""
    # Upstream always wins: it owns clash.py, and every fork hunk here is either
    # fork-local code that upstream has moved into outbound/ or the reserved-name
    # guard, which is rebuilt below.
    #
    # A merge can leave the fork's comment (and even the `reserved = ...` line)
    # behind while dropping the `if` that consumes it, so never trust the marker:
    # always rebuild the whole guard from scratch.
    text = take_theirs(text)
    text = strip_existing_guard(text)

    marker = LOOP_HEAD + "\n" + LOOP_BODY_ANCHOR
    if marker not in text:
        raise SystemExit("clash.py: reserved-name guard anchor not found")

    guarded_loop = "\n".join([LOOP_HEAD, GUARD_BODY, LOOP_BODY_ANCHOR])
    patched = text.replace(marker, GUARD_HEAD + guarded_loop, 1)
    # collapse the blank line left behind when the guard was stripped out
    return re.sub(r"\n{3,}(\s*# \[fork-patch\])", r"\n\n\1", patched)


def resolve_collect(text):
    """Take the fork version, then re-assert the fork's cron and command."""
    text = take_ours(text)
    text = re.sub(r'^(\s*cron:\s*)"[^"]*"', r'\1"10 * * * *"', text, count=1, flags=re.M)
    text = text.replace(
        "subscribe/collect.py --all --overwrite --skip",
        "subscribe/collect.py --all --overwrite",
    )
    if '"10 * * * *"' not in text:
        raise SystemExit("collect.yaml: cron patch not applied")
    if "--overwrite --skip" in text:
        raise SystemExit("collect.yaml: collect command patch not applied")
    return text


def resolve_crawl(text):
    """singlelink:// aggregation keys are exempt from token dedup."""
    text = take_theirs(text)
    anchor = (
        "        # dedup by token\n"
        "        tokens = {utils.parse_token(k): k for k in records.keys()}\n"
        "        tasks = {k: records[k] for k in tokens.values()}"
    )
    patch = (
        "        # [fork-patch] # dedup by token, but never drop single-link aggregations: their keys are\n"
        "        # not urls and all parse to the same empty token, so they would overwrite\n"
        "        # each other here (e.g. telegram's links being replaced by github's)\n"
        "        tasks = {k: records[k] for k in records.keys() if k.startswith(SINGLE_LINK_FLAG)}\n"
        "        tokens = {\n"
        "            utils.parse_token(k): k\n"
        "            for k in records.keys()\n"
        "            if not k.startswith(SINGLE_LINK_FLAG)\n"
        "        }\n"
        "        tasks.update({k: records[k] for k in tokens.values()})"
    )
    if "[fork-patch]" in text:
        return text
    if anchor not in text:
        raise SystemExit("crawl.py: token dedup anchor not found")
    return text.replace(anchor, patch, 1)


def resolve_import_source(text):
    """Helpers such as QuotedStr/quoted_scalar now live in outbound/.

    clash.py re-exports them, so both sides work, but the outbound/ import is the
    canonical one. Upstream may still be on the clash.py form, in which case the
    fork's import is kept and no change is required at all.
    """
    return take_ours(text)


RESOLVERS = {
    "subscribe/clash.py": resolve_clash,
    "subscribe/crawl.py": resolve_crawl,
    ".github/workflows/collect.yaml": resolve_collect,
    "subscribe/scripts/v2rayse.py": resolve_import_source,
}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."

    conflicted = run("git", "diff", "--name-only", "--diff-filter=U", cwd=root).stdout.split()
    if not conflicted:
        print("no conflicts to resolve")
        return

    for path in conflicted:
        if path in FORK_WINS:
            run("git", "checkout", "HEAD", "--", path, cwd=root)
            run("git", "add", "--", path, cwd=root)
            print(f"{path}: kept fork version")
            continue

        resolver = RESOLVERS.get(path)
        full = os.path.join(root, path)
        text = open(full, encoding="utf8").read()

        if resolver is not None:
            run("git", "checkout", "-m", "--", path, cwd=root)
            text = open(full, encoding="utf8").read()
            text = resolver(text)
        elif "<<<<<<<" in text:
            # default: take upstream's side
            run("git", "checkout", "--theirs", "--", path, cwd=root)
            text = open(full, encoding="utf8").read()

        if "<<<<<<<" in text or ">>>>>>>" in text:
            raise SystemExit(f"{path}: conflict markers remain")

        open(full, "w", encoding="utf8").write(text)
        run("git", "add", "--", path, cwd=root)
        print(f"{path}: resolved" + (" with fork patches" if resolver else " with upstream"))

    remaining = run("git", "diff", "--name-only", "--diff-filter=U", cwd=root).stdout.split()
    if remaining:
        raise SystemExit(f"still unmerged: {remaining}")

    hits = run("git", "grep", "-nI", "-e", "^<<<<<<< ", "--", ".", cwd=root, check=False)
    if hits.returncode == 0:
        raise SystemExit(f"conflict markers found in tree:\n{hits.stdout}")

    print("all conflicts resolved")


if __name__ == "__main__":
    main()
