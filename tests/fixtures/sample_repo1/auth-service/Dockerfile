# Build context is auth-service/ => this Dockerfile binds the code at this path to
# the image `acme/auth-service`, which the k8s Deployment runs. That path<->image<->
# workload join is the "verified" anchor.
FROM node:20-slim
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY src ./src
EXPOSE 8080
CMD ["npm", "start"]
