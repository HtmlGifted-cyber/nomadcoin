// Nomad Coin block explorer.
// Reads the node's built-in read-only REST interface (nomadcoind -rest):
//   chaininfo.json, blockhashbyheight/<h>.json, block/<hash>.json, tx/<txid>.json
// The same paths exist in /data as a static snapshot, so the page works before
// the 24/7 node is online. To go live, set SOURCE to the node's HTTPS REST URL.
(function () {
  "use strict";

  var SOURCE = {
    base: "data",                         // live: "https://<node-domain>/rest"
    label: "Testnet demo · snapshot of the Nomad Coin testnet (October 9, 2026)",
    live: false
  };
  var HALVING = 2100000, REWARD = 50, TARGET_SPACING = 120;
  var view = document.getElementById("view");
  var cache = {};

  function get(path) {
    if (cache[path]) return Promise.resolve(cache[path]);
    return fetch(SOURCE.base + "/" + path, { cache: SOURCE.live ? "no-store" : "default" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { if (!SOURCE.live || path.indexOf("block/") === 0) cache[path] = j; return j; });
  }
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var short = function (h) { return h.slice(0, 10) + "…" + h.slice(-8); };
  var nmd = function (v) { return Number(v).toLocaleString(undefined, { maximumFractionDigits: 8 }) + " NMD"; };
  var when = function (t) { return new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }); };
  function ago(t) {
    var s = Math.max(0, Math.round(Date.now() / 1000 - t));
    if (s < 90) return s + " sec ago"; if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 172800) return Math.round(s / 3600) + " hours ago"; return Math.round(s / 86400) + " days ago";
  }
  function minedSupply(height) {
    var total = 0, h = 1;
    while (h <= height) {
      var era = Math.floor(h / HALVING), end = Math.min(height, (era + 1) * HALVING - 1);
      total += (end - h + 1) * REWARD / Math.pow(2, era); h = end + 1;
    }
    return total;
  }
  function hashrate(diff) {
    var hs = diff * 4294967296 / TARGET_SPACING;
    return hs >= 1e6 ? (hs / 1e6).toFixed(2) + " MH/s" : hs >= 1e3 ? (hs / 1e3).toFixed(1) + " kH/s" : hs.toFixed(0) + " H/s";
  }
  function coinbaseValue(b) {
    var tx = b.tx && b.tx[0]; if (!tx || typeof tx === "string") return null;
    return tx.vout.reduce(function (a, o) { return a + o.value; }, 0);
  }
  function link(kind, id, text) { return '<a href="#/' + kind + "/" + esc(id) + '">' + esc(text || id) + "</a>"; }
  function msg(text) { view.innerHTML = '<p class="notice">' + esc(text) + "</p>"; }

  // ---------------------------------------------------------------- home
  function home() {
    view.innerHTML = '<p class="muted">Loading the chain…</p>';
    get("chaininfo.json").then(function (ci) {
      var tip = ci.blocks;
      var stats = '<div class="stat-grid">' +
        stat(tip.toLocaleString(), "blocks") +
        stat(ci.difficulty.toPrecision(4), "difficulty") +
        stat(hashrate(ci.difficulty), "est. network hash rate") +
        stat(Math.round(minedSupply(tip)).toLocaleString(), "NMD mined so far") + "</div>";
      var heights = []; for (var h = tip; h >= 0 && h > tip - 15; h--) heights.push(h);
      return Promise.all(heights.map(function (h) {
        return get("blockhashbyheight/" + h + ".json").then(function (r) { return get("block/" + r.blockhash + ".json"); });
      })).then(function (blocks) {
        var rows = blocks.map(function (b) {
          var cb = coinbaseValue(b);
          return "<tr><td>" + link("block", b.hash, b.height.toLocaleString()) + '</td><td class="mono">' + link("block", b.hash, short(b.hash)) +
            "</td><td>" + ago(b.time) + '</td><td class="num">' + b.nTx + '</td><td class="num">' + (cb === null ? "-" : nmd(cb)) +
            '</td><td class="num">' + (b.size / 1000).toFixed(2) + " kB</td></tr>";
        }).join("");
        view.innerHTML = stats + '<h2 class="section-title" style="font-size:1.5rem">Latest blocks</h2>' +
          '<div class="table-wrap"><table><tr><th>Height</th><th>Hash</th><th>Mined</th><th class="num">Txs</th><th class="num">Reward + fees</th><th class="num">Size</th></tr>' +
          rows + "</table></div>" +
          '<p class="muted" style="margin-top:14px">Chain: ' + esc(ci.chain === "main" ? "main network" : ci.chain + "net") +
          " · best block " + link("block", ci.bestblockhash, short(ci.bestblockhash)) + "</p>";
      });
    }).catch(function () { msg("Can't reach the Nomad Coin node right now. Please try again in a minute."); });
  }
  function stat(v, label) { return '<div class="stat"><strong>' + esc(v) + "</strong><span>" + esc(label) + "</span></div>"; }

  // --------------------------------------------------------------- block
  function block(id) {
    var p = /^\d+$/.test(id) ? get("blockhashbyheight/" + id + ".json").then(function (r) { return r.blockhash; }) : Promise.resolve(id);
    p.then(function (hash) { return get("block/" + hash + ".json"); }).then(function (b) {
      var cb = coinbaseValue(b);
      var nav = (b.previousblockhash ? link("block", b.previousblockhash, "← Block " + (b.height - 1)) : "") +
        (b.nextblockhash ? " &nbsp;·&nbsp; " + link("block", b.nextblockhash, "Block " + (b.height + 1) + " →") : "");
      var txs = b.tx.map(function (t, i) {
        var out = t.vout.reduce(function (a, o) { return a + o.value; }, 0);
        return '<tr><td class="mono">' + link("tx", t.txid, short(t.txid)) + "</td><td>" + (i === 0 ? "Mining reward" : "Payment") +
          '</td><td class="num">' + nmd(out) + "</td></tr>";
      }).join("");
      view.innerHTML = '<h2 class="section-title" style="font-size:1.6rem">Block ' + b.height.toLocaleString() + "</h2>" +
        '<div class="table-wrap"><table>' +
        row("Hash", '<span class="mono">' + esc(b.hash) + "</span>") +
        row("Mined", when(b.time) + " (" + ago(b.time) + ")") +
        row("Confirmations", (b.confirmations || 0).toLocaleString()) +
        row("Transactions", b.nTx) + row("Reward + fees", cb === null ? "-" : nmd(cb)) +
        row("Difficulty", b.difficulty.toPrecision(6)) + row("Size", b.size.toLocaleString() + " bytes") +
        row("Merkle root", '<span class="mono">' + esc(b.merkleroot) + "</span>") +
        row("Nonce", b.nonce) + row("Bits", b.bits) +
        "</table></div><p style='margin:14px 0'>" + nav + "</p>" +
        '<h3>Transactions</h3><div class="table-wrap"><table><tr><th>Transaction</th><th>Type</th><th class="num">Amount</th></tr>' + txs + "</table></div>";
    }).catch(function () { msg("Block not found: " + id); });
  }
  function row(k, v) { return "<tr><th style='width:34%'>" + esc(k) + "</th><td>" + v + "</td></tr>"; }

  // ------------------------------------------------------------------ tx
  function tx(id) {
    get("tx/" + id + ".json").then(function (t) {
      var coinbase = t.vin.length && t.vin[0].coinbase;
      var ins = coinbase ? "<li>New coins (mining reward)</li>" : t.vin.map(function (v) {
        return "<li class='mono'>" + link("tx", v.txid, short(v.txid)) + " : " + v.vout + "</li>"; }).join("");
      var outs = t.vout.map(function (o) {
        var a = (o.scriptPubKey && (o.scriptPubKey.address || (o.scriptPubKey.addresses || [])[0])) || (o.scriptPubKey.type === "nulldata" ? "Data (OP_RETURN)" : o.scriptPubKey.type);
        return "<tr><td class='mono'>" + esc(a) + "</td><td class='num'>" + nmd(o.value) + "</td></tr>"; }).join("");
      view.innerHTML = '<h2 class="section-title" style="font-size:1.6rem">Transaction</h2>' +
        '<div class="table-wrap"><table>' + row("ID", '<span class="mono">' + esc(t.txid) + "</span>") +
        (t.blockhash ? row("In block", link("block", t.blockhash, short(t.blockhash))) : "") +
        (t.blocktime ? row("Time", when(t.blocktime)) : "") +
        row("Size", t.size + " bytes") + "</table></div>" +
        "<h3 style='margin-top:20px'>From</h3><ul>" + ins + "</ul>" +
        '<h3>To</h3><div class="table-wrap"><table><tr><th>Address</th><th class="num">Amount</th></tr>' + outs + "</table></div>";
    }).catch(function () { msg("Transaction not found: " + id); });
  }

  // -------------------------------------------------------------- routing
  function route() {
    var m = location.hash.match(/^#\/(block|tx)\/([0-9a-fA-F]+|\d+)$/);
    if (!m) return home();
    window.scrollTo(0, 0);
    return m[1] === "block" ? block(m[2]) : tx(m[2]);
  }
  document.getElementById("search").addEventListener("submit", function (e) {
    e.preventDefault();
    var q = document.getElementById("q").value.trim();
    if (/^\d+$/.test(q)) { location.hash = "#/block/" + q; return; }
    if (!/^[0-9a-fA-F]{64}$/.test(q)) { msg("Enter a block height, or a 64-character block hash or transaction ID."); return; }
    get("block/" + q + ".json").then(function () { location.hash = "#/block/" + q; })
      .catch(function () { location.hash = "#/tx/" + q; });
  });
  document.getElementById("source-badge").textContent = SOURCE.label;
  window.addEventListener("hashchange", route);
  route();
  if (SOURCE.live) setInterval(function () { if (!location.hash || location.hash === "#/") home(); }, 60000);
})();
