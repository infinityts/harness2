FROM docker.io/nousresearch/hermes-agent:latest

USER root
RUN mkdir -p /opt/harness-worker/workspaces \
 && chown -R 10000:10000 /opt/harness-worker

COPY app/worker/hermes_supervisor.py /opt/harness-worker/hermes_supervisor.py
COPY app/client/harness_mcp_bridge.py /opt/harness-worker/harness_mcp_bridge.py
RUN chown -R 10000:10000 /opt/harness-worker

USER 10000:10000
ENV HERMES_HOME=/opt/data \
    HOME=/opt/data \
    PYTHONUNBUFFERED=1 \
    BRIDGE_PATH=/opt/harness-worker/harness_mcp_bridge.py \
    WORKSPACE_ROOT=/opt/harness-worker/workspaces

ENTRYPOINT ["/opt/hermes/.venv/bin/python", "/opt/harness-worker/hermes_supervisor.py"]
