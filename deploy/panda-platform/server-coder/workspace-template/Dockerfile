FROM node:22-bookworm@sha256:7702d8aa4d5910871692af4480eb3ba86f23047a6417a44d857d06c95d060d3f AS node
FROM ghcr.io/coder/coder:v2.38.6 AS coder-cli
FROM codercom/example-base@sha256:0850a3f07a059863c8d94c08d27f0b904490ad216e4d6f954ef4ac874bda6b46
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=coder-cli /opt/coder /usr/local/bin/coder
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
USER coder
