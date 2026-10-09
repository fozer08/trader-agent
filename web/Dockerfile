FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY public/ public/
COPY src/ src/
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY --from=builder /app/dist/ dist/
COPY server.mjs ./
COPY config.mjs ./
ENV NODE_ENV=production
USER node
CMD ["node", "server.mjs"]
