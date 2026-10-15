FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm install --no-save tsx@4.23.15
COPY src ./src
COPY tsconfig.json ./
ENV PORT=8787
EXPOSE 8787
CMD ["npx", "tsx", "src/server.ts"]
