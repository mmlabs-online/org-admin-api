#!/usr/bin/env python3
"""Compute the PR risk tier (low / medium / high) from changed paths and .github/risk-tiers.yml.

spaceM project-kit (CLAUDE.md §7). Standard library only, no YAML dependency: risk-tiers.yml is read as
`default: <tier>` plus `high:` / `low:` lists of quoted or bare globs. Globs use gitignore syntax:
`**` spans directories, `*` and `?` stay inside one segment, a pattern without `/` matches at any depth,
a trailing `/` matches everything below that directory. Precedence: high > low > default.
A PR's tier is the highest tier of its files; an empty change is `low`.

Usage:
  risk_tier.py --base origin/main           # files changed between merge-base(base, HEAD) and HEAD
  risk_tier.py --files a.ts db/migrations/1.sql
  risk_tier.py --base origin/main --explain # also print each file's tier and the matching pattern
  risk_tier.py ... --github-output          # append tier=<tier> to $GITHUB_OUTPUT
Exit code 0; prints the tier on the last line.
"""
import argparse
import os
import re
import subprocess
import sys

RANK = {"low": 0, "medium": 1, "high": 2}


def load_config(path):
    cfg = {"default": "medium", "high": [], "low": []}
    section = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = re.match(r"^(default|high|low|version|[A-Za-z_]+):\s*(\S*)", line)
            if m and not line.startswith(" "):
                key, val = m.group(1), m.group(2).strip("\"'")
                if key == "default" and val:
                    cfg["default"] = val
                section = key if key in ("high", "low") else None
                continue
            m = re.match(r"""^\s+-\s+(?:"([^"]*)"|'([^']*)'|([^\s#]+))""", line)
            if m and section:
                cfg[section].append(next(g for g in m.groups() if g is not None))
    if cfg["default"] not in RANK:
        sys.exit(f"risk_tier: default tier '{cfg['default']}' is not low/medium/high")
    return cfg


def glob_to_regex(pattern):
    anchored = "/" in pattern.rstrip("/")
    directory = pattern.endswith("/")
    p = pattern.strip("/")
    out, i = [], 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif p.startswith("**", i):
            out.append(".*"); i += 2
        elif p[i] == "*":
            out.append("[^/]*"); i += 1
        elif p[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(p[i])); i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    suffix = "/.*" if directory else "(?:/.*)?"
    return re.compile("^" + prefix + body + suffix + "$")


def tier_of(path, cfg, compiled):
    for tier in ("high", "low"):
        for pat, rx in compiled[tier]:
            if rx.match(path):
                return tier, pat
    return cfg["default"], "default"


def changed_files(base):
    mb = subprocess.run(["git", "merge-base", base, "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    out = subprocess.run(["git", "diff", "--name-only", mb, "HEAD"], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=".github/risk-tiers.yml")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--base")
    src.add_argument("--files", nargs="*")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    compiled = {t: [(p, glob_to_regex(p)) for p in cfg[t]] for t in ("high", "low")}
    files = changed_files(args.base) if args.base else list(args.files or [])
    tier = "low"
    for f in files:
        t, pat = tier_of(f, cfg, compiled)
        if args.explain:
            print(f"{t:<6} {f}  ({pat})")
        if RANK[t] > RANK[tier]:
            tier = t
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"tier={tier}\n")
    print(tier)


if __name__ == "__main__":
    main()
