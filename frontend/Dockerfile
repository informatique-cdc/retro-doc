# Stage 1: Build the Angular application
ARG NODE_IMAGE
ARG NGINX_IMAGE

FROM ${NODE_IMAGE} AS build

WORKDIR /app

# Copy package files and install dependencies
COPY package.json package-lock.json* ./
RUN npm ci

# Copy source code and build the application
COPY . .
RUN npm run build

# Stage 2: Serve with Nginx
FROM ${NGINX_IMAGE}

# API backend URL (set via build arg or overridden at runtime)
ARG API_URL=http://localhost:8080
ENV API_URL=${API_URL}

# Copy nginx configuration template (envsubst runs at container startup)
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Copy built Angular app from build stage
COPY --from=build /app/dist/retro-doc-frontend/browser /usr/share/nginx/html

EXPOSE 80
