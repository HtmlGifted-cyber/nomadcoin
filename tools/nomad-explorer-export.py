#!/usr/bin/env python3
"""Nomad Coin explorer exporter for an always-on node (blockchain data only).

Reads the local node over RPC (cookie auth) and writes REST-shaped JSON for the
static explorer into <site>/data/: chaininfo.json, blockhashbyheight/<h>.json,
block/<hash>.json and tx/<txid>.json. It publishes public blockchain data only -
no IP addresses or other network details. The seed list (seeds.json) is edited
by hand by the node owner.

After a rollback (tip went down, or a block below the tip was replaced) the exporter re-exports from the
lowest changed height and then removes its own files that are no longer on the main chain.

Settings (environment variables):
  NMD_SITE      path of the site repo clone       (default ~/nomadcoin-site)
  NMD_DATADIR   node data folder                  (default ~/.nomadcoin)
  NMD_NETWORK   main | test                       (default main)
  NMD_PUSH      1 = commit and push changes       (default 0)
  NOMAD_RPC_MODE  local = read the laptop node over RPC (default)
                  ssh   = run nomadcoin-cli on the always-on hub (Termux phone)
                          over ssh; no RPC port or credentials leave the hub
  NMD_HUB       ssh target for mode ssh (user@host or host; required in ssh mode)
  NMD_HUB_PORT  ssh port for mode ssh             (default 22)
  NMD_HUB_KEY   ssh identity file for mode ssh    (default: ssh's own default)
"""
import base64
import json
import os
import shlex
import subprocess
import urllib.request

SITE = os.path.expanduser(os.environ.get("NMD_SITE", "~/nomadcoin-site"))
DATADIR = os.path.expanduser(os.environ.get("NMD_DATADIR", "~/.nomadcoin"))
NET = os.environ.get("NMD_NETWORK", "main")
RPC_PORT, SUB = {"main": (17772, ""), "test": (27772, "testnet3")}[NET]
PUSH = os.environ.get("NMD_PUSH", "0") == "1"
MODE = os.environ.get("NOMAD_RPC_MODE", "local")
HUB = os.environ.get("NMD_HUB", "")
HUB_PORT = os.environ.get("NMD_HUB_PORT", "22")
HUB_KEY = os.environ.get("NMD_HUB_KEY", "")
OUT = os.path.join(SITE, "data")
if MODE not in ("local", "ssh"):
    raise SystemExit(f"NOMAD_RPC_MODE must be 'local' or 'ssh', not {MODE!r}")
if MODE == "ssh" and not HUB:
    raise SystemExit("NOMAD_RPC_MODE=ssh needs NMD_HUB (ssh target of the always-on node)")


def hub(remote_cmd, script=None):
    """Run a command on the hub's shell over ssh. Returns stdout, raises on failure."""
    cmd = ["ssh", "-p", HUB_PORT, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if HUB_KEY:
        cmd += ["-i", os.path.expanduser(HUB_KEY)]
    cmd += [HUB, remote_cmd]
    try:
        r = subprocess.run(cmd, input=script if script is not None else "", capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ssh to hub {HUB} timed out after 60s")
    if r.returncode != 0:
        raise RuntimeError(f"ssh to hub {HUB} failed (exit {r.returncode}): {r.stderr.strip()[:300] or r.stdout.strip()[:300]}")
    return r.stdout


def rpc(method, *params):
    if MODE == "ssh":
        args = " ".join(shlex.quote(str(x)) for x in (method, *params))
        out = hub("nomadcoin-cli " + args).strip()
        # nomadcoin-cli prints objects/arrays as JSON and plain scalars (hashes, numbers) raw
        return json.loads(out) if out[:1] in ("{", "[") else out
    with open(os.path.join(DATADIR, SUB, ".cookie")) as f:
        auth = base64.b64encode(f.read().strip().encode()).decode()
    body = json.dumps({"jsonrpc": "1.0", "id": "x", "method": method, "params": list(params)}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{RPC_PORT}/", body,
                                 {"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def write(path, obj):
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    text = json.dumps(obj, separators=(",", ":"))
    try:
        with open(full) as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(full, "w") as f:
        f.write(text)


def start_height(tip):
    d = os.path.join(OUT, "blockhashbyheight")
    have = [int(f[:-5]) for f in os.listdir(d) if f.endswith(".json") and f[:-5].isdigit()] if os.path.isdir(d) else []
    # First run writes everything; later runs refresh the last 120 blocks (confirmations change)
    # and also fill any gap between the last exported height and the tip (e.g. after the laptop was off).
    if not have:
        return 0
    return max(0, min(tip - 120, max(have) + 1))


def hub_blocks(start, tip):
    """ssh mode: one ssh session per chunk of 50 heights; the hub's shell runs nomadcoin-cli for each height."""
    for lo in range(start, tip + 1, 50):
        hi = min(tip, lo + 49)
        script = (f"h={lo}\n"
                  f"while [ $h -le {hi} ]; do\n"
                  "  bh=$(nomadcoin-cli getblockhash $h) || exit 1\n"
                  "  js=$(nomadcoin-cli getblock $bh 2) || exit 1\n"
                  "  printf '%s\\t%s\\n' \"$bh\" \"$(printf '%s' \"$js\" | tr -d '\\n')\"\n"
                  "  h=$((h+1))\n"
                  "done\n")
        lines = [ln for ln in hub("sh -s", script=script).splitlines() if ln.strip()]
        if len(lines) != hi - lo + 1:
            raise RuntimeError(f"hub returned {len(lines)} blocks for {lo}-{hi}, expected {hi - lo + 1}")
        for i, ln in enumerate(lines):
            bh, js = ln.split("\t", 1)
            yield lo + i, bh, json.loads(js)


def blocks(start, tip):
    if MODE == "ssh":
        yield from hub_blocks(start, tip)
        return
    for h in range(start, tip + 1):
        bh = rpc("getblockhash", h)
        yield h, bh, rpc("getblock", bh, 2)


def stored_hashes():
    """{height: blockhash} from the blockhashbyheight files this exporter wrote."""
    d = os.path.join(OUT, "blockhashbyheight")
    out = {}
    for f in (os.listdir(d) if os.path.isdir(d) else []):
        if f.endswith(".json") and f[:-5].isdigit():
            with open(os.path.join(d, f)) as fh:
                out[int(f[:-5])] = json.load(fh)["blockhash"]
    return out


def node_hashes(heights):
    """The node's current main-chain hash for each height. ssh mode: one ssh session per 500 heights."""
    if MODE != "ssh":
        return {h: rpc("getblockhash", h) for h in heights}
    out = {}
    for i in range(0, len(heights), 500):
        chunk = heights[i:i + 500]
        script = "".join(f"nomadcoin-cli getblockhash {h} || exit 1\n" for h in chunk)
        lines = [ln.strip() for ln in hub("sh -s", script=script).splitlines() if ln.strip()]
        if len(lines) != len(chunk):
            raise RuntimeError(f"hub returned {len(lines)} hashes for {len(chunk)} heights")
        out.update(zip(chunk, lines))
    return out


def export():
    info = rpc("getblockchaininfo")
    tip = info["blocks"]
    # Reorg below the tip: a stored height whose hash no longer matches the node is re-exported,
    # starting from the lowest such height.
    stored = stored_hashes()
    check = sorted(h for h in stored if h <= tip)
    now = node_hashes(check)
    bad = [h for h in check if stored[h] != now[h]]
    start = start_height(tip)
    if bad:
        start = min(start, bad[0])
        print(f"reorg: {len(bad)} stored height(s) changed, lowest {bad[0]}; re-exporting from {start}")
    write("chaininfo.json", info)
    for h, bh, block in blocks(start, tip):
        write(f"blockhashbyheight/{h}.json", {"blockhash": bh})
        write(f"block/{bh}.json", block)
        for tx in block["tx"]:
            write(f"tx/{tx['txid']}.json",
                  dict(tx, blockhash=bh, confirmations=block["confirmations"], time=block["time"], blocktime=block["time"]))
    return tip


def drop(rel, removed):
    os.remove(os.path.join(OUT, rel))
    removed.append(rel)


def prune(tip):
    """Delete this exporter's files that are no longer on the main chain: blockhashbyheight files above the
    tip, block/<hash>.json for blocks above the tip or whose hash differs from the stored hash at their
    height, and tx/<txid>.json pointing at one of those blocks. Returns the removed paths."""
    removed = []
    main = {}
    for h, bh in stored_hashes().items():
        if h > tip:
            drop(f"blockhashbyheight/{h}.json", removed)
        else:
            main[h] = bh
    orphan = set()
    bdir = os.path.join(OUT, "block")
    for f in (os.listdir(bdir) if os.path.isdir(bdir) else []):
        if not f.endswith(".json"):
            continue
        bh = f[:-5]
        with open(os.path.join(bdir, f)) as fh:
            h = json.load(fh).get("height")
        if h is None or (h <= tip and main.get(h, bh) == bh):
            continue
        orphan.add(bh)
        drop(f"block/{f}", removed)
    if orphan:
        tdir = os.path.join(OUT, "tx")
        for f in (os.listdir(tdir) if os.path.isdir(tdir) else []):
            if not f.endswith(".json"):
                continue
            with open(os.path.join(tdir, f)) as fh:
                if json.load(fh).get("blockhash") in orphan:
                    drop(f"tx/{f}", removed)
    return removed


def push(tip):
    git = ["git", "-C", SITE]
    subprocess.run(git + ["add", "data"], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet", "--", "data"]).returncode == 0:
        return "no changes"
    subprocess.run(git + ["commit", "-q", "-m", f"Explorer update: block {tip}"], check=True)
    subprocess.run(git + ["pull", "-q", "--rebase"], check=True)
    subprocess.run(git + ["push", "-q"], check=True)
    return "pushed"


if __name__ == "__main__":
    tip = export()
    removed = prune(tip)
    for rel in removed:
        print(f"removed {rel}")
    print(f"removed {len(removed)} stale file(s)")
    print(f"exported up to block {tip}", push(tip) if PUSH else "(not pushed)")
