FROM ghcr.io/adwitiyashukla/dockhand:latest
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=755 start.sh /start.sh
USER dockhand
ENV DATABASE_URL=postgresql://dockhand@127.0.0.1:5432/dockhand
EXPOSE 8000
CMD ["/start.sh"]
