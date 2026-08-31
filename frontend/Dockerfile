FROM node:22-alpine AS build
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
RUN npm run build
FROM nginx:1.27-alpine
# Copy the SPA entry point and its hashed assets explicitly.  This both prevents
# Docker directory-copy ambiguity and makes a missing Vite build fail at build time.
COPY --from=build /app/dist/index.html /usr/share/nginx/html/index.html
COPY --from=build /app/dist/assets/ /usr/share/nginx/html/assets/
COPY nginx.conf /etc/nginx/conf.d/default.conf
