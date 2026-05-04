FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN printf "nameserver 8.8.8.8\nnameserver 8.8.4.4\n" > /etc/resolv.conf && npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
