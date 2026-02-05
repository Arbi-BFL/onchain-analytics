#!/usr/bin/env python3
"""
Onchain Analytics Dashboard for Arbi
Tracks wallet activity on Base and Solana using Alchemy API
"""

from flask import Flask, render_template, jsonify, send_from_directory, request
from flask_cors import CORS
import os
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from threading import Thread
import logging

app = Flask(__name__, static_folder='public', template_folder='public')
CORS(app)

# Configuration
ALCHEMY_API_KEY = os.getenv('ALCHEMY_API_KEY', '')
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK', '')
BASE_WALLET = '0x75f39d9Bff76d376F3960028d98F324aAbB6c5e6'
SOLANA_WALLET = 'FeB1jqjCFKyQ2vVTPLgYmZu1yLvBWhsGoudP46fhhF8z'

# Alchemy endpoints
ALCHEMY_BASE_URL = f'https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}'
ALCHEMY_SOLANA_URL = f'https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}'

# Price cache (refresh every 5 minutes)
price_cache = {}
price_cache_time = 0

def get_token_prices():
    """Fetch current ETH and SOL prices from CoinGecko"""
    global price_cache, price_cache_time
    
    # Use cache if less than 5 minutes old
    if time.time() - price_cache_time < 300 and price_cache:
        return price_cache
    
    try:
        response = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={
                'ids': 'ethereum,solana',
                'vs_currencies': 'usd'
            },
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            price_cache = {
                'ETH': data.get('ethereum', {}).get('usd', 0),
                'SOL': data.get('solana', {}).get('usd', 0)
            }
            price_cache_time = time.time()
            logger.info(f"Updated prices: ETH=${price_cache['ETH']}, SOL=${price_cache['SOL']}")
            return price_cache
    except Exception as e:
        logger.error(f"Error fetching prices: {e}")
    
    # Return last known prices or zero
    return price_cache if price_cache else {'ETH': 0, 'SOL': 0}

def get_token_price_dexscreener(token_address, chain='base'):
    """Fetch token price from DexScreener"""
    try:
        response = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{token_address}',
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            pairs = data.get('pairs', [])
            
            # Find the pair with highest liquidity on the correct chain
            best_pair = None
            for pair in pairs:
                if pair.get('chainId', '').lower() == chain.lower():
                    if not best_pair or float(pair.get('liquidity', {}).get('usd', 0)) > float(best_pair.get('liquidity', {}).get('usd', 0)):
                        best_pair = pair
            
            if best_pair:
                price = float(best_pair.get('priceUsd', 0))
                logger.info(f"Found price for {token_address}: ${price}")
                return price
    except Exception as e:
        logger.error(f"Error fetching token price from DexScreener: {e}")
    
    return 0

# Database setup
DB_PATH = '/data/onchain.db'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    """Initialize SQLite database"""
    os.makedirs('/data', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Transactions table
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (hash TEXT PRIMARY KEY,
                  network TEXT,
                  from_address TEXT,
                  to_address TEXT,
                  value TEXT,
                  timestamp INTEGER,
                  block_number INTEGER,
                  status TEXT,
                  gas_used TEXT,
                  notified INTEGER DEFAULT 0,
                  token_symbol TEXT,
                  token_address TEXT,
                  usd_value TEXT)''')
    
    # Activity snapshots table
    c.execute('''CREATE TABLE IF NOT EXISTS activity_snapshots
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp INTEGER,
                  network TEXT,
                  transaction_count INTEGER,
                  total_value TEXT)''')
    
    conn.commit()
    conn.close()

def send_discord_notification(tx_data):
    """Send transaction notification to Discord"""
    if not DISCORD_WEBHOOK:
        return
    
    network_emoji = "🔵" if tx_data['network'] == 'base' else "🟣"
    value_eth = float(tx_data['value']) / 1e18 if tx_data['network'] == 'base' else float(tx_data['value']) / 1e9
    
    embed = {
        "title": f"{network_emoji} New Transaction on {tx_data['network'].upper()}",
        "color": 5814783 if tx_data['network'] == 'base' else 9055202,
        "fields": [
            {"name": "From", "value": f"`{tx_data['from_address'][:10]}...{tx_data['from_address'][-8:]}`", "inline": True},
            {"name": "To", "value": f"`{tx_data['to_address'][:10]}...{tx_data['to_address'][-8:]}`", "inline": True},
            {"name": "Value", "value": f"{value_eth:.6f} {'ETH' if tx_data['network'] == 'base' else 'SOL'}", "inline": True},
            {"name": "Hash", "value": f"[View on Explorer]({'https://basescan.org/tx/' if tx_data['network'] == 'base' else 'https://explorer.solana.com/tx/'}{tx_data['hash']})", "inline": False}
        ],
        "timestamp": datetime.utcfromtimestamp(tx_data['timestamp']).isoformat()
    }
    
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]})
        logger.info(f"Discord notification sent for tx: {tx_data['hash']}")
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")

def fetch_base_transactions():
    """Fetch recent transactions from Base using Alchemy"""
    try:
        all_txs = []
        
        # Get outgoing transactions (fromAddress)
        response_from = requests.post(
            ALCHEMY_BASE_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [{
                    "fromBlock": "0x0",
                    "toBlock": "latest",
                    "fromAddress": BASE_WALLET,
                    "category": ["external", "erc20", "erc721", "erc1155"],
                    "withMetadata": True,
                    "maxCount": "0x32"  # 50 transactions
                }]
            }
        )
        
        if response_from.status_code == 200:
            data = response_from.json()
            if 'result' in data and 'transfers' in data['result']:
                logger.info(f"Found {len(data['result']['transfers'])} outgoing Base transactions")
                all_txs.extend(data['result']['transfers'])
            elif 'error' in data:
                logger.error(f"Alchemy API error (outgoing): {data['error']}")
        
        # Get incoming transactions (toAddress)
        response_to = requests.post(
            ALCHEMY_BASE_URL,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "alchemy_getAssetTransfers",
                "params": [{
                    "fromBlock": "0x0",
                    "toBlock": "latest",
                    "toAddress": BASE_WALLET,
                    "category": ["external", "erc20", "erc721", "erc1155"],
                    "withMetadata": True,
                    "maxCount": "0x32"  # 50 transactions
                }]
            }
        )
        
        if response_to.status_code == 200:
            data = response_to.json()
            if 'result' in data and 'transfers' in data['result']:
                logger.info(f"Found {len(data['result']['transfers'])} incoming Base transactions")
                all_txs.extend(data['result']['transfers'])
            elif 'error' in data:
                logger.error(f"Alchemy API error (incoming): {data['error']}")
        
        # Remove duplicates by hash
        seen_hashes = set()
        unique_txs = []
        for tx in all_txs:
            tx_hash = tx.get('hash', '')
            if tx_hash and tx_hash not in seen_hashes:
                seen_hashes.add(tx_hash)
                unique_txs.append(tx)
        
        return unique_txs
    except Exception as e:
        logger.error(f"Error fetching Base transactions: {e}")
        return []

def fetch_solana_transactions():
    """Fetch recent transactions from Solana using Alchemy"""
    try:
        # First, get transaction signatures
        response = requests.post(
            ALCHEMY_SOLANA_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [SOLANA_WALLET, {"limit": 50}]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            if 'result' in data:
                signatures = data['result']
                
                # Fetch full transaction details for each signature
                detailed_txs = []
                for sig_info in signatures[:10]:  # Limit to 10 most recent
                    sig = sig_info.get('signature')
                    if not sig:
                        continue
                    
                    # Get full transaction details
                    tx_response = requests.post(
                        ALCHEMY_SOLANA_URL,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getTransaction",
                            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                        }
                    )
                    
                    if tx_response.status_code == 200:
                        tx_data = tx_response.json()
                        if 'result' in tx_data and tx_data['result']:
                            detailed_txs.append({
                                'signature': sig,
                                'blockTime': sig_info.get('blockTime'),
                                'slot': sig_info.get('slot'),
                                'err': sig_info.get('err'),
                                'details': tx_data['result']
                            })
                
                return detailed_txs
        
        return []
    except Exception as e:
        logger.error(f"Error fetching Solana transactions: {e}")
        return []

def process_base_transaction(tx):
    """Process and store Base transaction"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    tx_hash = tx.get('hash', '')
    
    # Check if already exists
    c.execute('SELECT hash FROM transactions WHERE hash = ?', (tx_hash,))
    if c.fetchone():
        conn.close()
        return False
    
    # Extract token information
    token_symbol = tx.get('asset', 'ETH')  # Default to ETH for native transfers
    token_address = ''
    if 'rawContract' in tx and 'address' in tx['rawContract']:
        token_address = tx['rawContract']['address']
    
    # Calculate USD value
    usd_value = '0'
    try:
        value_float = float(tx.get('value', 0))
        if value_float > 0:
            if token_symbol == 'ETH':
                prices = get_token_prices()
                eth_price = prices.get('ETH', 0)
                if eth_price > 0:
                    usd_value = str(value_float * eth_price)
            elif token_address:
                # Fetch token price from DexScreener
                token_price = get_token_price_dexscreener(token_address, 'base')
                if token_price > 0:
                    usd_value = str(value_float * token_price)
    except Exception as e:
        logger.error(f"Error calculating USD value: {e}")
    
    tx_data = {
        'hash': tx_hash,
        'network': 'base',
        'from_address': tx.get('from', ''),
        'to_address': tx.get('to', ''),
        'value': str(tx.get('value', 0)),
        'timestamp': int(datetime.fromisoformat(tx['metadata']['blockTimestamp'].replace('Z', '+00:00')).timestamp()) if 'metadata' in tx else int(time.time()),
        'block_number': int(tx['blockNum'], 16) if 'blockNum' in tx else 0,
        'status': 'confirmed',
        'gas_used': '0',
        'token_symbol': token_symbol,
        'token_address': token_address,
        'usd_value': usd_value
    }
    
    c.execute('''INSERT INTO transactions 
                 (hash, network, from_address, to_address, value, timestamp, block_number, status, gas_used, token_symbol, token_address, usd_value)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (tx_data['hash'], tx_data['network'], tx_data['from_address'], 
               tx_data['to_address'], tx_data['value'], tx_data['timestamp'],
               tx_data['block_number'], tx_data['status'], tx_data['gas_used'],
               tx_data['token_symbol'], tx_data['token_address'], tx_data['usd_value']))
    
    conn.commit()
    conn.close()
    
    # Send Discord notification
    send_discord_notification(tx_data)
    return True

def process_solana_transaction(tx):
    """Process and store Solana transaction"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    tx_hash = tx.get('signature', '')
    
    # Check if already exists
    c.execute('SELECT hash FROM transactions WHERE hash = ?', (tx_hash,))
    if c.fetchone():
        conn.close()
        return False
    
    # Parse transaction details
    details = tx.get('details', {})
    meta = details.get('meta', {})
    transaction = details.get('transaction', {})
    message = transaction.get('message', {})
    
    # Extract accounts and balance changes
    account_keys = message.get('accountKeys', [])
    pre_balances = meta.get('preBalances', [])
    post_balances = meta.get('postBalances', [])
    
    # Find our wallet index
    our_wallet_index = -1
    from_address = ''
    to_address = ''
    value = 0
    token_symbol = 'SOL'
    
    for idx, acc in enumerate(account_keys):
        if isinstance(acc, dict):
            acc_addr = acc.get('pubkey', '')
        else:
            acc_addr = acc
        
        if acc_addr == SOLANA_WALLET:
            our_wallet_index = idx
            break
    
    # Calculate SOL transfer amount
    if our_wallet_index >= 0 and our_wallet_index < len(pre_balances):
        pre_balance = pre_balances[our_wallet_index]
        post_balance = post_balances[our_wallet_index]
        balance_change = post_balance - pre_balance
        
        # Convert lamports to SOL
        value = abs(balance_change) / 1e9
        
        # Determine from/to based on whether we received or sent
        if balance_change > 0:
            # We received
            to_address = SOLANA_WALLET
            # Try to find sender (account with negative balance change)
            for idx, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                if post < pre and idx != our_wallet_index and idx < len(account_keys):
                    acc = account_keys[idx]
                    from_address = acc.get('pubkey', acc) if isinstance(acc, dict) else acc
                    break
        else:
            # We sent
            from_address = SOLANA_WALLET
            # Try to find recipient (account with positive balance change)
            for idx, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                if post > pre and idx != our_wallet_index and idx < len(account_keys):
                    acc = account_keys[idx]
                    to_address = acc.get('pubkey', acc) if isinstance(acc, dict) else acc
                    break
    
    # Calculate USD value
    usd_value = '0'
    try:
        if value > 0 and token_symbol == 'SOL':
            prices = get_token_prices()
            sol_price = prices.get('SOL', 0)
            if sol_price > 0:
                usd_value = str(value * sol_price)
    except:
        pass
    
    tx_data = {
        'hash': tx_hash,
        'network': 'solana',
        'from_address': from_address or SOLANA_WALLET,
        'to_address': to_address,
        'value': str(value),
        'timestamp': tx.get('blockTime', int(time.time())),
        'block_number': tx.get('slot', 0),
        'status': 'confirmed' if not tx.get('err') else 'failed',
        'gas_used': '0',
        'token_symbol': token_symbol,
        'token_address': '',
        'usd_value': usd_value
    }
    
    c.execute('''INSERT INTO transactions 
                 (hash, network, from_address, to_address, value, timestamp, block_number, status, gas_used, token_symbol, token_address, usd_value)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (tx_data['hash'], tx_data['network'], tx_data['from_address'], 
               tx_data['to_address'], tx_data['value'], tx_data['timestamp'],
               tx_data['block_number'], tx_data['status'], tx_data['gas_used'],
               tx_data['token_symbol'], tx_data['token_address'], tx_data['usd_value']))
    
    conn.commit()
    conn.close()
    
    # Send Discord notification
    send_discord_notification(tx_data)
    return True

def monitor_transactions():
    """Background thread to monitor transactions"""
    logger.info("Starting transaction monitoring...")
    
    while True:
        try:
            # Fetch and process Base transactions
            base_txs = fetch_base_transactions()
            for tx in base_txs:
                process_base_transaction(tx)
            
            # Fetch and process Solana transactions
            solana_txs = fetch_solana_transactions()
            for tx in solana_txs:
                process_solana_transaction(tx)
            
            # Take activity snapshot
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            now = int(time.time())
            
            # Base activity
            c.execute('SELECT COUNT(*), SUM(CAST(value AS REAL)) FROM transactions WHERE network = ?', ('base',))
            base_count, base_value = c.fetchone()
            c.execute('INSERT INTO activity_snapshots (timestamp, network, transaction_count, total_value) VALUES (?, ?, ?, ?)',
                     (now, 'base', base_count or 0, str(base_value or 0)))
            
            # Solana activity
            c.execute('SELECT COUNT(*), SUM(CAST(value AS REAL)) FROM transactions WHERE network = ?', ('solana',))
            sol_count, sol_value = c.fetchone()
            c.execute('INSERT INTO activity_snapshots (timestamp, network, transaction_count, total_value) VALUES (?, ?, ?, ?)',
                     (now, 'solana', sol_count or 0, str(sol_value or 0)))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Monitoring cycle complete. Base: {len(base_txs)} txs, Solana: {len(solana_txs)} txs")
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
        
        time.sleep(300)  # Check every 5 minutes

# API Routes
@app.route('/api/stats')
def api_stats():
    """Get overall statistics"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Total transactions by network
    c.execute('SELECT network, COUNT(*) FROM transactions GROUP BY network')
    network_counts = dict(c.fetchall())
    
    # Recent transactions (last 24h)
    yesterday = int(time.time()) - 86400
    c.execute('SELECT COUNT(*) FROM transactions WHERE timestamp > ?', (yesterday,))
    recent_count = c.fetchone()[0]
    
    # Total value moved (Base only, in wei)
    c.execute('SELECT SUM(CAST(value AS REAL)) FROM transactions WHERE network = ?', ('base',))
    total_value = c.fetchone()[0] or 0
    
    conn.close()
    
    return jsonify({
        'total_transactions': sum(network_counts.values()),
        'base_transactions': network_counts.get('base', 0),
        'solana_transactions': network_counts.get('solana', 0),
        'recent_24h': recent_count,
        'total_value_eth': total_value / 1e18 if total_value else 0
    })

@app.route('/api/transactions')
def api_transactions():
    """Get recent transactions"""
    limit = int(request.args.get('limit', 20))
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute('''SELECT * FROM transactions 
                 ORDER BY timestamp DESC 
                 LIMIT ?''', (limit,))
    
    transactions = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(transactions)

@app.route('/api/activity')
def api_activity():
    """Get activity snapshots for charts"""
    hours = int(request.args.get('hours', 24))
    since = int(time.time()) - (hours * 3600)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute('''SELECT * FROM activity_snapshots 
                 WHERE timestamp > ?
                 ORDER BY timestamp ASC''', (since,))
    
    snapshots = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(snapshots)

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': int(time.time()),
        'alchemy_configured': bool(ALCHEMY_API_KEY),
        'discord_configured': bool(DISCORD_WEBHOOK)
    })

@app.route('/')
def index():
    """Serve the dashboard"""
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    """Serve static files"""
    return send_from_directory('public', path)

if __name__ == '__main__':
    init_db()
    
    # Start monitoring thread
    monitor_thread = Thread(target=monitor_transactions, daemon=True)
    monitor_thread.start()
    
    # Start Flask app
    app.run(host='0.0.0.0', port=8000, debug=False)
