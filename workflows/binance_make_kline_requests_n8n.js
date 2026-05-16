/**
 * n8n Code node: build Binance kline HTTP requests.
 *
 * Code node mode: Run Once for All Items
 *
 * Next node:
 *   HTTP Request
 *   Method: GET
 *   URL: ={{$json.url}}
 *   Response format: JSON
 *
 * Your n8n version may split Binance's top-level array response into 300
 * items per symbol. That is okay: the paper-trader Code node regroups the
 * 3000 rows back into the original 10 symbols.
 *
 * Binance docs:
 *   GET /api/v3/klines
 *   Required params: symbol, interval
 *   limit max: 1000
 */

const SYMBOLS = [
  "ADAUSDT",
  "AVAXUSDT",
  "BNBUSDT",
  "BTCUSDT",
  "DOGEUSDT",
  "DOTUSDT",
  "ETHUSDT",
  "LINKUSDT",
  "SOLUSDT",
  "XRPUSDT",
];

const BASE_URL = "https://data-api.binance.vision/api/v3/klines";
const INTERVAL = "4h";
const LIMIT = 300;

return SYMBOLS.map((symbol) => ({
  json: {
    symbol,
    interval: INTERVAL,
    limit: LIMIT,
    url: `${BASE_URL}?symbol=${symbol}&interval=${INTERVAL}&limit=${LIMIT}`,
  },
}));
