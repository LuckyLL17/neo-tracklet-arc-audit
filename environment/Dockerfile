FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
WORKDIR /app
RUN python3 -m pip install --no-cache-dir "pytest==8.3.5"
COPY neo_audit.py /app/neo_audit.py
# Keep Harbor's task container alive; the agent invokes /app/neo_audit.py explicitly.
CMD ["sleep", "infinity"]
