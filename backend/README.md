# SINKA QUANT backend — experimental paper engine

This is a **separate backend prototype**, not deployed and not wired to the public GitHub Pages frontend. It does not use private OKX credentials and cannot place real orders.

## Local start

Create a long random `PAPER_API_TOKEN` in your own environment; do not commit it.

```sh
cd backend
export PAPER_API_TOKEN="replace-with-a-long-random-secret"
docker compose up --build -d
curl http://127.0.0.1:8000/health
curl -H "Authorization: Bearer $PAPER_API_TOKEN" http://127.0.0.1:8000/api/state
```

The SQLite database persists in the Docker volume. The API binds only to localhost. To connect a frontend remotely, deploy behind HTTPS and authenticated reverse proxy, with strict CORS configuration and proper secret management.

## Limitations

- One simulated position; manual API entry/exit only.
- Polls public OKX ticker every 5 seconds for stop triggers. Stops may execute late after price gaps, downtime, outages, or failed requests. **Not an exchange-native stop**.
- Simulated risk sizing and fixed fee/slippage assumptions; no contract lot rounding, funding, liquidation, leverage tiers, or complete margin model.
- No SNR auto-entry, no real trades, no backfill after downtime, no multi-worker coordination. Run one worker only.
- This is an implementation starting point, not a production trading service.
