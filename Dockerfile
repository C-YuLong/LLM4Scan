FROM scan-agent-base:ubuntu24
RUN apt-get update && apt-get install -y --no-install-recommends make && rm -rf /var/lib/apt/lists/*
COPY submission/ /submission/
COPY knowledge/ /knowledge/
RUN chmod +x /submission/agent_system
ENTRYPOINT ["/submission/agent_system"]
