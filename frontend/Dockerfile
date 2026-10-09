# Node 24 = the version dev, CI, and the test suite run on (Node 20 is EOL
# since 2026-04 and lacks worker_threads.markAsUncloneable, which undici 8
# requires — the frontend Dockerfile doesn't run tests, but staying on one
# Node version everywhere avoids a second set of surprises).
FROM node:24-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
CMD ["npm", "run", "start"]
