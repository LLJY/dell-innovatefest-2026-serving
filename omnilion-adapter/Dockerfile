FROM oven/bun:1.4.0

WORKDIR /app
COPY --chown=bun:bun src ./src

USER bun

EXPOSE 8790
CMD ["bun", "run", "src/main.ts"]
