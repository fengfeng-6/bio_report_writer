FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 ecoagent
WORKDIR /app

COPY src ./src

USER ecoagent
EXPOSE 8000

ENV PYTHONPATH=/app/src
ENTRYPOINT ["python", "-m", "eco_report_agent.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
