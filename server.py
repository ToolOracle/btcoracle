#!/usr/bin/env python3
"""
btcOracle MCP Server v1.0.0 — Port 12101
Bitcoin Intelligence for AI Agents.

10 Tools:
  btc_overview        — Price, market cap, dominance, 24h stats
  btc_mempool         — Mempool depth, fee recommendations, congestion
  btc_fees            — Real-time fee tracker with USD cost estimates
  btc_block_stats     — Latest block, hashrate, difficulty, halving countdown
  btc_address_check   — Address balance, TX count, received/spent
  btc_tx_lookup       — TX status, confirmations, inputs/outputs, fee
  btc_network_stats   — Node count, hashrate trend, difficulty adjustment
  btc_lightning_stats — Lightning Network capacity, nodes, channels
  btc_inscription     — Ordinals inscription lookup, BRC-20 tokens
  btc_whale_alert     — Large transactions > threshold BTC in recent blocks

APIs: mempool.space (fees, mempool, blocks, lightning)
      Blockstream Esplora (addresses, transactions, UTXOs)
      CoinGecko (price, market data)
      Hiro API (Ordinals/Inscriptions)
No API keys required.
"""
import os, sys, json, logging, aiohttp, asyncio
from datetime import datetime, timezone

sys.path.insert(0, "/root/whitelabel")
from shared.utils.mcp_base import WhitelabelMCPServer

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [btcOracle] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/root/whitelabel/logs/btcoracle.log", mode="a"),
    ])
logger = logging.getLogger("btcOracle")

PRODUCT_NAME = "btcOracle"
VERSION      = "1.0.0"
PORT_MCP     = 12101
PORT_HEALTH  = 12102

# APIs — all public, no key required
MEMPOOL  = "https://mempool.space/api"
ESPLORA  = "https://blockstream.info/api"
CG       = "https://api.coingecko.com/api/v3"
HIRO     = "https://api.hiro.so"
HEADERS  = {"User-Agent": "btcOracle-ToolOracle/1.0", "Accept": "application/json"}

# Bitcoin constants
SATOSHI       = 1e8
HALVING_BLOCK = 840000  # Last halving April 2024
HALVING_INTERVAL = 210000
BLOCK_TIME_MIN   = 10  # ~10 min per block

def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

async def get(url, params=None, timeout=15):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, headers=HEADERS,
                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200:
                    ct = r.content_type or ""
                    if "json" in ct:
                        return await r.json(content_type=None)
                    return await r.text()
                return {"error": f"HTTP {r.status}"}
    except Exception as e:
        return {"error": str(e)[:80]}

def sats_to_btc(sats):
    try:
        return round(int(sats) / SATOSHI, 8)
    except:
        return None

def next_halving(current_block):
    halvings_done = current_block // HALVING_INTERVAL
    next_halving_block = (halvings_done + 1) * HALVING_INTERVAL
    blocks_remaining = next_halving_block - current_block
    days_remaining = round(blocks_remaining * BLOCK_TIME_MIN / 60 / 24, 1)
    current_reward = 3.125  # Post-April 2024 halving
    return {
        "next_halving_block": next_halving_block,
        "blocks_remaining": blocks_remaining,
        "estimated_days": days_remaining,
        "current_block_reward_btc": current_reward,
        "next_block_reward_btc": current_reward / 2,
        "halvings_completed": halvings_done,
    }

# ── Tool Handlers ─────────────────────────────────────────────────────────────

async def handle_overview(args):
    """Bitcoin ecosystem overview: price, market cap, dominance, supply stats."""
    price_data, global_data = await asyncio.gather(
        get(f"{CG}/simple/price", {
            "ids": "bitcoin", "vs_currencies": "usd,eur",
            "include_24hr_change": "true",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_7d_change": "true",
        }),
        get(f"{CG}/global"),
    )
    btc = price_data.get("bitcoin", {}) if isinstance(price_data, dict) else {}
    gdata = global_data.get("data", {}) if isinstance(global_data, dict) else {}

    block_height = await get(f"{MEMPOOL}/blocks/tip/height")
    height = int(block_height) if isinstance(block_height, (int, str)) and str(block_height).isdigit() else 0

    halving = next_halving(height) if height else {}

    return {
        "asset": "Bitcoin",
        "ticker": "BTC",
        "timestamp": ts(),
        "price": {
            "usd": btc.get("usd"),
            "eur": btc.get("eur"),
            "change_24h_pct": round(btc.get("usd_24h_change", 0), 2),
            "change_7d_pct": round(btc.get("usd_7d_change", 0), 2) if btc.get("usd_7d_change") else None,
            "market_cap_usd": btc.get("usd_market_cap"),
            "volume_24h_usd": btc.get("usd_24h_vol"),
        },
        "dominance": {
            "btc_dominance_pct": round(gdata.get("market_cap_percentage", {}).get("btc", 0), 2),
            "total_market_cap_usd": gdata.get("total_market_cap", {}).get("usd"),
        },
        "network": {
            "block_height": height,
            "halving": halving,
        },
        "supply": {
            "max_supply": 21_000_000,
            "circulating_approx": 19_700_000,
            "remaining_to_mine": 1_300_000,
        },
        "source": "CoinGecko + mempool.space",
    }


async def handle_mempool(args):
    """Mempool depth, fee recommendations, congestion level, pending TX count."""
    fees, mempool_stats = await asyncio.gather(
        get(f"{MEMPOOL}/v1/fees/recommended"),
        get(f"{MEMPOOL}/mempool"),
    )
    if isinstance(fees, dict) and "error" not in fees:
        fastest   = fees.get("fastestFee", 0)
        half_hour = fees.get("halfHourFee", 0)
        hour_fee  = fees.get("hourFee", 0)
        economy   = fees.get("economyFee", 0)
        minimum   = fees.get("minimumFee", 1)
    else:
        fastest = half_hour = hour_fee = economy = minimum = None

    stats = mempool_stats if isinstance(mempool_stats, dict) else {}
    tx_count   = stats.get("count", 0)
    vsize_mb   = round(stats.get("vsize", 0) / 1e6, 2)
    total_fee  = stats.get("total_fee", 0)

    # Congestion level
    congestion = (
        "severe"   if fastest and fastest > 50 else
        "high"     if fastest and fastest > 20 else
        "moderate" if fastest and fastest > 5  else
        "low"
    )

    return {
        "timestamp": ts(),
        "congestion": congestion,
        "pending_transactions": tx_count,
        "mempool_size_mb": vsize_mb,
        "fee_recommendations_sat_vb": {
            "fastest_10min": fastest,
            "half_hour_30min": half_hour,
            "hour_60min": hour_fee,
            "economy_several_hours": economy,
            "minimum": minimum,
        },
        "total_fees_btc": round(total_fee / SATOSHI, 6) if total_fee else None,
        "source": "mempool.space",
    }


async def handle_fees(args):
    """Real-time fee tracker with USD cost estimates for common TX types."""
    fees_data, price_data = await asyncio.gather(
        get(f"{MEMPOOL}/v1/fees/recommended"),
        get(f"{CG}/simple/price", {"ids": "bitcoin", "vs_currencies": "usd"}),
    )
    btc_usd = price_data.get("bitcoin", {}).get("usd", 0) if isinstance(price_data, dict) else 0

    if isinstance(fees_data, dict) and "error" not in fees_data:
        fastest   = fees_data.get("fastestFee", 1)
        half_hour = fees_data.get("halfHourFee", 1)
        hour_fee  = fees_data.get("hourFee", 1)
        economy   = fees_data.get("economyFee", 1)
    else:
        fastest = half_hour = hour_fee = economy = 1

    # Common TX sizes in vBytes
    TX_SIZES = {
        "simple_p2wpkh": 141,       # 1 input, 1 output (SegWit)
        "p2pkh_legacy": 225,        # Legacy transaction
        "p2wpkh_2in_2out": 208,     # 2 inputs, 2 outputs
        "consolidation_5in": 600,   # Consolidation TX
    }

    def fee_usd(sat_vb, vbytes):
        sats = sat_vb * vbytes
        return round(sats / SATOSHI * btc_usd, 4)

    def fee_sats(sat_vb, vbytes):
        return sat_vb * vbytes

    estimates = {}
    for tx_type, vbytes in TX_SIZES.items():
        estimates[tx_type] = {
            "vbytes": vbytes,
            "fastest": {"sats": fee_sats(fastest, vbytes),
                        "usd": fee_usd(fastest, vbytes)},
            "half_hour": {"sats": fee_sats(half_hour, vbytes),
                          "usd": fee_usd(half_hour, vbytes)},
            "economy": {"sats": fee_sats(economy, vbytes),
                        "usd": fee_usd(economy, vbytes)},
        }

    return {
        "timestamp": ts(),
        "btc_price_usd": btc_usd,
        "fee_rates_sat_vb": {
            "fastest": fastest,
            "half_hour": half_hour,
            "hour": hour_fee,
            "economy": economy,
        },
        "cost_estimates": estimates,
        "note": "sat/vB = satoshis per virtual byte. SegWit txs are cheaper than legacy.",
        "source": "mempool.space + CoinGecko",
    }


async def handle_block_stats(args):
    """Latest block info, hashrate, difficulty, halving countdown."""
    tip_hash, tip_height, hashrate_data, difficulty_adj = await asyncio.gather(
        get(f"{MEMPOOL}/blocks/tip/hash"),
        get(f"{MEMPOOL}/blocks/tip/height"),
        get(f"{MEMPOOL}/v1/mining/hashrate/3d"),
        get(f"{MEMPOOL}/v1/difficulty-adjustment"),
    )

    height = int(tip_height) if isinstance(tip_height, (int,str)) and str(tip_height).isdigit() else 0

    # Latest block details
    latest_block = {}
    if tip_hash and isinstance(tip_hash, str) and len(tip_hash) == 64:
        block_data = await get(f"{MEMPOOL}/block/{tip_hash}")
        if isinstance(block_data, dict):
            latest_block = {
                "height": block_data.get("height"),
                "hash": tip_hash[:16] + "...",
                "timestamp": datetime.fromtimestamp(
                    block_data.get("timestamp", 0), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tx_count": block_data.get("tx_count"),
                "size_bytes": block_data.get("size"),
                "weight": block_data.get("weight"),
                "total_fees_btc": round(block_data.get("extras", {}).get("totalFees", 0) / SATOSHI, 6),
                "miner": block_data.get("extras", {}).get("pool", {}).get("name", "Unknown"),
            }

    # Hashrate (last value)
    hr = hashrate_data if isinstance(hashrate_data, dict) else {}
    current_hashrate = None
    if hr.get("hashrates"):
        current_hashrate = hr["hashrates"][-1].get("avgHashrate")

    # Difficulty adjustment
    da = difficulty_adj if isinstance(difficulty_adj, dict) else {}

    halving = next_halving(height)

    return {
        "timestamp": ts(),
        "block_height": height,
        "latest_block": latest_block,
        "hashrate": {
            "current_eh_s": round(current_hashrate / 1e18, 2) if current_hashrate else None,
            "unit": "EH/s (ExaHash per second)",
        },
        "difficulty_adjustment": {
            "progress_pct": round(da.get("progressPercent", 0), 1),
            "estimated_change_pct": round(da.get("difficultyChange", 0), 2),
            "remaining_blocks": da.get("remainingBlocks"),
            "estimated_retarget_date": da.get("estimatedRetargetDate"),
        },
        "halving": halving,
        "source": "mempool.space",
    }


async def handle_address_check(args):
    """Bitcoin address intelligence: balance, TX count, received/sent."""
    address = args.get("address", "").strip()
    if not address:
        return {"error": "address required (Bitcoin address: 1..., 3..., bc1...)"}

    addr_data, utxos = await asyncio.gather(
        get(f"{ESPLORA}/address/{address}"),
        get(f"{ESPLORA}/address/{address}/utxo"),
    )

    if isinstance(addr_data, dict) and "error" not in addr_data:
        chain = addr_data.get("chain_stats", {})
        mempool_s = addr_data.get("mempool_stats", {})

        funded_sats    = chain.get("funded_txo_sum", 0)
        spent_sats     = chain.get("spent_txo_sum", 0)
        balance_sats   = funded_sats - spent_sats
        tx_count       = chain.get("tx_count", 0)
        utxo_count     = len(utxos) if isinstance(utxos, list) else 0

        # Type detection
        addr_type = (
            "P2WPKH (SegWit native)" if address.startswith("bc1q") else
            "P2WSH (SegWit script)"  if address.startswith("bc1p") else
            "P2SH (SegWit wrapped)"  if address.startswith("3")   else
            "P2PKH (Legacy)"         if address.startswith("1")   else
            "Unknown"
        )

        # Pending (mempool)
        pending_in  = sats_to_btc(mempool_s.get("funded_txo_sum", 0))
        pending_out = sats_to_btc(mempool_s.get("spent_txo_sum", 0))

        return {
            "address": address,
            "address_type": addr_type,
            "balance_btc": sats_to_btc(balance_sats),
            "balance_sats": balance_sats,
            "total_received_btc": sats_to_btc(funded_sats),
            "total_spent_btc": sats_to_btc(spent_sats),
            "tx_count": tx_count,
            "utxo_count": utxo_count,
            "pending": {"incoming_btc": pending_in, "outgoing_btc": pending_out},
            "timestamp": ts(),
            "source": "Blockstream Esplora",
        }
    return {"error": f"Address not found or invalid: {address}"}


async def handle_tx_lookup(args):
    """Transaction lookup: status, confirmations, inputs, outputs, fee."""
    txid = args.get("txid", "").strip().lower()
    if not txid or len(txid) != 64:
        return {"error": "txid required (64-char hex string)"}

    tx_data, tx_status = await asyncio.gather(
        get(f"{ESPLORA}/tx/{txid}"),
        get(f"{ESPLORA}/tx/{txid}/status"),
    )

    if isinstance(tx_data, dict) and "error" not in tx_data:
        status = tx_status if isinstance(tx_status, dict) else {}
        confirmed      = status.get("confirmed", False)
        block_height   = status.get("block_height")
        tip_height_raw = await get(f"{MEMPOOL}/blocks/tip/height")
        tip_height = int(tip_height_raw) if isinstance(tip_height_raw, (int,str)) and str(tip_height_raw).isdigit() else 0
        confirmations = (tip_height - block_height + 1) if confirmed and block_height else 0

        # Inputs / Outputs summary
        vin  = tx_data.get("vin", [])
        vout = tx_data.get("vout", [])
        total_out = sum(o.get("value", 0) for o in vout)
        fee_sats  = tx_data.get("fee", 0)
        vsize     = tx_data.get("vsize", 0)

        return {
            "txid": txid[:16] + "...",
            "confirmed": confirmed,
            "confirmations": confirmations,
            "block_height": block_height,
            "block_time": datetime.fromtimestamp(
                status.get("block_time", 0), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ") if status.get("block_time") else None,
            "inputs": len(vin),
            "outputs": len(vout),
            "total_output_btc": sats_to_btc(total_out),
            "fee_sats": fee_sats,
            "fee_btc": sats_to_btc(fee_sats),
            "fee_rate_sat_vb": round(fee_sats / vsize, 2) if vsize else None,
            "vsize_bytes": vsize,
            "rbf_enabled": any(i.get("sequence", 0xffffffff) < 0xffffffff - 1 for i in vin),
            "timestamp": ts(),
            "source": "Blockstream Esplora",
        }
    return {"error": f"Transaction not found: {txid}"}


async def handle_network_stats(args):
    """Bitcoin network health: node count, hashrate trend, difficulty, mining pools."""
    hashrate_1w, hashrate_1m, pools, nodes = await asyncio.gather(
        get(f"{MEMPOOL}/v1/mining/hashrate/1w"),
        get(f"{MEMPOOL}/v1/mining/hashrate/1m"),
        get(f"{MEMPOOL}/v1/mining/pools/1w"),
        get(f"{MEMPOOL}/v1/lightning/statistics/latest"),  # reuse, no direct node count API
    )

    hr_1w = hashrate_1w if isinstance(hashrate_1w, dict) else {}
    hr_1m = hashrate_1m if isinstance(hashrate_1m, dict) else {}

    current_hr = None
    if hr_1w.get("hashrates"):
        current_hr = hr_1w["hashrates"][-1].get("avgHashrate")

    # Pool distribution
    pool_dist = []
    if isinstance(pools, dict) and pools.get("pools"):
        for p in pools["pools"][:10]:
            pool_dist.append({
                "name": p.get("name"),
                "blocks_1w": p.get("blockCount"),
                "share_pct": round(p.get("blockCount", 0) / max(pools.get("blockCount", 1), 1) * 100, 1),
            })

    difficulty_adj = await get(f"{MEMPOOL}/v1/difficulty-adjustment")
    da = difficulty_adj if isinstance(difficulty_adj, dict) else {}

    return {
        "timestamp": ts(),
        "hashrate": {
            "current_eh_s": round(current_hr / 1e18, 2) if current_hr else None,
            "unit": "EH/s",
            "all_time_high_note": "~700+ EH/s ATH (2024)",
        },
        "difficulty": {
            "next_adjustment_pct": round(da.get("difficultyChange", 0), 2),
            "blocks_until_retarget": da.get("remainingBlocks"),
            "progress_pct": round(da.get("progressPercent", 0), 1),
        },
        "mining_pools_top10_1w": pool_dist,
        "total_blocks_1w": pools.get("blockCount") if isinstance(pools, dict) else None,
        "decentralization_note": (
            "Healthy" if pool_dist and pool_dist[0].get("share_pct", 100) < 30
            else "Concentrated — top pool > 30% of blocks"
        ),
        "source": "mempool.space",
    }


async def handle_lightning_stats(args):
    """Lightning Network statistics: capacity, nodes, channels, avg fee rates."""
    stats = await get(f"{MEMPOOL}/v1/lightning/statistics/latest")

    if isinstance(stats, dict) and "error" not in stats:
        latest = stats.get("latest") or stats
        return {
            "timestamp": ts(),
            "total_capacity_btc": round(latest.get("total_capacity", 0) / SATOSHI, 2),
            "total_channels": latest.get("channel_count"),
            "total_nodes": latest.get("node_count"),
            "avg_channel_capacity_btc": round(
                latest.get("avg_capacity", 0) / SATOSHI, 6
            ) if latest.get("avg_capacity") else None,
            "avg_fee_rate_ppm": latest.get("avg_fee_rate"),
            "avg_base_fee_msat": latest.get("avg_base_fee_mtokens"),
            "med_channel_capacity_btc": round(
                latest.get("med_capacity", 0) / SATOSHI, 6
            ) if latest.get("med_capacity") else None,
            "network_health": (
                "Growing" if latest.get("channel_count", 0) > 50000 else
                "Established" if latest.get("channel_count", 0) > 20000 else
                "Developing"
            ),
            "use_case": "Instant micropayments, streaming money, AI agent micropayments (x402)",
            "source": "mempool.space Lightning",
        }
    return {"error": "Lightning stats unavailable", "timestamp": ts()}


async def handle_inscription(args):
    """Ordinals inscription lookup and BRC-20 token stats."""
    inscription_id = args.get("inscription_id", "").strip()
    token          = args.get("brc20_token", "").strip().upper()

    if inscription_id:
        data = await get(f"{HIRO}/ordinals/v1/inscriptions/{inscription_id}")
        if isinstance(data, dict) and "error" not in data:
            return {
                "inscription_id": inscription_id[:20] + "...",
                "number": data.get("number"),
                "content_type": data.get("content_type"),
                "content_length": data.get("content_length"),
                "genesis_block": data.get("genesis_block_height"),
                "genesis_timestamp": data.get("genesis_timestamp"),
                "address": data.get("address"),
                "offset": data.get("offset"),
                "sat_ordinal": data.get("sat_ordinal"),
                "sat_rarity": data.get("sat_rarity"),
                "timestamp": ts(),
                "source": "Hiro Ordinals API",
            }
        return {"error": f"Inscription not found: {inscription_id}"}

    if token:
        data = await get(f"{HIRO}/ordinals/v1/brc-20/tokens/{token}")
        if isinstance(data, dict) and "error" not in data:
            tok = data.get("token", data)
            return {
                "ticker": token,
                "deploy_block": tok.get("genesis_block_height"),
                "max_supply": tok.get("max_supply"),
                "mint_limit": tok.get("mint_limit"),
                "decimals": tok.get("decimals"),
                "deploy_timestamp": tok.get("deploy_timestamp"),
                "minted_supply": data.get("supply", {}).get("minted_supply"),
                "holders": data.get("supply", {}).get("holders"),
                "timestamp": ts(),
                "source": "Hiro BRC-20 API",
            }
        return {"error": f"BRC-20 token not found: {token}"}

    # Default: Ordinals overview stats
    stats = await get(f"{HIRO}/ordinals/v1/stats/inscriptions")
    if isinstance(stats, dict):
        return {
            "total_inscriptions": stats.get("results", [{}])[0].get("inscription_count") if stats.get("results") else stats.get("inscription_count"),
            "note": "Provide inscription_id for lookup or brc20_token for token info",
            "example_inscription_id": "provide full inscription ID (txid + i0)",
            "example_brc20": "ORDI, SATS, RATS",
            "timestamp": ts(),
            "source": "Hiro Ordinals API",
        }
    return {"error": "Ordinals stats unavailable", "timestamp": ts()}


async def handle_whale_alert(args):
    """Detect large Bitcoin transactions in recent blocks."""
    min_btc    = float(args.get("min_btc", 100))
    block_count = min(int(args.get("blocks", 3)), 10)

    # Get recent blocks
    tip_hash_raw = await get(f"{MEMPOOL}/blocks/tip/hash")
    tip_height_raw = await get(f"{MEMPOOL}/blocks/tip/height")
    tip_height = int(tip_height_raw) if isinstance(tip_height_raw,(int,str)) and str(tip_height_raw).isdigit() else 0

    recent_blocks = await get(f"{MEMPOOL}/v1/blocks")
    if not isinstance(recent_blocks, list):
        return {"error": "Could not fetch recent blocks", "timestamp": ts()}

    whales = []
    blocks_scanned = 0

    for block in recent_blocks[:block_count]:
        block_hash = block.get("id")
        block_h    = block.get("height")
        if not block_hash:
            continue

        txids = await get(f"{MEMPOOL}/block/{block_hash}/txids")
        if not isinstance(txids, list):
            continue

        blocks_scanned += 1
        # Check first 100 TXs per block (coinbase + large ones tend to be early)
        for txid in txids[1:101]:  # skip coinbase (index 0)
            tx = await get(f"{ESPLORA}/tx/{txid}")
            if not isinstance(tx, dict):
                continue
            total_out = sum(o.get("value", 0) for o in tx.get("vout", []))
            btc_out = total_out / SATOSHI
            if btc_out >= min_btc:
                whales.append({
                    "txid": txid[:16] + "...",
                    "btc_amount": round(btc_out, 4),
                    "block_height": block_h,
                    "outputs": len(tx.get("vout", [])),
                    "fee_btc": round(tx.get("fee", 0) / SATOSHI, 8),
                })
            if len(whales) >= 20:
                break
        if len(whales) >= 20:
            break

    whales.sort(key=lambda x: x["btc_amount"], reverse=True)

    return {
        "threshold_btc": min_btc,
        "blocks_scanned": blocks_scanned,
        "tip_height": tip_height,
        "whale_transactions": whales,
        "whale_count": len(whales),
        "total_whale_btc": round(sum(w["btc_amount"] for w in whales), 2),
        "note": f"Scanning first 100 TXs per block for amounts >= {min_btc} BTC",
        "timestamp": ts(),
        "source": "mempool.space + Blockstream Esplora",
    }


def build_server():
    server = WhitelabelMCPServer(
        product_name=PRODUCT_NAME,
        product_slug="btcoracle",
        version=VERSION,
        port_mcp=PORT_MCP,
        port_health=PORT_HEALTH,
    )

    server.register_tool("btc_overview",
        "Bitcoin ecosystem overview: BTC price (USD/EUR), 24h/7d change, market cap, "
        "BTC dominance, circulating supply, block height, halving countdown.",
        {"type": "object", "properties": {}, "required": []},
        handle_overview)

    server.register_tool("btc_mempool",
        "Real-time Bitcoin mempool: pending TX count, mempool size, congestion level "
        "(low/moderate/high/severe), fee recommendations in sat/vB for 10min/30min/1hr/economy.",
        {"type": "object", "properties": {}, "required": []},
        handle_mempool)

    server.register_tool("btc_fees",
        "Bitcoin fee tracker with USD cost estimates. Returns sat/vB rates and USD costs "
        "for common TX types: P2WPKH (141 vB), legacy (225 vB), 2-in-2-out (208 vB), "
        "consolidation (600 vB). Fastest/half-hour/economy tiers.",
        {"type": "object", "properties": {}, "required": []},
        handle_fees)

    server.register_tool("btc_block_stats",
        "Latest Bitcoin block details, hashrate (EH/s), difficulty adjustment progress "
        "and estimated % change, halving countdown (blocks + days remaining, current/next reward).",
        {"type": "object", "properties": {}, "required": []},
        handle_block_stats)

    server.register_tool("btc_address_check",
        "Bitcoin address intelligence: balance (BTC + sats), total received/spent, "
        "TX count, UTXO count, pending mempool amounts, address type detection "
        "(P2WPKH/P2WSH/P2SH/P2PKH).",
        {"type": "object",
         "properties": {"address": {"type": "string",
             "description": "Bitcoin address (1..., 3..., bc1q..., bc1p...)"}},
         "required": ["address"]},
        handle_address_check)

    server.register_tool("btc_tx_lookup",
        "Bitcoin transaction lookup: confirmation status, block height, confirmations, "
        "input/output count, total output BTC, fee (sats + BTC + sat/vB rate), "
        "RBF flag, block timestamp.",
        {"type": "object",
         "properties": {"txid": {"type": "string",
             "description": "64-character transaction ID (hex)"}},
         "required": ["txid"]},
        handle_tx_lookup)

    server.register_tool("btc_network_stats",
        "Bitcoin network health: current hashrate (EH/s), difficulty adjustment "
        "(% change + blocks remaining), top 10 mining pools by 1-week block share, "
        "decentralization assessment.",
        {"type": "object", "properties": {}, "required": []},
        handle_network_stats)

    server.register_tool("btc_lightning_stats",
        "Lightning Network statistics: total capacity (BTC), channel count, node count, "
        "average/median channel capacity, average fee rate (ppm), base fee (msat), "
        "network health assessment.",
        {"type": "object", "properties": {}, "required": []},
        handle_lightning_stats)

    server.register_tool("btc_inscription",
        "Ordinals inscription lookup and BRC-20 token stats. "
        "Provide inscription_id for a specific inscription, brc20_token (e.g. ORDI, SATS) "
        "for token supply/holders, or call empty for total inscription count.",
        {"type": "object",
         "properties": {
             "inscription_id": {"type": "string",
                 "description": "Ordinals inscription ID"},
             "brc20_token": {"type": "string",
                 "description": "BRC-20 ticker (e.g. ORDI, SATS, RATS)"},
         }, "required": []},
        handle_inscription)

    server.register_tool("btc_whale_alert",
        "Detect large Bitcoin transactions in recent blocks. "
        "Scans last N blocks (default 3, max 10) for TXs >= threshold BTC (default 100 BTC). "
        "Returns sorted list with txid, amount, block height, fee.",
        {"type": "object",
         "properties": {
             "min_btc": {"type": "number",
                 "description": "Minimum BTC amount to flag (default 100)", "default": 100},
             "blocks": {"type": "integer",
                 "description": "Number of recent blocks to scan (default 3, max 10)",
                 "default": 3},
         }, "required": []},
        handle_whale_alert)

    return server


if __name__ == "__main__":
    srv = build_server()
    srv.run()
