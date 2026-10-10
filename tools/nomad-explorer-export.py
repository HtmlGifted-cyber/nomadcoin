#!/usr/bin/env python3
"""Nomad Coin explorer exporter for an always-on node (blockchain data only).

Reads the local node over RPC (cookie auth) and writes REST-shaped JSON for the
static explorer into <site>/data/: chaininfo.json, blockhashbyheight/<h>.json,
block/<hash>.json and tx/<txid>.json. It publishes public blockchain data only -
no IP addresses or other network details. The seed list (seeds.json) is edited
by hand by the node owner.

Settings (environment variables):
  NMD_SITE      path of the site repo clone       (default ~/nomadcoin-site)
  NMD_DATADIR   node data folder                  (default ~/.nomadcoin)
  NMD_NETWORK   main | test                       (default main)
  NMD_PUSH      1 = commit and push changes       (default 0)
"""
import base64
import json
import os
import subprocess
import urllib.request

SITE = os.path.expanduser(os.environ.get("NMD_SITE", "~/nomadcoin-site"))
DATADIR = os.path.expanduser(os.environ.get("NMD_DATADIR", "~/.nomadcoin"))
NET = os.environ.get("NMD_NETWORK", "main")
RPC_PORT, SUB = {"main": (17772, ""), "test": (27772, "testnet3")}[NET]
PUSH = os.environ.get("NMD_PUSH", "0") == "1"
OUT = os.path.join(SITE, "data")


def rpc(method, *params):
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


def export():
    info = rpc("getblockchaininfo")
    write("chaininfo.json", info)
    tip = info["blocks"]
    # First run writes everything; later runs refresh the last 120 blocks (confirmations change).
    start = 0 if not os.path.exists(os.path.join(OUT, "blockhashbyheight", "0.json")) else max(0, tip - 120)
    for h in range(start, tip + 1):
        bh = rpc("getblockhash", h)
        write(f"blockhashbyheight/{h}.json", {"blockhash": bh})
        block = rpc("getblock", bh, 2)
        write(f"block/{bh}.json", block)
        for tx in block["tx"]:
            write(f"tx/{tx['txid']}.json",
                  dict(tx, blockhash=bh, confirmations=block["confirmations"], time=block["time"], blocktime=block["time"]))
    return tip


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
    print(f"exported up to block {tip}", push(tip) if PUSH else "(not pushed)")
