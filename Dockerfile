FROM scan-agent-base:ubuntu24
COPY submission/ /submission/
RUN chmod +x /submission/agent_system
ENTRYPOINT ["/submission/agent_system"]
