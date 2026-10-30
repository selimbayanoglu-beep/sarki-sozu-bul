FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PORT=8891 \
    HTTPS_PORT=8892

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    flac \
    openssl \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Mobil tarayıcılarda sertifika uyarısız HTTPS mikrofon için cloudflared kurulumu (mimariye göre otomatik)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ] || [ "$ARCH" = "arm64" ]; then \
      curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}" -o /usr/local/bin/cloudflared && \
      chmod +x /usr/local/bin/cloudflared || true; \
    fi

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/
RUN chmod +x /app/entrypoint.sh

EXPOSE 8891 8892

CMD ["/app/entrypoint.sh"]
