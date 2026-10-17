FROM node:22-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app
COPY strategy/snr-engine.mjs strategy/public-market.mjs strategy/paper-runner.mjs strategy/account-checkpoint.mjs ./strategy/
COPY worker/paper-store.mjs worker/paper-worker.mjs worker/container-smoke.mjs ./worker/
RUN mkdir /data && chown node:node /data
USER node
STOPSIGNAL SIGTERM
ENTRYPOINT ["node", "worker/paper-worker.mjs", "--db", "/data/paper.sqlite"]
CMD ["--symbol", "BTC-USDT-SWAP"]
