FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home papertrail
USER papertrail
ENV PAPERTRAIL_HOST=0.0.0.0 PAPERTRAIL_PORT=8000
EXPOSE 8000
CMD ["python", "-m", "app"]
