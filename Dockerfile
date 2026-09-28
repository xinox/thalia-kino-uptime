FROM python:3.12-alpine

WORKDIR /app
COPY . .

# Dokploy führt den eigentlichen Check als Schedule Job im laufenden Container aus.
CMD ["tail", "-f", "/dev/null"]
