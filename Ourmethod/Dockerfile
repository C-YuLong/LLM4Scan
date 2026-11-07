FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS opensta-package
RUN apt-get update -qq && apt-get install -y --no-install-recommends opensta=0~20191111gitc018cb2+dfsg-1
FROM scan-agent-base:ubuntu24
COPY --from=opensta-package /usr/bin/sta /usr/local/bin/sta
COPY --from=opensta-package /usr/share/doc/opensta /usr/share/doc/opensta
RUN apt-get update -qq && apt-get install -y --no-install-recommends libtcl8.6 libreadline8t64 libtinfo6 zlib1g libstdc++6 && rm -rf /var/lib/apt/lists/*
RUN sta -version | grep -Fx '2.0.17' && ! ldd /usr/local/bin/sta | grep 'not found'
RUN printf 'puts "OPENSTA_SMOKE [llength [get_libs *]]"\nexit\n' | sta -no_init | grep 'OPENSTA_SMOKE 0'
COPY Ourmethod/agent_system Ourmethod/scan_agent.py Ourmethod/repair_task.py \
     Ourmethod/scan_runner.py Ourmethod/llm_client.py Ourmethod/model_contract.py \
     Ourmethod/telemetry.py Ourmethod/acceptance.py Ourmethod/commands.md \
     Ourmethod/stage_artifacts.py Ourmethod/configuration.py Ourmethod/deepseek_strict.py Ourmethod/components.py Ourmethod/offline_verify.py /submission/
COPY Ourmethod/generator/*.py /submission/generator/
COPY Ourmethod/verifier/__init__.py Ourmethod/verifier/acceptance.py \
     Ourmethod/verifier/diagnosis.py Ourmethod/verifier/task2.py \
     Ourmethod/verifier/tools.py Ourmethod/verifier/model_tools.py Ourmethod/verifier/checklist.py Ourmethod/verifier/ff_facts.py Ourmethod/verifier/gaps.py Ourmethod/verifier/result_view.py Ourmethod/verifier/text_view.py Ourmethod/verifier/log_events.py Ourmethod/verifier/context.py Ourmethod/verifier/tool_schema.py Ourmethod/verifier/result.py Ourmethod/verifier/direct_agent.py Ourmethod/verifier/guided_state.py Ourmethod/verifier/guided_agent.py Ourmethod/verifier/diagnostics.py /submission/verifier/
COPY Ourmethod/verifier/source_reader.py Ourmethod/verifier/source-reader-requirements.txt /submission/verifier/
COPY Ourmethod/verifier/opensta.py Ourmethod/verifier/batch_connections.py Ourmethod/verifier/instance_identity.py Ourmethod/verifier/trace_connection.py /submission/verifier/
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /submission/verifier/source-reader-requirements.txt
COPY Ourmethod/verifier/structural/ /submission/verifier/structural/
COPY Ourmethod/config.toml Ourmethod/config.direct_agent.toml /submission/
RUN python3 -c "import sys; assert sys.version_info >= (3,11)" && command -v yosys && chmod +x /submission/agent_system
ENTRYPOINT ["/submission/agent_system"]
