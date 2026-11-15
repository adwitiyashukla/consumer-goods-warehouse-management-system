FROM ghcr.io/adwitiyashukla/consumer-goods-warehouse-management-system:latest
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=755 start.sh /start.sh
USER wms
ENV DATABASE_URL=postgresql://wms@127.0.0.1:5432/wms
EXPOSE 8000
CMD ["/start.sh"]
