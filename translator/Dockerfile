FROM oven/bun:1.4.0

WORKDIR /app
COPY --chown=bun:bun package.json bun.lock ./
RUN bun install --frozen-lockfile --production
COPY --chown=bun:bun src ./src

USER bun

EXPOSE 8787
CMD ["bun", "run", "src/main.ts"]
