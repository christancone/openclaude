# Sparengine asset-graph server.
#
# Multi-stage:
#   1. build:    Node 22 + Bun → builds OpenClaude's dist/cli.mjs (the agent CLI)
#   2. runtime:  slim image with Node, Python 3.11, ripgrep, git
#                serves sparengine-export/server.mjs on :2001
#                spawns the agent (Claude Code or OpenClaude) as a child process

# ---------- build stage ----------
FROM node:22-slim AS build

RUN npm install -g bun@1.3.11
WORKDIR /app

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY src/        src/
COPY scripts/    scripts/
COPY bin/        bin/
COPY tsconfig.json ./

RUN bun run build

# Prune devDependencies for the runtime copy.
RUN rm -rf node_modules && bun install --frozen-lockfile --production


# ---------- runtime stage ----------
FROM node:22-slim

# Python (for the agent's pipeline) + git/ripgrep (for the agent's tools)
# + tini for proper signal handling so SIGTERM kills child processes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        git ripgrep ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

# Install Claude Code CLI globally (used when AGENT_CLI=claude, default).
# Drop this line if you only ever run AGENT_CLI=openclaude.
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# OpenClaude bundle (used when AGENT_CLI=openclaude).
COPY --from=build /app/dist/cli.mjs    dist/cli.mjs
COPY --from=build /app/bin/            bin/
COPY --from=build /app/node_modules/   node_modules/
COPY --from=build /app/package.json    package.json

# Sparengine server + phase brief + visualisation template.
COPY sparengine-export/ sparengine-export/

# Where each asset's artefacts live. Mount this as a named volume so
# CSVs and graphs survive container restarts.
RUN mkdir -p /app/csvs && chown -R node:node /app

# Pre-create the agent's home cache directory with `node` ownership.
# Required because:
#   - Claude Code CLI writes to /home/node/.claude/session-env on every Bash tool call.
#   - OpenClaude (forked from Claude Code) uses the same path.
# When a named volume is mounted on top of an EXISTING dir, Docker copies the
# dir's permissions onto the volume — so future runs that DO mount a volume
# (claude-auth for Pro/Max OAuth) inherit `node:node` instead of `root:root`.
RUN mkdir -p /home/node/.claude /home/node/.claude/session-env \
    && chown -R node:node /home/node

ENV NODE_ENV=production \
    PORT=2001 \
    AGENT_CLI=openclaude \
    HOME=/home/node

EXPOSE 2001

USER node

# tini reaps zombies and propagates SIGTERM to the agent child processes
# the server spawns. Without it, Ctrl-C / docker stop leaks Python procs.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "sparengine-export/server.mjs"]
